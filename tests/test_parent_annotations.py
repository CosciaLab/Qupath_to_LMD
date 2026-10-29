"""Grouping shapes by the parent annotation they sit in.

Parent annotations decide which patient's tissue goes into which well. A failure here means tissue from two
patients can share a well, or a parent annotation itself gets cut.
"""

import ast
import json

import geopandas
import pandas
from shapely.geometry import MultiPolygon, box

from qupath_to_lmd import geojson, parent_annotations, slides
from qupath_to_lmd.model import CLASS_NAME
from tests.conftest import PARENTS_FILE

P1 = box(0, 0, 100, 100)
P2 = box(200, 0, 300, 100)


def _frame(features, classification=None) -> geopandas.GeoDataFrame:
    """Shapes as `read_and_qc` leaves them, from `(class name, geometry)` pairs."""
    names = [name for name, _ in features]
    return geopandas.GeoDataFrame(
        {
            "classification": [classification or str({"name": name, "color": [255, 0, 0]}) for name in names],
            CLASS_NAME: names,
        },
        geometry=[geometry for _, geometry in features],
    )


def _left_out_reasons(report) -> dict[str, str]:
    return dict(zip(report.left_out[CLASS_NAME], report.left_out["reason"], strict=True))


def test_shape_inside_one_parent_is_renamed():
    grouped, _report = parent_annotations.group_by_parents(_frame([("P1", P1), ("gf", box(10, 10, 20, 20))]), ["P1"])
    assert list(grouped[CLASS_NAME]) == ["P1_gf"], (
        "A gf shape inside the P1 parent annotation must become sample P1_gf, or patients mix in one well."
    )
    assert ast.literal_eval(grouped["classification"].iloc[0])["name"] == "P1_gf", (
        "The processed GeoJSON would reopen in QuPath under the old class, not the sample it was cut into."
    )


def test_parents_never_reach_the_frame_that_is_collected():
    frame = _frame([("P1", P1), ("P2", P2), ("gf", box(10, 10, 20, 20)), ("gf", box(210, 10, 220, 20))])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1", "P2"])
    assert not set(grouped[CLASS_NAME]) & {"P1", "P2"}, "A parent annotation reached the collection methods — it would be cut."
    assert report.n_parents == 2, f"Expected 2 parent annotations, got {report.n_parents}; the success line would miscount."


def test_accounting_every_shape_is_somewhere():
    frame = _frame([
        ("P1", P1), ("P2", P2), ("P3", box(50, 0, 150, 100)),
        ("gf", box(10, 10, 20, 20)),       # P1 only
        ("yf", box(210, 10, 220, 20)),     # P2 only
        ("gf", box(500, 500, 510, 510)),   # nowhere
        ("gf", box(60, 10, 70, 20)),       # P1 and P3
    ])
    _grouped, report = parent_annotations.group_by_parents(frame, ["P1", "P2", "P3"])
    assert report.n_parents + report.n_grouped + len(report.left_out) == len(frame), (
        "Some shapes are neither grouped, left out nor parent annotations: they would vanish without a word on screen."
    )


def test_shape_outside_every_parent_is_left_out():
    frame = _frame([("P1", P1), ("gf", box(500, 500, 510, 510))])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1"])
    assert grouped.empty, "A shape outside every parent annotation was collected, into a sample of no patient."
    assert _left_out_reasons(report) == {"gf": parent_annotations.NO_PARENT}, (
        "The left-out shape must keep its plain class and say why, so the warning can tell the user what to fix."
    )


def test_nested_parents_of_different_classes_are_ambiguous():
    frame = _frame([("P1", P1), ("C1", box(5, 5, 50, 50)), ("gf", box(10, 10, 20, 20))])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1", "C1"])
    assert grouped.empty, "A shape inside two parent annotations of different classes was given to one of them silently."
    assert _left_out_reasons(report) == {"gf": parent_annotations.SEVERAL_PARENTS}


def test_overlapping_parents_of_one_class_are_not_ambiguous():
    frame = _frame([("P1", P1), ("P1", box(50, 0, 150, 100)), ("gf", box(60, 10, 70, 20))])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1"])
    assert list(grouped[CLASS_NAME]) == ["P1_gf"], (
        "Two overlapping pieces of one patient's parent annotation made its shapes ambiguous; they would not be collected."
    )
    assert report.repeated_parents == {"P1": 2}


def test_shape_crossing_the_edge_is_assigned_by_its_interior_point():
    grouped, _report = parent_annotations.group_by_parents(_frame([("P1", P1), ("gf", box(90, 10, 105, 20))]), ["P1"])
    assert list(grouped[CLASS_NAME]) == ["P1_gf"], (
        "A structure poking slightly over its parent annotation was left out; users would have to redraw every parent annotation tightly."
    )


def test_multipolygon_parent_groups_both_pieces():
    frame = _frame([("gf", box(10, 10, 20, 20)), ("gf", box(210, 10, 220, 20))])
    multipolygons = geopandas.GeoDataFrame({CLASS_NAME: ["P1"], "name": [None]}, geometry=[MultiPolygon([P1, P2])])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1"], multipolygons)
    assert list(grouped[CLASS_NAME]) == ["P1_gf", "P1_gf"], (
        "A parent annotation merged from two tissue pieces in QuPath grouped nothing; that patient could not be collected."
    )
    assert report.n_multipolygon_parents == 1


def test_slide_without_parents_leaves_everything_out():
    frame = _frame([("gf", box(10, 10, 20, 20)), ("yf", box(30, 30, 40, 40))])
    grouped, report = parent_annotations.group_by_parents(frame, ["P1"])
    assert grouped.empty and report.n_grouped == 0, "Shapes on a slide with no parent annotation were collected under no patient."
    assert set(report.left_out["reason"]) == {parent_annotations.NO_PARENT}


def test_empty_parents_are_counted():
    frame = _frame([("P1", P1), ("P2", P2), ("gf", box(10, 10, 20, 20))])
    _grouped, report = parent_annotations.group_by_parents(frame, ["P1", "P2"])
    assert report.empty_parents == {"P2": 1}, (
        "A parent annotation holding no shapes went unmentioned; a misplaced parent annotation would only show up as a missing sample."
    )


def test_multiclass_structure_is_written_as_one_class():
    value = str({"names": ["Immune cells", "Tumor"], "color": [1, 2, 3]})
    frame = _frame([("P1", P1)])
    structure = _frame([("Immune cells--Tumor", box(10, 10, 20, 20))], classification=value)
    grouped, _report = parent_annotations.group_by_parents(pandas.concat([frame, structure], ignore_index=True), ["P1"])
    written = ast.literal_eval(grouped["classification"].iloc[0])
    assert written.get("name") == "P1_Immune cells--Tumor" and "names" not in written, (
        f"QuPath would reopen {written} as the old classes rather than the sample it was cut into."
    )


def test_colliding_names_are_found_across_slides():
    _a, report_a = parent_annotations.group_by_parents(_frame([("P1", P1), ("a_b", box(10, 10, 20, 20))]), ["P1"])
    _b, report_b = parent_annotations.group_by_parents(_frame([("P1_a", P1), ("b", box(10, 10, 20, 20))]), ["P1_a"])
    assert parent_annotations.colliding_names({"A": report_a, "B": report_b}) == {"P1_a_b": [("P1", "a_b"), ("P1_a", "b")]}, (
        "Two different patient-class pairs produce one name and would share a well without a warning."
    )


def test_no_parent_classes_returns_the_frame_untouched():
    frame = _frame([("P1", P1), ("gf", box(10, 10, 20, 20))])
    grouped, report = parent_annotations.group_by_parents(frame, [])
    pandas.testing.assert_frame_equal(grouped, frame)
    assert report.n_parents == 0 and report.left_out.empty, "Ticking nothing changed the shapes; the frozen output would move."


def test_explode_after_grouping_numbers_the_grouped_class():
    grouped, _report = parent_annotations.group_by_parents(_frame([("P1", P1), ("gf", box(10, 10, 20, 20))]), ["P1"])
    exploded = geojson.explode_classes(grouped, ["P1_gf"])
    assert list(exploded[CLASS_NAME]) == ["P1_gf_001"], (
        "'Every shape its own well' matched nothing after grouping, so single-shape collection would silently do nothing."
    )


def test_left_out_table_and_provenance_count_the_same_shapes():
    reports = {
        "A": parent_annotations.group_by_parents(_frame([("P1", P1), ("gf", box(500, 500, 510, 510))]), ["P1"])[1],
        "B": parent_annotations.group_by_parents(
            _frame([("P1", P1), ("C1", box(5, 5, 50, 50)), ("yf", box(10, 10, 20, 20)), ("yf", box(600, 0, 610, 10))]),
            ["P1", "C1"],
        )[1],
    }
    table = parent_annotations.left_out_table(reports)
    recorded = parent_annotations.provenance(["P1", "C1"], reports)
    in_table = int(table[[parent_annotations.NO_PARENT, parent_annotations.SEVERAL_PARENTS]].to_numpy().sum())
    in_provenance = sum(
        slide["left_out_no_parent"] + slide["left_out_several_parents"] for slide in recorded["slides"].values()
    )
    assert in_table == in_provenance == 3, (
        f"The warning shows {in_table} left-out shapes and provenance.json records {in_provenance}; "
        "the user and the record would disagree."
    )
    assert recorded["classes"] == ["C1", "P1"]


def test_grouped_class_from_two_slides_is_one_sample():
    ga, _ = parent_annotations.group_by_parents(_frame([("P1", P1), ("gf", box(10, 10, 20, 20))]), ["P1"])
    gb, _ = parent_annotations.group_by_parents(_frame([("P1", P2), ("gf", box(210, 10, 220, 20))]), ["P1"])
    sample_set = slides.whole_shape_samples({"A": ga, "B": gb}, ["P1_gf"], {"A": None, "B": None})
    assert sample_set.samples == ["P1_gf"], "P1's tissue from two images would land in two wells instead of being pooled."
    assert all(set(frame["group_key"]) == {"P1_gf"} for frame in sample_set.shapes.values())


def test_slide_with_only_parents_groups_nothing_without_failing():
    grouped, report = parent_annotations.group_by_parents(_frame([("P1", P1)]), ["P1"])
    assert grouped.empty and report.left_out.empty, "A slide holding only parent annotations should simply contribute nothing."
    assert report.empty_parents == {"P1": 1}


def _feature(geometry: dict, class_name: str | None = None, name: str | None = None) -> dict:
    properties = {"objectType": "annotation"}
    if class_name:
        properties["classification"] = {"name": class_name, "color": [255, 0, 0]}
    if name:
        properties["name"] = name
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _square(x: float, y: float, size: float) -> list:
    return [[[x, y], [x + size, y], [x + size, y + size], [x, y + size], [x, y]]]


def test_multipolygon_parent_read_from_a_file_groups_shapes(tmp_path):
    features = [
        _feature({"type": "Point", "coordinates": xy}, name=name)
        for name, xy in (("A", [0, 0]), ("B", [400, 0]), ("C", [0, 400]))
    ]
    features.append(
        _feature({"type": "MultiPolygon", "coordinates": [_square(0, 0, 100), _square(200, 0, 100)]}, "P1")
    )
    features += [
        _feature({"type": "Polygon", "coordinates": _square(10, 10, 10)}, "gf"),
        _feature({"type": "Polygon", "coordinates": _square(210, 10, 10)}, "gf"),
    ]
    path = tmp_path / "merged_parent.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))

    gdf, _points, report = geojson.read_and_qc(str(path))
    grouped, _report = parent_annotations.group_by_parents(gdf, ["P1"], report.multipolygons)
    assert list(grouped[CLASS_NAME]) == ["P1_gf", "P1_gf"], (
        "A patient parent annotation merged in QuPath grouped nothing after reading; that patient could not be collected."
    )


def test_demo_file_groups_as_the_readme_says():
    """The README's parent annotations vignette walks through this file; its numbers must stay true."""
    gdf, points, report = geojson.read_and_qc(PARENTS_FILE)
    grouped, parent_report = parent_annotations.group_by_parents(gdf, ["P1", "P2"], report.multipolygons)
    assert len(points) == 3, "The demo needs three named calibration points to get through Stage 1."
    assert sorted(set(grouped[CLASS_NAME])) == [
        "P1_Gland", "P1_Stroma", "P1_Vessel", "P2_Gland", "P2_Stroma", "P2_Vessel",
    ], "The demo no longer gives the six samples the README promises."
    assert len(grouped) == 18 and parent_report.n_parents == 2, (
        f"The README says 2 parent annotations group 18 shapes; got {parent_report.n_parents} and {len(grouped)}."
    )
    assert _left_out_reasons(parent_report) == {"Gland": parent_annotations.NO_PARENT}, (
        "The README says one Gland between the two pieces is left out; the demo no longer shows that warning."
    )
