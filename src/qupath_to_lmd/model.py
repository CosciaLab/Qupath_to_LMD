"""Canonical data model that both workflows produce and the export path consumes.

See ROADMAP.md. The important idea is `group_key`: the unit that maps to exactly one
well. The legacy workflow sets it to the class name, exploded classes set it per shape,
and the cell workflow will set it to class + replicate. Well assignment, plate QC and
export therefore need only one rule.
"""

from dataclasses import dataclass, field
from typing import Any

import geopandas
import numpy
import pandas
from loguru import logger

# Columns the export path relies on. Everything else in the frame is passthrough
# from QuPath and is carried along for the re-importable GeoJSON.
SHAPE_ID = "shape_id"
CLASS_NAME = "classification_name"
REPLICATE = "replicate"
GROUP_KEY = "group_key"
WELL = "well"

CANONICAL_COLUMNS = (SHAPE_ID, CLASS_NAME, REPLICATE, GROUP_KEY, WELL, "geometry")

# What a plan needs to carry: the canonical columns plus the fields QuPath needs back for the
# re-importable GeoJSON. Copying only these keeps a plan affordable on large files — the plan
# builders copy the frame, and at a million shapes a full copy costs 99 MB
# (`decisions.md` 051).
PLAN_SOURCE_COLUMNS = ("id", "objectType", "classification", CLASS_NAME, "geometry")


def _plan_frame(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """A copy holding only the columns a plan and its exports need."""
    keep = [column for column in PLAN_SOURCE_COLUMNS if column in gdf.columns]
    extra = [c for c in gdf.columns if c not in keep and c == "original_classification_name"]
    return gdf[keep + extra].copy()


@dataclass
class CollectionPlan:
    """A fully-decided collection: which shape goes into which well, and how.

    `shapes` holds every candidate shape. Rows with no `well` are not cut — they are kept
    so the app can tell the user what was left out instead of silently dropping it.
    """

    shapes: geopandas.GeoDataFrame
    calibration_names: list[str]
    calibration_array: numpy.ndarray
    workflow: str
    source_file: str | None = None
    session_id: str | None = None
    pixel_size_um: float | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> geopandas.GeoDataFrame:
        """Shapes that will be cut, in the order they were loaded."""
        return self.shapes[self.shapes[WELL].notna()]

    @property
    def skipped(self) -> geopandas.GeoDataFrame:
        """Shapes with no well assigned; reported to the user, never cut."""
        return self.shapes[self.shapes[WELL].isna()]

    @property
    def unplaced(self) -> geopandas.GeoDataFrame:
        """Shapes that belong to a group but whose group got no well.

        Distinct from `skipped`: in the cell workflow most shapes are simply not selected,
        which is the point of the workflow. These are shapes the user *asked* to collect and
        that will not be cut anyway, because their group ran out of wells.
        """
        if GROUP_KEY not in self.shapes.columns:
            return self.shapes.iloc[:0]
        return self.shapes[self.shapes[GROUP_KEY].notna() & self.shapes[WELL].isna()]

    @property
    def not_selected(self) -> geopandas.GeoDataFrame:
        """Shapes deliberately left out — no group was ever assigned to them."""
        if GROUP_KEY not in self.shapes.columns:
            return self.shapes.iloc[:0]
        return self.shapes[self.shapes[GROUP_KEY].isna()]

    @property
    def wells_used(self) -> list[str]:
        """Wells that will receive tissue, sorted."""
        return sorted(set(self.selected[WELL]))

    def provenance(self) -> dict[str, Any]:
        """Everything that determined the output, for the download bundle."""
        return {
            "workflow": self.workflow,
            "source_file": self.source_file,
            "session_id": self.session_id,
            "pixel_size_um": self.pixel_size_um,
            "calibration_points": {
                name: [float(x), float(y)]
                for name, (x, y) in zip(self.calibration_names, self.calibration_array, strict=True)
            },
            "parameters": self.params,
            "shapes_total": int(len(self.shapes)),
            "shapes_selected": int(len(self.selected)),
            "shapes_skipped": int(len(self.skipped)),
            "groups": int(self.selected[GROUP_KEY].nunique()),
            "wells_used": self.wells_used,
        }


def plan_from_class_wells(
    gdf: geopandas.GeoDataFrame,
    samples_and_wells: dict[str, str],
    calibration_names: list[str],
    calibration_array: numpy.ndarray,
    *,
    source_file: str | None = None,
    session_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> CollectionPlan:
    """Build a plan for the legacy workflow: one class is one sample is one well.

    Exploded classes need no special handling here — explosion already rewrote
    `classification_name` per shape, so each exploded shape becomes its own group.
    """
    shapes = _plan_frame(gdf)
    shapes[SHAPE_ID] = shapes["id"] if "id" in shapes.columns else shapes.index.astype(str)
    shapes[REPLICATE] = None
    # Only classes the user put in the scheme get a group. A class they left out was not asked
    # for, which is the same state as an unselected shape in the cell workflow, so both are
    # reported the same way.
    in_scheme = shapes[CLASS_NAME].isin(samples_and_wells)
    shapes[GROUP_KEY] = shapes[CLASS_NAME].where(in_scheme)
    shapes[WELL] = shapes[GROUP_KEY].map(samples_and_wells)

    return CollectionPlan(
        shapes=shapes,
        calibration_names=list(calibration_names),
        calibration_array=calibration_array,
        workflow="legacy",
        source_file=source_file,
        session_id=session_id,
        params=params or {},
    )


def plan_from_selection(
    gdf: geopandas.GeoDataFrame,
    replicate_of: pandas.Series,
    wells: list[str],
    calibration_names: list[str],
    calibration_array: numpy.ndarray,
    *,
    samples_and_wells: dict[str, str] | None = None,
    source_file: str | None = None,
    session_id: str | None = None,
    pixel_size_um: float | None = None,
    params: dict[str, Any] | None = None,
    workflow: str = "cells",
) -> tuple[CollectionPlan, dict[str, str]]:
    """Build a plan for the cell workflow: one class-and-replicate per well.

    Also serves the regions workflow, which reaches the same shape of answer by a different
    route — one class-and-replicate per well, where a row is a region or a packed circle rather
    than a cell.

    Args:
        gdf: the QC'd shapes.
        replicate_of: replicate number per shape index, NA for shapes not selected.
        wells: usable wells, consumed in order — one per group. Ignored when
            `samples_and_wells` is given.
        samples_and_wells: an assignment already shown to the user. Passing it keeps the
            plate the user approved, including groups that ended up with no shapes.
        calibration_names: the three chosen point names.
        calibration_array: their coordinates.
        source_file: uploaded filename, for the bundle.
        session_id: for the log inside the bundle.
        pixel_size_um: recorded in provenance; may be None.
        params: everything else that determined the output.
        workflow: recorded in provenance, so a bundle says which route produced it.

    Returns:
        The plan, and the group-to-well mapping the export path also needs.
    """
    shapes = _plan_frame(gdf)
    shapes[SHAPE_ID] = shapes["id"] if "id" in shapes.columns else shapes.index.astype(str)
    shapes[REPLICATE] = replicate_of.reindex(shapes.index)

    selected = shapes[REPLICATE].notna()
    shapes[GROUP_KEY] = None
    shapes.loc[selected, GROUP_KEY] = (
        shapes.loc[selected, CLASS_NAME].astype(str)
        + "_r"
        + shapes.loc[selected, REPLICATE].astype(int).astype(str)
    )

    if samples_and_wells is None:
        # Groups are sorted so the same selection always lands in the same wells.
        groups = sorted(shapes.loc[selected, GROUP_KEY].unique())
        samples_and_wells = dict(zip(groups, wells, strict=False))
        if len(groups) > len(wells):
            logger.warning(f"{len(groups) - len(wells)} groups have no well and will not be cut")
    else:
        missing = sorted(set(shapes.loc[selected, GROUP_KEY]) - set(samples_and_wells))
        if missing:
            logger.warning(f"{len(missing)} selected groups have no well: {missing[:5]}")

    shapes[WELL] = shapes[GROUP_KEY].map(samples_and_wells)

    plan = CollectionPlan(
        shapes=shapes,
        calibration_names=list(calibration_names),
        calibration_array=calibration_array,
        workflow=workflow,
        source_file=source_file,
        session_id=session_id,
        pixel_size_um=pixel_size_um,
        params=params or {},
    )
    return plan, samples_and_wells
