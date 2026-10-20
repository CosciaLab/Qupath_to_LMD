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
    # Only classes the user put in the scheme get a group. A class they left out was not asked
    # for, which is the same state as an unselected shape in the cell workflow, so both are
    # reported the same way.
    in_scheme = gdf[CLASS_NAME].isin(samples_and_wells)
    return plan_from_groups(
        gdf,
        group_key=gdf[CLASS_NAME].where(in_scheme),
        replicate=None,
        samples_and_wells=samples_and_wells,
        calibration_names=calibration_names,
        calibration_array=calibration_array,
        workflow="legacy",
        source_file=source_file,
        session_id=session_id,
        params=params,
    )


def plan_from_groups(
    gdf: geopandas.GeoDataFrame,
    group_key: pandas.Series,
    replicate: pandas.Series | None,
    samples_and_wells: dict[str, str],
    calibration_names: list[str],
    calibration_array: numpy.ndarray,
    *,
    workflow: str,
    source_file: str | None = None,
    session_id: str | None = None,
    pixel_size_um: float | None = None,
    params: dict[str, Any] | None = None,
) -> CollectionPlan:
    """Build a plan from shapes that already know their group: every collection method ends here.

    Args:
        gdf: every candidate shape of one slide.
        group_key: the sample each shape belongs to, NA for shapes not collected.
        replicate: replicate number per shape, or None where the method has no replicates.
        samples_and_wells: sample to well, for the one plate this plan cuts into. A sample
            absent from it is left without a well and reported, never silently dropped.
        calibration_names: the three chosen point names.
        calibration_array: their coordinates.
        workflow: recorded in provenance, so a bundle says which route produced it.
        source_file: uploaded filename, for the bundle.
        session_id: for the log inside the bundle.
        pixel_size_um: recorded in provenance; may be None.
        params: everything else that determined the output.
    """
    shapes = _plan_frame(gdf)
    shapes[SHAPE_ID] = shapes["id"] if "id" in shapes.columns else shapes.index.astype(str)
    shapes[REPLICATE] = None if replicate is None else replicate.reindex(shapes.index)
    shapes[GROUP_KEY] = group_key.reindex(shapes.index)
    shapes[WELL] = shapes[GROUP_KEY].map(samples_and_wells)

    return CollectionPlan(
        shapes=shapes,
        calibration_names=list(calibration_names),
        calibration_array=calibration_array,
        workflow=workflow,
        source_file=source_file,
        session_id=session_id,
        pixel_size_um=pixel_size_um,
        params=params or {},
    )


def groups_from_replicates(gdf: geopandas.GeoDataFrame, replicate: pandas.Series) -> pandas.Series:
    """`class_r<replicate>` for every shape with a replicate, None for the rest."""
    replicate = replicate.reindex(gdf.index)
    selected = replicate.notna()
    groups = pandas.Series(None, index=gdf.index, dtype=object)
    groups[selected] = (
        gdf.loc[selected, CLASS_NAME].astype(str) + "_r" + replicate[selected].astype(int).astype(str)
    )
    return groups


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
    groups = groups_from_replicates(gdf, replicate_of)
    present = groups.dropna()

    if samples_and_wells is None:
        # Groups are sorted so the same selection always lands in the same wells.
        ordered = sorted(present.unique())
        samples_and_wells = dict(zip(ordered, wells, strict=False))
        if len(ordered) > len(wells):
            logger.warning(f"{len(ordered) - len(wells)} groups have no well and will not be cut")
    else:
        missing = sorted(set(present) - set(samples_and_wells))
        if missing:
            logger.warning(f"{len(missing)} selected groups have no well: {missing[:5]}")

    plan = plan_from_groups(
        gdf,
        group_key=groups,
        replicate=replicate_of,
        samples_and_wells=samples_and_wells,
        calibration_names=calibration_names,
        calibration_array=calibration_array,
        workflow=workflow,
        source_file=source_file,
        session_id=session_id,
        pixel_size_um=pixel_size_um,
        params=params,
    )
    return plan, samples_and_wells


@dataclass
class SampleSet:
    """What a collection method decided, for every slide: the seam between Samples and Plates.

    Every method — whole shapes, selected shapes, regions and circles — returns one of these and
    nothing downstream needs to know which method made it. The plate stage reads `samples`; the
    cut stage builds one plan per slide and plate from `shapes`.

    Attributes:
        workflow: recorded in provenance — `legacy`, `cells` or `regions`.
        shapes: per slide, every candidate shape with its `group_key` (the sample, NA when not
            collected) and `replicate` columns. Circles and regions are shapes like any other.
        samples: every sample the plate has to hold, including any that ended up empty, so an
            empty replicate keeps its well and the plate matches what the user asked for.
        pixel_sizes: per slide, for areas in the sample sheet.
        requested: per sample, the amount asked for in `unit`; absent where a method cuts
            everything it is given.
        unit: `shapes` or `µm²`.
        params: everything else that determined the samples, for provenance.
    """

    workflow: str
    shapes: dict[str, geopandas.GeoDataFrame]
    samples: list[str]
    pixel_sizes: dict[str, float | None] = field(default_factory=dict)
    requested: dict[str, float] = field(default_factory=dict)
    unit: str = "shapes"
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def slide_names(self) -> list[str]:
        """Slides in the order they were uploaded."""
        return list(self.shapes)

    def n_collected(self) -> int:
        """How many shapes are going to be cut, over every slide."""
        return int(sum(frame[GROUP_KEY].notna().sum() for frame in self.shapes.values()))

    def sheet(self) -> pandas.DataFrame:
        """The sample sheet: one row per sample, with what each slide gives it.

        Columns: sample, class, replicate, then per slide its shapes and — where the slide has a
        scale — its µm², then the totals (µm² only when every slide has a scale) and, where the
        method asks for an amount, the request.
        """
        sheet = pandas.DataFrame({"sample": self.samples})
        total_shapes = pandas.Series(0, index=sheet.index)
        total_area = pandas.Series(0.0, index=sheet.index)
        for name, frame in self.shapes.items():
            collected = frame[frame[GROUP_KEY].notna()]
            counts = collected.groupby(GROUP_KEY).size()
            sheet[f"{name} shapes"] = sheet["sample"].map(counts).fillna(0).astype(int)
            total_shapes += sheet[f"{name} shapes"]
            scale = self.pixel_sizes.get(name)
            if scale:
                areas = (collected.geometry.area * scale**2).groupby(collected[GROUP_KEY]).sum()
                sheet[f"{name} µm²"] = sheet["sample"].map(areas).fillna(0.0)
                total_area += sheet[f"{name} µm²"]
        if len(self.shapes) > 1:
            sheet["shapes"] = total_shapes
            # A total that silently leaves out a slide without a scale would understate the sample.
            if all(self.pixel_sizes.get(name) for name in self.shapes):
                sheet["µm²"] = total_area
        if self.requested:
            sheet[f"asked for ({self.unit})"] = sheet["sample"].map(self.requested)

        if sheet.empty:
            return sheet
        classes, replicates = [], []
        for sample in sheet["sample"]:
            head, separator, tail = str(sample).rpartition("_r")
            is_replicate = bool(separator) and tail.isdigit()
            classes.append(head if is_replicate else sample)
            replicates.append(int(tail) if is_replicate else None)
        sheet.insert(1, "class", classes)
        sheet.insert(2, "replicate", pandas.array(replicates, dtype="Int64"))
        if sheet["replicate"].isna().all():
            sheet = sheet.drop(columns=["replicate"])
        # Whole shapes: a sample *is* its class, so the column would only repeat the first one.
        if (sheet["class"] == sheet["sample"]).all():
            sheet = sheet.drop(columns=["class"])
        return sheet

    def plan(
        self,
        slide: str,
        samples_and_wells: dict[str, str],
        calibration_names: list[str],
        calibration_array: numpy.ndarray,
        *,
        source_file: str | None = None,
        session_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> CollectionPlan:
        """The plan for one slide into one plate."""
        frame = self.shapes[slide]
        replicate = frame[REPLICATE] if REPLICATE in frame.columns else None
        return plan_from_groups(
            frame,
            group_key=frame[GROUP_KEY],
            replicate=replicate,
            samples_and_wells=samples_and_wells,
            calibration_names=calibration_names,
            calibration_array=calibration_array,
            workflow=self.workflow,
            source_file=source_file,
            session_id=session_id,
            pixel_size_um=self.pixel_sizes.get(slide),
            params={**self.params, **(params or {})},
        )
