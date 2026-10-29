"""Reading and QC of QuPath GeoJSON.

Failures here mean the app is misreading its only input format, so each test says what shape
of real export it stands for.
"""

import json

import pytest

from qupath_to_lmd import geojson
from qupath_to_lmd.model import CLASS_NAME
from tests.conftest import ANNOTATIONS_FILE, CELLS_FILE


def test_crs_is_cleared_so_areas_are_computed_on_pixels(cells_gdf):
    """QuPath coordinates are image pixels, but GeoJSON's default CRS is EPSG:4326.

    Left alone, geopandas treats them as longitude/latitude and every area and distance is
    computed against a geographic projection — silently wrong, and the basis of every µm²
    figure in the app.
    """
    assert cells_gdf.crs is None, (
        f"Expected no CRS so that .area works on pixels, but the frame is tagged {cells_gdf.crs}. "
        "Areas and distances will be computed against a geographic projection and be wrong."
    )
    assert cells_gdf.geometry.area.sum() > 0, "Areas came out as zero or negative on pixel coordinates."


def test_calibration_points_are_found_and_removed(cells):
    """Named Point geometries become the calibration pool and leave the shape frame.

    If they stayed, they would be treated as cuttable shapes.
    """
    gdf, points, report = cells
    assert list(points) == ["calib1", "calib2", "calib3"], (
        f"Expected the three named calibration points from the demo file, got {list(points)}."
    )
    assert "Point" not in set(gdf.geometry.geom_type), (
        "Calibration points are still in the shape frame; they would be exported as shapes to cut."
    )
    assert report.calibration_point_names == list(points)


def test_a_file_without_calibration_points_still_reads(tmp_path):
    """QuPath omits a property entirely when no object carries it.

    So a file with no calibration points has no `name` column at all. That is the commonest
    shape of a "broken" export and it is not broken — the user has not added the points yet.
    Rejecting it here would be wrong; the calibration step is where that is reported.
    """
    document = json.loads(open(CELLS_FILE).read())
    document["features"] = [f for f in document["features"] if f["geometry"]["type"] != "Point"]
    path = tmp_path / "no_calibs.geojson"
    path.write_text(json.dumps(document))

    gdf, points, _report = geojson.read_and_qc(str(path))
    assert points == {}, f"Expected no calibration points, got {list(points)}."
    assert len(gdf) > 0, (
        "A file with no calibration points was read as empty. It should load fine — only the "
        "calibration step should object."
    )


def test_multiclass_objects_become_one_combined_class(multiclass):
    """QuPath writes multi-class objects as `names` (plural), which single-class parsing misses.

    Read naively they come out as None, which used to crash the plate layout. They are joined
    with `--`, sorted, so one set of classes always yields one class name.
    """
    gdf, _points, report = multiclass
    assert report.multiclass_counts == {"Immune cells--Tumor": 24}, (
        f"Expected 24 multi-class shapes named 'Immune cells--Tumor', got {report.multiclass_counts}."
    )
    assert not gdf[CLASS_NAME].isna().any(), (
        "Some shapes have no class name. A None class breaks the plate layout, which sorts class names."
    )


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ({"name": "Tumor"}, "Tumor"),
        ({"names": ["Tumor", "Immune cells"]}, "Immune cells--Tumor"),
        ({"names": ["Immune cells", "Tumor"]}, "Immune cells--Tumor"),
        ({"names": ["Stroma", "Tumor", "B cell"]}, "B cell--Stroma--Tumor"),
        ({"color": [1, 2, 3]}, None),
    ],
)
def test_class_names_are_order_independent(names, expected):
    """The same set of classes must always give the same name, whatever order QuPath wrote it.

    Otherwise one biological category splits across two wells depending on export order.
    """
    assert geojson._classification_name(names) == expected, (
        f"Classification {names} resolved to {geojson._classification_name(names)!r}, expected {expected!r}."
    )


def test_multipoint_calibration_annotations_are_split(tmp_path):
    """QuPath's point tool can put several points into one annotation, exporting as MultiPoint.

    All three would be unreachable if only Point were handled.
    """
    document = json.loads(open(CELLS_FILE).read())
    points = [f for f in document["features"] if f["geometry"]["type"] == "Point"]
    coordinates = [f["geometry"]["coordinates"] for f in points]
    document["features"] = [f for f in document["features"] if f["geometry"]["type"] != "Point"]
    document["features"].insert(
        0,
        {
            "type": "Feature",
            "id": "mp",
            "properties": {"objectType": "annotation", "name": "calibs"},
            "geometry": {"type": "MultiPoint", "coordinates": coordinates},
        },
    )
    path = tmp_path / "multipoint.geojson"
    path.write_text(json.dumps(document))

    gdf, found, _report = geojson.read_and_qc(str(path))
    assert list(found) == ["calibs #1", "calibs #2", "calibs #3"], (
        f"A MultiPoint annotation should expose each point separately, got {list(found)}."
    )
    assert [list(v) for v in found.values()] == coordinates, "MultiPoint coordinates were not preserved in order."
    assert "MultiPoint" not in set(gdf.geometry.geom_type), "MultiPoint left in the shape frame."


def test_unclassified_and_unnamed_objects_are_dropped_with_counts(tmp_path):
    """Anything the app cannot assign to a well is dropped — but never silently.

    The count is what the UI reports, so losing it means losing the user's only notice.
    """
    document = json.loads(open(ANNOTATIONS_FILE).read())
    polygons = [f for f in document["features"] if f["geometry"]["type"] == "Polygon"]
    for feature in polygons[:2]:
        feature["properties"].pop("classification", None)
    for feature in polygons[2:3]:
        feature["properties"]["classification"] = {"color": [1, 2, 3]}
    path = tmp_path / "messy.geojson"
    path.write_text(json.dumps(document))

    gdf, _points, report = geojson.read_and_qc(str(path))
    assert report.n_unclassified_dropped == 2, (
        f"Expected 2 unclassified objects counted, got {report.n_unclassified_dropped}. "
        "The UI shows this count; without it the user is not told what was dropped."
    )
    assert report.n_unnamed_classification_dropped == 1, (
        f"Expected 1 classification with no usable name, got {report.n_unnamed_classification_dropped}."
    )
    assert len(gdf) == len(polygons) - 3


def test_multipolygons_are_reported_and_dropped(tmp_path):
    """py-lmd cuts one closed path per shape, so a MultiPolygon has no meaning.

    The report table used to select a column QuPath never writes, which raised KeyError for
    anyone who actually had one.
    """
    document = json.loads(open(ANNOTATIONS_FILE).read())
    for feature in document["features"]:
        if feature["geometry"]["type"] == "Polygon":
            feature["geometry"] = {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                    [[[50, 50], [60, 50], [60, 60], [50, 60], [50, 50]]],
                ],
            }
            break
    path = tmp_path / "multipolygon.geojson"
    path.write_text(json.dumps(document))

    gdf, _points, report = geojson.read_and_qc(str(path))
    assert report.n_multipolygons_dropped == 1, (
        f"Expected 1 MultiPolygon reported, got {report.n_multipolygons_dropped}."
    )
    assert report.multipolygons is not None and not report.multipolygons.empty, (
        "The MultiPolygon report table is empty, so the UI has nothing to show the user."
    )
    assert "MultiPolygon" not in set(gdf.geometry.geom_type)
    assert set(report.multipolygons.geom_type) == {"MultiPolygon"}, (
        "An outline merged from two tissue pieces in QuPath would have no geometry to group shapes with."
    )


def test_unused_columns_are_dropped_to_keep_large_files_affordable(cells_gdf):
    """`measurements` is ~22% of the frame at scale and is read exactly once, during the read.

    `name` only ever labelled calibration points, which have already gone.
    """
    for column in ("measurements", "name", "isLocked"):
        assert column not in cells_gdf.columns, (
            f"Column {column!r} survived the read. It is unused downstream and costs memory that "
            "matters on a 2.7 GB deployment."
        )
    for column in ("id", "objectType", "classification", "geometry", CLASS_NAME):
        assert column in cells_gdf.columns, (
            f"Column {column!r} is missing. It is needed either for the export or for the "
            "QuPath round-trip."
        )


def test_implied_pixel_size_is_derived_during_the_read(cells):
    """QuPath areas are µm² while coordinates are pixels, so the scale is recoverable.

    Derived once while the file is open, because the measurement strings are dropped after.
    """
    _gdf, _points, report = cells
    assert report.implied_pixel_size_um == pytest.approx(0.3467, abs=5e-4), (
        f"Expected ~0.3467 µm/px implied by this file's own measurements, got "
        f"{report.implied_pixel_size_um}."
    )
    assert report.n_area_measurements == 121, (
        f"Expected 121 measured cells, got {report.n_area_measurements}."
    )
    assert report.pixel_size_spread < 0.01, (
        f"Implied scale varies by {report.pixel_size_spread:.1%} across objects, which suggests "
        "the derivation is picking up the wrong field."
    )


def test_a_file_without_measurements_implies_no_scale(annotations):
    """Annotation exports carry no area measurements, so the app must ask instead of guessing."""
    _gdf, _points, report = annotations
    assert report.implied_pixel_size_um is None, (
        f"An annotation-only file should imply no scale, got {report.implied_pixel_size_um}."
    )
    assert report.n_area_measurements == 0


def test_explode_is_idempotent_across_repeated_runs(cells_gdf):
    """Exploding twice must not produce `T-Cell_001_001`.

    The original name is remembered so matching always happens against it.
    """
    once = geojson.explode_classes(cells_gdf, ["single_cells_demo"])
    twice = geojson.explode_classes(once, ["single_cells_demo"])
    assert sorted(once[CLASS_NAME]) == sorted(twice[CLASS_NAME]), (
        "Exploding an already-exploded class changed the names, so re-running the step corrupts them."
    )
    assert "single_cells_demo_001" in set(once[CLASS_NAME])


def test_empty_file_is_rejected(tmp_path):
    """An empty FeatureCollection cannot produce anything, so it raises rather than warns."""
    path = tmp_path / "empty.geojson"
    path.write_text('{"type": "FeatureCollection", "features": []}')
    with pytest.raises(geojson.GeojsonError, match="no features"):
        geojson.read_and_qc(str(path))


def test_sanitize_keeps_what_qupath_needs_back(cells_gdf):
    """The processed GeoJSON must re-open in QuPath, which needs these four fields and no NaNs."""
    sanitized = geojson.sanitize_for_qupath(cells_gdf)
    assert list(sanitized.columns) == ["id", "objectType", "classification", "geometry"], (
        f"QuPath needs exactly these columns to re-import; got {list(sanitized.columns)}."
    )
    assert not sanitized.isna().to_numpy().any(), "NaNs survived, and QuPath rejects NaN-bearing properties."


def test_points_without_names_are_reported_not_dropped_silently(tmp_path):
    """A point with no name cannot be picked as a calibration point.

    Dropping it silently left the user staring at "no calibration points" while looking at the
    points they had just drawn in QuPath.
    """
    document = json.loads(open(CELLS_FILE).read())
    for feature in document["features"]:
        if feature["geometry"]["type"] == "Point":
            feature["properties"]["name"] = None
    path = tmp_path / "unnamed_points.geojson"
    path.write_text(json.dumps(document))

    _gdf, points, report = geojson.read_and_qc(str(path))
    assert points == {}, "Unnamed points must not become calibration candidates."
    assert report.n_unnamed_points == 3, (
        f"Three unnamed points should be reported so the user knows why none are selectable; "
        f"the report says {report.n_unnamed_points}."
    )


def test_named_points_are_not_counted_as_unnamed(cells):
    _gdf, points, report = cells
    assert len(points) == 3
    assert report.n_unnamed_points == 0, (
        f"All three points in this file are named, but {report.n_unnamed_points} were reported "
        "as unnamed."
    )


def test_the_summary_only_lists_findings_that_apply(annotations, multiclass):
    """A clean file must not produce rows of zeros.

    This table replaced six warning boxes. Its whole reason for existing is that a user reads
    it; padding it with findings that did not happen brings back the noise it was meant to
    remove.
    """
    _gdf, _points, clean = annotations
    summary = clean.summary()
    assert list(summary.index) == ["In the file"], (
        f"A clean file produced {list(summary.index)}. Only findings that actually apply "
        "belong in the table."
    )
    assert summary.at["In the file", "Count"] == clean.n_shapes_in_file

    _gdf, _points, messy = multiclass
    rows = messy.summary()
    assert rows.index[0] == "In the file", (
        f"The table should start from the file total and list findings under it; got "
        f"{list(rows.index)}."
    )
    assert "Ready to collect" not in rows.index, (
        "The surviving count belongs to the success message, not the table — showing it in "
        "both leaves the reader deciding which one to trust."
    )
    assert len(rows) > 1, "This export has unclassified and multi-class shapes; both must show."
    for label, count in rows["Count"].items():
        assert count > 0, f"'{label}' is in the table with a count of {count}."


def test_the_summary_explains_every_shape_that_was_dropped(multiclass):
    """Shapes are never dropped silently (CLAUDE.md rule 3), and this table is now the only
    place the user is told, so a cause missing from it makes the loss invisible."""
    _gdf, _points, report = multiclass
    summary = report.summary()

    ignored = {
        label: int(count)
        for label, count in summary["Count"].items()
        if "ignored" in summary.at[label, "What happens"]
    }
    dropped = report.n_shapes_in_file - report.n_shapes_kept
    assert sum(ignored.values()) == dropped, (
        f"{dropped} shapes did not survive QC but the table explains {sum(ignored.values())} "
        f"of them ({ignored}). A user reading it would not know where the rest went."
    )
    assert dropped > 0, "This fixture drops shapes, so there is something to explain."


def _renamed(tmp_path, names: list, file_name: str = "renamed.geojson"):
    """TD_01's six annotations, classified in turn by each entry of `names` (a name or a list)."""
    document = json.loads(open(ANNOTATIONS_FILE).read())
    shapes = [f for f in document["features"] if f["geometry"]["type"] != "Point"]
    for feature, name in zip(shapes, names * len(shapes), strict=False):
        key = "names" if isinstance(name, list) else "name"
        feature["properties"]["classification"] = {key: name, "color": [255, 0, 0]}
    path = tmp_path / file_name
    path.write_text(json.dumps(document))
    return path


def test_spaces_inside_a_class_name_are_kept(tmp_path):
    """Issue 24: the old samples-and-wells parser removed every space, so `Immune cells` matched
    nothing. Spaces inside a name are the user's and must reach the wells untouched."""
    gdf, _points, report = geojson.read_and_qc(_renamed(tmp_path, ["Immune cells", "CD8 T cell: PD1 positive"]))
    assert set(gdf[CLASS_NAME]) == {"Immune cells", "CD8 T cell: PD1 positive"}, (
        f"Class names with inner spaces were changed on reading: {set(gdf[CLASS_NAME])}"
    )
    assert not report.tidied_class_names, f"Ordinary names were reported as tidied: {report.tidied_class_names}"


@pytest.mark.parametrize(
    ("written", "used"),
    [("Tumor ", "Tumor"), (" Tumor", "Tumor"), ("Immune  cells", "Immune cells"), ("Immune cells", "Immune cells"),
     ("Immune\tcells", "Immune cells")],
)
def test_invisible_whitespace_in_a_class_name_is_tidied_and_reported(tmp_path, written, used):
    """`Tumor ` looks like `Tumor` on screen but is a different class: it would pool with nothing
    on another slide and match nothing in a samples-and-wells file, with no visible reason."""
    gdf, _points, report = geojson.read_and_qc(_renamed(tmp_path, [written, "Stroma"]))
    assert set(gdf[CLASS_NAME]) == {used, "Stroma"}, (
        f"{written!r} was read as {set(gdf[CLASS_NAME]) - {'Stroma'}}, so it is a different class from {used!r}."
    )
    assert report.tidied_class_names == {written: used}
    assert report.n_tidied_shapes == 3, f"Expected the 3 shapes of that class counted, got {report.n_tidied_shapes}."
    summary = report.summary()
    assert "Class name with extra spaces" in summary.index, "The tidying is not shown in the reading table."
    assert f"'{used}'" in summary.loc["Class name with extra spaces", "What happens"]


def test_tidied_multiclass_parts_still_sort_into_one_class(tmp_path):
    """A leading space sorts before every letter, so untidied parts would join in a different order
    and split one combination across two wells."""
    gdf, _points, _report = geojson.read_and_qc(_renamed(tmp_path, [[" Tumor", "Immune cells"], ["Tumor", "Immune cells"]]))
    assert set(gdf[CLASS_NAME]) == {"Immune cells--Tumor"}, f"One combination of classes became {set(gdf[CLASS_NAME])}."


def test_a_tidied_name_is_what_reopens_in_qupath(tmp_path):
    """The processed GeoJSON goes back into QuPath; its classification must carry the name the
    app collected under, or the re-imported shapes show a class the collection never used."""
    gdf, _points, _report = geojson.read_and_qc(_renamed(tmp_path, ["Tumor ", ["Immune cells ", " Stroma"]]))
    written = geojson.sanitize_for_qupath(gdf)["classification"].map(geojson._classification_parts)
    assert {tuple(parts) for parts in written} == {("Tumor",), ("Immune cells", "Stroma")}, (
        f"The processed GeoJSON still carries the untidied names: {set(map(tuple, written))}"
    )


def test_a_class_name_of_only_spaces_counts_as_unnamed(tmp_path):
    gdf, _points, report = geojson.read_and_qc(_renamed(tmp_path, ["   ", "Tumor"]))
    assert set(gdf[CLASS_NAME]) == {"Tumor"}, "A blank class name became a class of its own, with a well."
    assert report.n_unnamed_classification_dropped == 3, "The blank-named shapes were not reported as dropped."
