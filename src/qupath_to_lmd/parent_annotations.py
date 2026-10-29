"""Group shapes by the parent annotation they sit in: `gf` inside a `P1` parent annotation becomes `P1_gf`.

Several patients often share one image, and slides pool by class name, so without this `gf` from
every patient would land in one well. A parent annotation is a shape whose class names a group — a patient,
a core, a region. It is never cut; it only gives its class to the shapes inside it, so a lab can
annotate a handful of categories and still keep patients apart (`decisions.md` 091).
"""

import ast
from collections.abc import Collection
from dataclasses import dataclass, field

import geopandas
import numpy
import pandas
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME

PARENT_SEPARATOR = "_"
NO_PARENT = "no parent annotation"
SEVERAL_PARENTS = "several parent annotations"


def _empty_parents() -> geopandas.GeoDataFrame:
    return geopandas.GeoDataFrame({"label": pandas.Series(dtype=str)}, geometry=[])


def _empty_left_out() -> geopandas.GeoDataFrame:
    return geopandas.GeoDataFrame(
        {CLASS_NAME: pandas.Series(dtype=str), "reason": pandas.Series(dtype=str)}, geometry=[]
    )


@dataclass
class ParentReport:
    """What grouping one slide by its parent annotations did. Everything here is shown to the user."""

    parents: geopandas.GeoDataFrame = field(default_factory=_empty_parents)
    left_out: geopandas.GeoDataFrame = field(default_factory=_empty_left_out)
    n_grouped: int = 0
    repeated_parents: dict[str, int] = field(default_factory=dict)
    empty_parents: dict[str, int] = field(default_factory=dict)
    n_multipolygon_parents: int = 0
    combinations: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    @property
    def n_parents(self) -> int:
        """How many parent annotations the slide has, merged ones included."""
        return len(self.parents)


def grouped_name(parent: str, class_name: str) -> str:
    """The sample name of a class inside a parent annotation, parent annotation first: `P1_gf`."""
    return f"{parent}{PARENT_SEPARATOR}{class_name}"


def _grouped_classification(value, name: str) -> str:
    """The classification QuPath should reopen: one class, the grouped one.

    A multi-class structure carries `names`; left in place beside the new `name`, QuPath could
    reopen it as its old classes rather than the sample it was cut into.
    """
    as_dict = ast.literal_eval(value) if isinstance(value, str) else dict(value)
    as_dict.pop("names", None)
    as_dict["name"] = name
    return str(as_dict)


def group_by_parents(
    gdf: geopandas.GeoDataFrame,
    parent_classes: Collection[str],
    multipolygons: geopandas.GeoDataFrame | None = None,
) -> tuple[geopandas.GeoDataFrame, ParentReport]:
    """Rename every shape after the parent annotation it sits in, and take the parent annotations out.

    A shape belongs to the parent annotation containing its interior point, so a structure that pokes over
    the edge still counts. Parent annotations of one class may overlap — one patient, two pieces — but a
    shape inside parent annotations of two different classes is ambiguous and left out, as is a shape in no
    parent annotation: either way it would otherwise reach a sample of the wrong patient, or of none.

    Args:
        gdf: one slide's shapes, as `geojson.read_and_qc` returns them.
        parent_classes: the classes whose shapes are parent annotations. Empty returns `gdf` unchanged.
        multipolygons: the MultiPolygons reading set aside as uncuttable; those of a parent annotation
            class are parent annotations too, since a merged parent annotation is still an area.

    Returns:
        The grouped shapes — parent annotations and left-out shapes removed, original index kept — and
        the report.
    """
    if not parent_classes:
        return gdf, ParentReport()

    is_parent = gdf[CLASS_NAME].isin(parent_classes)
    sources = [gdf.loc[is_parent, [CLASS_NAME, "geometry"]]]
    n_multipolygon_parents = 0
    if multipolygons is not None and not multipolygons.empty:
        merged = multipolygons[multipolygons[CLASS_NAME].isin(parent_classes)]
        n_multipolygon_parents = len(merged)
        sources.append(merged[[CLASS_NAME, "geometry"]])
    parents = geopandas.GeoDataFrame(
        pandas.concat(sources, ignore_index=True).rename(columns={CLASS_NAME: "label"}), geometry="geometry"
    )

    structures = gdf.loc[~is_parent]
    points = geopandas.GeoDataFrame(geometry=structures.geometry.representative_point(), index=structures.index)
    joined = geopandas.sjoin(points, parents, predicate="within", how="left")
    # Vectorised: at a million cells a per-shape Python step costs tens of seconds.
    found = joined["label"].groupby(level=0)
    n_labels, first_label = found.nunique(), found.first()

    assigned = first_label[n_labels == 1]
    grouped = structures.loc[assigned.index].copy()
    original = grouped[CLASS_NAME]
    grouped[CLASS_NAME] = assigned.astype(str) + PARENT_SEPARATOR + original.astype(str)
    # One parse per distinct classification and name, not one per shape.
    parsed: dict[tuple, str] = {}
    grouped["classification"] = [
        parsed.get(key) or parsed.setdefault(key, _grouped_classification(*key))
        for key in zip(grouped["classification"], grouped[CLASS_NAME], strict=True)
    ]

    pairs = pandas.DataFrame({"parent": assigned, "class": original, "name": grouped[CLASS_NAME]}).drop_duplicates()
    combinations: dict[str, set[tuple[str, str]]] = {}
    for parent, class_name, name in pairs.itertuples(index=False):
        combinations.setdefault(name, set()).add((parent, class_name))

    ambiguous = n_labels[n_labels != 1]
    reasons = pandas.Series(
        numpy.where(ambiguous == 0, NO_PARENT, SEVERAL_PARENTS), index=ambiguous.index, dtype=object
    )
    left_out = geopandas.GeoDataFrame(
        structures.loc[reasons.index, [CLASS_NAME, "geometry"]].assign(reason=reasons), geometry="geometry"
    )

    holding = set(joined["index_right"].dropna().astype(int))
    empty = parents.loc[~parents.index.isin(holding), "label"].value_counts()
    counts = parents["label"].value_counts()

    report = ParentReport(
        parents=parents,
        left_out=left_out,
        n_grouped=len(grouped),
        repeated_parents={label: int(n) for label, n in counts.items() if n > 1},
        empty_parents={label: int(n) for label, n in empty.items()},
        n_multipolygon_parents=n_multipolygon_parents,
        combinations=combinations,
    )
    logger.info(
        f"Parent annotations {sorted(parent_classes)}: {report.n_parents} parent annotations, {report.n_grouped} shapes grouped, "
        f"{len(left_out)} left out"
    )
    return grouped, report


def left_out_table(reports: dict[str, ParentReport]) -> pandas.DataFrame:
    """Per slide and class, how many shapes were left out and why. Only rows with a count."""
    rows = [
        report.left_out[[CLASS_NAME, "reason"]].assign(slide=slide)
        for slide, report in reports.items()
        if not report.left_out.empty
    ]
    columns = ["slide", "class", NO_PARENT, SEVERAL_PARENTS]
    if not rows:
        return pandas.DataFrame(columns=columns)
    frame = pandas.concat(rows, ignore_index=True).rename(columns={CLASS_NAME: "class"})
    table = pandas.crosstab([frame["slide"], frame["class"]], frame["reason"])
    table = table.reindex(columns=[NO_PARENT, SEVERAL_PARENTS], fill_value=0).reset_index()
    table.columns.name = None
    return table[columns]


def colliding_names(reports: dict[str, ParentReport]) -> dict[str, list[tuple[str, str]]]:
    """Names that two different parent annotation and class pairs both produce, across every slide.

    `P1` + `a_b` and `P1_a` + `b` are both `P1_a_b`, and would share a well.
    """
    pairs: dict[str, set[tuple[str, str]]] = {}
    for report in reports.values():
        for name, found in report.combinations.items():
            pairs.setdefault(name, set()).update(found)
    return {name: sorted(found) for name, found in sorted(pairs.items()) if len(found) > 1}


def provenance(classes: list[str], reports: dict[str, ParentReport]) -> dict:
    """What the parent annotations did, for `provenance.json`."""
    return {
        "classes": sorted(classes),
        "slides": {
            slide: {
                "parent_annotations": report.n_parents,
                "shapes_grouped": report.n_grouped,
                "left_out_no_parent": int((report.left_out["reason"] == NO_PARENT).sum()),
                "left_out_several_parents": int((report.left_out["reason"] == SEVERAL_PARENTS).sum()),
            }
            for slide, report in reports.items()
        },
    }
