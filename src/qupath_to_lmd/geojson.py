"""Reading, QC and rewriting of QuPath GeoJSON.

Pure functions: they return findings instead of drawing them, so the UI layer decides
how to present them and so they can be exercised outside Streamlit.
"""

import ast
import json
from dataclasses import dataclass, field
from typing import Any

import geopandas
import numpy
import pandas
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME

# Joins the classes of a multi-class object into one class name. Deliberately not ": ",
# which reads as a hierarchy — QuPath multi-class objects have no parent and no child, and
# there can be four or more of them. Names are sorted before joining so the same set of
# classes always produces the same class name, whatever order QuPath wrote them in.
MULTICLASS_SEPARATOR = "--"


class GeojsonError(Exception):
    """The file cannot be used at all — no geometry, or no way to find calibration points."""


@dataclass
class GeojsonReport:
    """What QC found. Everything here is shown to the user; nothing is dropped silently."""

    geometry_counts: dict[str, int] = field(default_factory=dict)
    n_shapes_in_file: int = 0
    calibration_point_names: list[str] = field(default_factory=list)
    n_unnamed_points: int = 0
    n_unclassified_dropped: int = 0
    n_unnamed_classification_dropped: int = 0
    multiclass_counts: dict[str, int] = field(default_factory=dict)
    multipolygons: pandas.DataFrame | None = None
    n_shapes_kept: int = 0
    # Derived from QuPath's own area measurements while the file is open, so the measurement
    # strings can then be dropped — they are 22% of the frame at a million shapes.
    implied_pixel_size_um: float | None = None
    n_area_measurements: int = 0
    pixel_size_spread: float | None = None

    @property
    def n_multipolygons_dropped(self) -> int:
        """How many MultiPolygons were removed because py-lmd cannot cut them."""
        return 0 if self.multipolygons is None else len(self.multipolygons)


    def summary(self) -> "pandas.DataFrame":
        """One compact table describing the file, for showing instead of a stack of warnings.

        Only rows that apply are included, so a clean file produces one line rather than six
        with zeros in them. Per-class detail belongs later in the workflow, not here
        (`decisions.md` 064).

        The count that survives QC is deliberately absent: the success message below the table
        already gives it, and saying it twice on one screen invites the reader to work out
        which of the two is authoritative.
        """
        rows = [("In the file", self.n_shapes_in_file, "")]
        findings = [
            (
                "No QuPath classification",
                self.n_unclassified_dropped,
                "ignored — classify them in QuPath to include them",
            ),
            (
                "Classification without a usable name",
                self.n_unnamed_classification_dropped,
                "ignored — check how they are classified in QuPath",
            ),
            (
                "More than one class",
                sum(self.multiclass_counts.values()),
                "kept, as combined classes joined by --",
            ),
            (
                "Several separate outlines",
                self.n_multipolygons_dropped,
                "ignored — the cutter cannot follow these as one path",
            ),
        ]
        rows.extend((label, count, note) for label, count, note in findings if count)

        frame = pandas.DataFrame(rows, columns=["Shapes", "Count", "What happens"])
        return frame.set_index("Shapes")


def read_and_qc(source) -> tuple[geopandas.GeoDataFrame, dict[str, list[float]], GeojsonReport]:
    """Read a QuPath GeoJSON, split off calibration points, and QC the rest.

    Args:
        source: path or file-like object of a QuPath-exported FeatureCollection.

    Returns:
        The cuttable shapes, the calibration-point pool as `{name: [x, y]}`, and a report.

    Raises:
        GeojsonError: the file is empty, or has no `name` column to find points with.
    """
    logger.info("Reading GeoJSON")
    gdf = geopandas.read_file(source)

    # QuPath coordinates are image pixels, but GeoJSON's default CRS is EPSG:4326, so
    # geopandas tags them as longitude/latitude. Left alone, every .area and .distance
    # call is computed against a geographic projection and is wrong.
    gdf = gdf.set_crs(None, allow_override=True)

    if gdf.empty:
        raise GeojsonError("The uploaded GeoJSON file has no features in it.")

    report = GeojsonReport(geometry_counts=gdf.geometry.geom_type.value_counts().to_dict())
    logger.info(f"Geometries: {report.geometry_counts}")

    # Named Points are the calibration-point pool; all Points then leave the frame.
    # A file with none is perfectly readable — the user just has not added them yet, which
    # the UI reports. QuPath omits a property entirely when no object has it, so a file
    # without calibration points has no `name` column at all.
    is_point = gdf.geometry.geom_type.isin(["Point", "MultiPoint"])
    report.n_shapes_in_file = int((~is_point).sum())

    calibration_points = _calibration_points(gdf)
    report.calibration_point_names = list(calibration_points)
    # A point with no name cannot be picked as a calibration point, and dropping it silently
    # leaves the user staring at "no calibration points" while looking at points they just drew.
    report.n_unnamed_points = max(0, int(is_point.sum()) - _named_point_count(gdf))
    logger.info(
        f"Found {len(calibration_points)} calibration points: {report.calibration_point_names}"
        + (f", and {report.n_unnamed_points} unnamed points" if report.n_unnamed_points else "")
    )
    gdf = gdf[~is_point]

    # Unclassified QuPath objects cannot be assigned to a well.
    if "classification" not in gdf.columns:
        raise GeojsonError("No 'classification' column found — none of the objects are classified in QuPath.")
    n_unclassified = int(gdf["classification"].isna().sum())
    if n_unclassified:
        report.n_unclassified_dropped = n_unclassified
        logger.debug(f"Dropping {n_unclassified} unclassified shapes")
        gdf = gdf[gdf["classification"].notna()]

    gdf = gdf.copy()
    gdf[CLASS_NAME] = gdf["classification"].apply(_classification_name)

    # QuPath multi-class objects carry `names` (plural) instead of `name`; those are joined
    # into one class below. Anything still unnamed cannot be assigned to a well.
    report.multiclass_counts = (
        gdf.loc[gdf[CLASS_NAME].str.contains(MULTICLASS_SEPARATOR, na=False), CLASS_NAME]
        .value_counts()
        .to_dict()
    )
    unnamed = gdf[CLASS_NAME].isna()
    if unnamed.any():
        report.n_unnamed_classification_dropped = int(unnamed.sum())
        logger.warning(f"Dropping {int(unnamed.sum())} shapes whose classification has no usable name")
        gdf = gdf[~unnamed]

    # py-lmd cuts a single closed path per shape, so a MultiPolygon has no meaning here.
    is_multi = gdf.geometry.geom_type == "MultiPolygon"
    if is_multi.any():
        columns = [c for c in ("name", CLASS_NAME) if c in gdf.columns]
        report.multipolygons = pandas.DataFrame(gdf.loc[is_multi, columns])
        logger.debug(f"Dropping {int(is_multi.sum())} MultiPolygons")
        gdf = gdf[~is_multi]

    # Read the scale QuPath's measurements imply before dropping them.
    implied, n_measured, spread = implied_pixel_size(gdf)
    report.implied_pixel_size_um = implied
    report.n_area_measurements = n_measured
    report.pixel_size_spread = spread

    gdf = drop_unused_columns(gdf)

    report.n_shapes_kept = len(gdf)
    logger.success(f"GeoJSON QC complete, {report.n_shapes_kept} shapes kept")
    return gdf, calibration_points, report


# Columns nothing downstream reads. `measurements` is consumed once, above, to derive the
# implied pixel size; `name` only ever labelled the calibration points, which have already
# left the frame. At a million shapes they are 107 MB of a 383 MB frame (`decisions.md` 051).
UNUSED_COLUMNS = ("measurements", "name", "isLocked")


def drop_unused_columns(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Drop columns nothing downstream reads, to keep large files affordable."""
    present = [column for column in UNUSED_COLUMNS if column in gdf.columns]
    if not present:
        return gdf
    freed = gdf[present].memory_usage(deep=True).sum() / 1e6
    logger.info(f"Dropping unused columns {present}, freeing {freed:.0f} MB")
    return gdf.drop(columns=present)


def implied_pixel_size(gdf: geopandas.GeoDataFrame) -> tuple[float | None, int, float | None]:
    """The µm/px that QuPath's own area measurements imply, if it carries any.

    QuPath writes object areas in µm² while GeoJSON coordinates stay in image pixels, so
    `sqrt(area_µm² / area_px²)` recovers the scale. Computed once while the file is open.

    Returns:
        The median implied scale, how many objects it came from, and its relative spread.
    """
    area_um2 = area_measurements(gdf)
    area_px2 = gdf.geometry.area
    usable = area_um2.notna() & (area_um2 > 0) & (area_px2 > 0)
    if not usable.any():
        return None, 0, None

    implied = numpy.sqrt(area_um2[usable] / area_px2[usable])
    median = float(implied.median())
    spread = float(implied.std() / median) if len(implied) > 1 else 0.0
    logger.info(f"QuPath areas imply {median:.4f} µm/px over {int(usable.sum())} shapes")
    return median, int(usable.sum()), spread


def _classification_name(value) -> str | None:
    """Resolve a QuPath `classification` value to a single class name.

    QuPath writes it as a JSON string, though geopandas sometimes hands back a dict. A
    single-class object has `name`; a **multi-class** object has `names` (plural) holding
    every class applied to it. Multi-class names are sorted and joined with
    `MULTICLASS_SEPARATOR` into a single flat class name — the classes are peers, not a
    hierarchy, and sorting means one set of classes always yields one class name.
    """
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            logger.debug(f"Could not parse classification {value!r}")
            return None
    if not isinstance(value, dict):
        return None

    name = value.get("name")
    if name:
        return name

    names = value.get("names")
    if isinstance(names, list) and names:
        return MULTICLASS_SEPARATOR.join(sorted(str(part) for part in names))
    return None


def _calibration_points(gdf: geopandas.GeoDataFrame) -> dict[str, list[float]]:
    """Named Point geometries, as `{name: [x, y]}`.

    Handles `MultiPoint` too: QuPath's point tool can put several points into one
    annotation object, which exports as a single MultiPoint feature. Each part is exposed
    separately, suffixed `#1`, `#2`, so all three can still be picked individually.
    """
    if "name" not in gdf.columns:
        return {}

    points = gdf[gdf.geometry.geom_type.isin(["Point", "MultiPoint"])]
    calibration_points: dict[str, list[float]] = {}

    for _, row in points.iterrows():
        label = row["name"]
        if not label or pandas.isna(label):
            continue
        geometry = row.geometry
        if geometry.geom_type == "Point":
            calibration_points[str(label)] = [geometry.x, geometry.y]
        else:
            for i, part in enumerate(geometry.geoms, start=1):
                calibration_points[f"{label} #{i}"] = [part.x, part.y]

    return calibration_points


def _named_point_count(gdf: geopandas.GeoDataFrame) -> int:
    """How many Point/MultiPoint features carry a usable name."""
    if "name" not in gdf.columns:
        return 0
    points = gdf[gdf.geometry.geom_type.isin(["Point", "MultiPoint"])]
    return int(sum(1 for label in points["name"] if label and not pandas.isna(label)))


def explode_classes(gdf: geopandas.GeoDataFrame, classes: list[str]) -> geopandas.GeoDataFrame:
    """Give every shape of the named classes its own numbered class, for single-cell collection.

    `T-Cell` becomes `T-Cell_001`, `T-Cell_002`, ... Re-running is safe: the original name
    is remembered, so matching is always done against it rather than against the last result.
    """
    logger.info(f"Exploding classes into per-shape names: {classes}")
    gdf = gdf.copy()

    if "original_classification_name" not in gdf.columns:
        gdf["original_classification_name"] = gdf[CLASS_NAME]

    for class_name in classes:
        matching = gdf.index[gdf["original_classification_name"] == class_name]
        for i, idx in enumerate(matching, start=1):
            gdf.loc[idx, CLASS_NAME] = f"{class_name}_{str(i).zfill(3)}"

    gdf = rewrite_classification(gdf)
    logger.success(f"Exploded {len(classes)} class(es)")
    return gdf


def rewrite_classification(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Push `classification_name` back into the nested `classification` value.

    Needed so the exported GeoJSON re-opens in QuPath with the new class names.
    """
    gdf = gdf.copy()

    def rewrite(row):
        value = row["classification"]
        as_dict = ast.literal_eval(value) if isinstance(value, str) else dict(value)
        as_dict["name"] = row[CLASS_NAME]
        return str(as_dict)

    gdf["classification"] = gdf.apply(rewrite, axis=1)
    return gdf


def classification_values(gdf: geopandas.GeoDataFrame) -> dict[str, Any]:
    """The `classification` value QuPath wrote for each class name.

    Used to give shapes this app creates the same class colour the user chose in QuPath, so a
    re-imported file looks like the one they exported rather than a set of grey outlines.
    """
    if "classification" not in gdf.columns or CLASS_NAME not in gdf.columns:
        return {}
    first = gdf.dropna(subset=["classification"]).drop_duplicates(subset=[CLASS_NAME])
    return dict(zip(first[CLASS_NAME], first["classification"], strict=True))


def synthesize_qupath_columns(
    gdf: geopandas.GeoDataFrame, prefix: str, source: geopandas.GeoDataFrame | None = None
) -> geopandas.GeoDataFrame:
    """Give shapes this app created the three fields QuPath re-import needs.

    Merged regions and packed circles have no QuPath object behind them, but `sanitize_for_qupath`
    and the plan frame both require `id`, `objectType` and `classification`. Ids are positional,
    so the same input always produces the same ids and a collection stays comparable between
    sessions. `objectType` is `annotation` because that is what QuPath calls a region someone
    drew, which is what these are.
    """
    gdf = gdf.copy()
    gdf["id"] = [f"{prefix}-{position:06d}" for position in range(len(gdf))]
    gdf["objectType"] = "annotation"

    known = classification_values(source) if source is not None else {}
    gdf["classification"] = [
        known.get(name, str({"name": name})) for name in gdf[CLASS_NAME]
    ]
    logger.info(f"Synthesized QuPath fields for {len(gdf)} shapes with prefix {prefix!r}")
    return gdf


def extract_coordinates(geometry) -> list[list[float]]:
    """Outline of a shape as a list of `[x, y]`, ready for py-lmd."""
    if geometry.geom_type == "Polygon":
        return [list(coord) for coord in geometry.exterior.coords]
    if geometry.geom_type == "LineString":
        return [list(coord) for coord in geometry.coords]
    raise GeojsonError(
        f"Geometry type {geometry.geom_type} is not supported. "
        "Convert it to a Polygon or LineString in QuPath."
    )


def sanitize_for_qupath(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Reduce a frame to what QuPath will re-import without complaining.

    QuPath chokes on NaN-bearing properties, so columns holding any are dropped and only
    the fields it needs are kept.
    """
    logger.info("Sanitizing GeoDataFrame for QuPath re-import")
    gdf = gdf.dropna(axis="columns")

    required = ["id", "objectType", "classification", "geometry"]
    missing = [column for column in required if column not in gdf.columns]
    if missing:
        raise GeojsonError(f"Cannot write a QuPath-compatible GeoJSON, missing columns: {missing}")

    return gdf[required]


def measurements_frame(gdf: geopandas.GeoDataFrame) -> pandas.DataFrame:
    """Explode QuPath's `measurements` JSON into a DataFrame, one column per measurement.

    Only cells and detections carry measurements; annotation-only files give an empty frame.
    The index matches `gdf`, so the result can be joined straight back on.
    """
    if "measurements" not in gdf.columns:
        return pandas.DataFrame(index=gdf.index)

    parsed = {}
    for index, raw in gdf["measurements"].items():
        if isinstance(raw, str) and raw.strip():
            try:
                parsed[index] = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(f"Could not parse measurements for {index}")
        elif isinstance(raw, dict):
            parsed[index] = raw

    frame = pandas.DataFrame.from_dict(parsed, orient="index")
    logger.info(f"Parsed measurements for {len(frame)} of {len(gdf)} objects, {frame.shape[1]} fields")
    return frame.reindex(gdf.index)


def area_measurements(gdf: geopandas.GeoDataFrame) -> pandas.Series:
    """QuPath's per-object area in µm², without parsing the other measurement fields.

    A real export carries around 100 fields per cell, so building the whole frame to read one
    column costs a quarter of a second and a large transient allocation on every rerun
    (`decisions.md` 050). This pulls out just the area.
    """
    if "measurements" not in gdf.columns:
        return pandas.Series(dtype="float64", index=gdf.index)

    values = {}
    for index, raw in gdf["measurements"].items():
        parsed = None
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw, dict):
            parsed = raw
        if not parsed:
            continue
        key = _area_key(parsed)
        if key:
            values[index] = parsed[key]

    return pandas.Series(values, dtype="float64").reindex(gdf.index)


def _area_key(measurement: dict) -> str | None:
    """`Cell: Area` if present, else any other field naming an area."""
    if "Cell: Area" in measurement:
        return "Cell: Area"
    return next((k for k in measurement if str(k).strip().endswith("Area")), None)


def area_measurement_column(measurements: pandas.DataFrame) -> str | None:
    """Find the column holding QuPath's object area, which it reports in µm².

    `Cell: Area` is what cell segmentation writes; fall back to any other area field so
    detections and custom pipelines still work.
    """
    if measurements.empty:
        return None
    if "Cell: Area" in measurements.columns:
        return "Cell: Area"
    candidates = [c for c in measurements.columns if c.strip().endswith("Area")]
    return candidates[0] if candidates else None
