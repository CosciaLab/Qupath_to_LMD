"""Group shapes by the outline they sit in: `gf` inside a `P1` outline becomes `P1_gf`.

Several patients often share one image, and slides pool by class name, so without this `gf` from
every patient would land in one well. An outline is a shape whose class names a group — a patient,
a core, a region. It is never cut; it only gives its class to the shapes inside it, so a lab can
annotate a handful of categories and still keep patients apart (`decisions.md` 091).
"""

import ast
from collections.abc import Collection
from dataclasses import dataclass, field

import geopandas
import pandas
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME

OUTLINE_SEPARATOR = "_"
NO_OUTLINE = "no outline"
SEVERAL_OUTLINES = "several outlines"


def _empty_outlines() -> geopandas.GeoDataFrame:
    return geopandas.GeoDataFrame({"label": pandas.Series(dtype=str)}, geometry=[])


def _empty_left_out() -> geopandas.GeoDataFrame:
    return geopandas.GeoDataFrame(
        {CLASS_NAME: pandas.Series(dtype=str), "reason": pandas.Series(dtype=str)}, geometry=[]
    )


@dataclass
class OutlineReport:
    """What grouping one slide by its outlines did. Everything here is shown to the user."""

    outlines: geopandas.GeoDataFrame = field(default_factory=_empty_outlines)
    left_out: geopandas.GeoDataFrame = field(default_factory=_empty_left_out)
    n_grouped: int = 0
    repeated_outlines: dict[str, int] = field(default_factory=dict)
    empty_outlines: dict[str, int] = field(default_factory=dict)
    n_multipolygon_outlines: int = 0
    combinations: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    @property
    def n_outlines(self) -> int:
        """How many outlines the slide has, merged ones included."""
        return len(self.outlines)


def grouped_name(outline: str, class_name: str) -> str:
    """The sample name of a class inside an outline, outline first: `P1_gf`."""
    return f"{outline}{OUTLINE_SEPARATOR}{class_name}"


def _grouped_classification(value, name: str) -> str:
    """The classification QuPath should reopen: one class, the grouped one.

    A multi-class structure carries `names`; left in place beside the new `name`, QuPath could
    reopen it as its old classes rather than the sample it was cut into.
    """
    as_dict = ast.literal_eval(value) if isinstance(value, str) else dict(value)
    as_dict.pop("names", None)
    as_dict["name"] = name
    return str(as_dict)


def group_by_outlines(
    gdf: geopandas.GeoDataFrame,
    outline_classes: Collection[str],
    multipolygons: geopandas.GeoDataFrame | None = None,
) -> tuple[geopandas.GeoDataFrame, OutlineReport]:
    """Rename every shape after the outline it sits in, and take the outlines out.

    A shape belongs to the outline containing its interior point, so a structure that pokes over
    the edge still counts. Outlines of one class may overlap — one patient, two pieces — but a
    shape inside outlines of two different classes is ambiguous and left out, as is a shape in no
    outline: either way it would otherwise reach a sample of the wrong patient, or of none.

    Args:
        gdf: one slide's shapes, as `geojson.read_and_qc` returns them.
        outline_classes: the classes whose shapes are outlines. Empty returns `gdf` unchanged.
        multipolygons: the MultiPolygons reading set aside as uncuttable; those of an outline
            class are outlines too, since a merged outline is still an area.

    Returns:
        The grouped shapes — outlines and left-out shapes removed, original index kept — and
        the report.
    """
    if not outline_classes:
        return gdf, OutlineReport()

    is_outline = gdf[CLASS_NAME].isin(outline_classes)
    sources = [gdf.loc[is_outline, [CLASS_NAME, "geometry"]]]
    n_multipolygon_outlines = 0
    if multipolygons is not None and not multipolygons.empty:
        merged = multipolygons[multipolygons[CLASS_NAME].isin(outline_classes)]
        n_multipolygon_outlines = len(merged)
        sources.append(merged[[CLASS_NAME, "geometry"]])
    outlines = geopandas.GeoDataFrame(
        pandas.concat(sources, ignore_index=True).rename(columns={CLASS_NAME: "label"}), geometry="geometry"
    )

    structures = gdf.loc[~is_outline]
    points = geopandas.GeoDataFrame(geometry=structures.geometry.representative_point(), index=structures.index)
    joined = geopandas.sjoin(points, outlines, predicate="within", how="left")
    labels = joined.groupby(level=0)["label"].agg(lambda found: sorted(set(found.dropna())))

    assigned = labels[labels.map(len) == 1].map(lambda found: found[0])
    grouped = structures.loc[assigned.index].copy()
    original = grouped[CLASS_NAME]
    grouped[CLASS_NAME] = [grouped_name(o, c) for o, c in zip(assigned, original, strict=True)]
    grouped["classification"] = [
        _grouped_classification(value, name)
        for value, name in zip(grouped["classification"], grouped[CLASS_NAME], strict=True)
    ]

    combinations: dict[str, set[tuple[str, str]]] = {}
    for outline, class_name, name in zip(assigned, original, grouped[CLASS_NAME], strict=True):
        combinations.setdefault(name, set()).add((outline, class_name))

    reasons = labels[labels.map(len) != 1].map(lambda found: NO_OUTLINE if not found else SEVERAL_OUTLINES)
    left_out = geopandas.GeoDataFrame(
        structures.loc[reasons.index, [CLASS_NAME, "geometry"]].assign(reason=reasons), geometry="geometry"
    )

    holding = set(joined["index_right"].dropna().astype(int))
    empty = outlines.loc[~outlines.index.isin(holding), "label"].value_counts()
    counts = outlines["label"].value_counts()

    report = OutlineReport(
        outlines=outlines,
        left_out=left_out,
        n_grouped=len(grouped),
        repeated_outlines={label: int(n) for label, n in counts.items() if n > 1},
        empty_outlines={label: int(n) for label, n in empty.items()},
        n_multipolygon_outlines=n_multipolygon_outlines,
        combinations=combinations,
    )
    logger.info(
        f"Outlines {sorted(outline_classes)}: {report.n_outlines} outlines, {report.n_grouped} shapes grouped, "
        f"{len(left_out)} left out"
    )
    return grouped, report


def left_out_table(reports: dict[str, OutlineReport]) -> pandas.DataFrame:
    """Per slide and class, how many shapes were left out and why. Only rows with a count."""
    rows = [
        report.left_out[[CLASS_NAME, "reason"]].assign(slide=slide)
        for slide, report in reports.items()
        if not report.left_out.empty
    ]
    columns = ["slide", "class", NO_OUTLINE, SEVERAL_OUTLINES]
    if not rows:
        return pandas.DataFrame(columns=columns)
    frame = pandas.concat(rows, ignore_index=True).rename(columns={CLASS_NAME: "class"})
    table = pandas.crosstab([frame["slide"], frame["class"]], frame["reason"])
    table = table.reindex(columns=[NO_OUTLINE, SEVERAL_OUTLINES], fill_value=0).reset_index()
    table.columns.name = None
    return table[columns]


def colliding_names(reports: dict[str, OutlineReport]) -> dict[str, list[tuple[str, str]]]:
    """Names that two different outline and class pairs both produce, across every slide.

    `P1` + `a_b` and `P1_a` + `b` are both `P1_a_b`, and would share a well.
    """
    pairs: dict[str, set[tuple[str, str]]] = {}
    for report in reports.values():
        for name, found in report.combinations.items():
            pairs.setdefault(name, set()).update(found)
    return {name: sorted(found) for name, found in sorted(pairs.items()) if len(found) > 1}


def provenance(classes: list[str], reports: dict[str, OutlineReport]) -> dict:
    """What the outlines did, for `provenance.json`."""
    return {
        "classes": sorted(classes),
        "slides": {
            slide: {
                "outlines": report.n_outlines,
                "shapes_grouped": report.n_grouped,
                "left_out_no_outline": int((report.left_out["reason"] == NO_OUTLINE).sum()),
                "left_out_several_outlines": int((report.left_out["reason"] == SEVERAL_OUTLINES).sum()),
            }
            for slide, report in reports.items()
        },
    }
