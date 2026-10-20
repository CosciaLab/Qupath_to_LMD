"""Collectors other than plates: the Eppendorf tube holder and the 8-well strip holder.

A tube holder or a strip holder plays the part of a plate. What goes wrong when these break is
always the same at the microscope: tissue sent to a cap that does not exist, or to the wrong one.
"""

import io
import string
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pandas
import pytest

from qupath_to_lmd import export, geojson, plate, qc
from qupath_to_lmd.model import CLASS_NAME, plan_from_class_wells

TD_01 = Path(__file__).resolve().parent.parent / "demo_Qupath_project" / "TD_01_verysmall_mIF.geojson"


def _old_acceptable_wells(plate_type, margins=0, step_row=1, step_col=1):
    """`acceptable_wells` as it was before collectors, for plates only."""
    max_row, max_col = {"384": (16, 24), "96": (8, 12)}[plate_type]
    min_row, min_col = 1, 1
    if margins > 0:
        max_row -= margins
        max_col -= margins
        min_row += margins
        min_col += margins
    return [
        f"{row}{column}"
        for row in string.ascii_uppercase[min_row - 1 : max_row : step_row]
        for column in range(min_col, max_col + 1, step_col)
    ]


def test_tube_holder_positions_are_letters_a_to_d():
    assert plate.acceptable_wells("tubes") == ["A", "B", "C", "D"], (
        "The tube holder must offer tubes A–D and nothing else; any other CapID sends tissue to a "
        "tube the LMD does not have."
    )


def test_strip_positions_are_letters_a_to_h():
    assert plate.acceptable_wells("strip") == list("ABCDEFGH"), (
        "The strip holder must offer wells A–H of its one strip; any other CapID sends tissue to "
        "a well the LMD does not have."
    )


@pytest.mark.parametrize("key", ["tubes", "strip"])
def test_margin_and_spacing_do_not_apply_to_tubes_or_strips(key):
    assert plate.acceptable_wells(key, margins=2, step_row=2, step_col=2) == plate.acceptable_wells(key), (
        f"A margin or spacing removed positions from the {key} holder. They exist for the edge "
        "wells of a 384 plate; on a holder they would silently leave tubes unused."
    )


@pytest.mark.parametrize("plate_type", ["384", "96"])
@pytest.mark.parametrize("margins", [0, 1, 2, 3])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_plates_keep_their_wells(plate_type, margins, step):
    assert plate.acceptable_wells(plate_type, margins, step, step) == _old_acceptable_wells(
        plate_type, margins, step, step
    ), (
        f"The usable wells of a {plate_type} plate changed (margin {margins}, step {step}), so "
        "existing experiments would land in different wells than before."
    )


def test_a_position_splits_into_row_and_column():
    assert plate.split_position("C3") == ("C", 3)
    assert plate.split_position("P24") == ("P", 24)
    assert plate.split_position("C") == ("C", None), (
        "A tube position has no column; reading one in would invent a well that does not exist."
    )


def test_a_tube_holder_table_is_one_column():
    table = plate.placement_dataframe({"Tumor": "C"}, plate="tubes")
    assert list(table.index) == ["A", "B", "C", "D"]
    assert list(table.columns) == ["tube"], (
        f"The tube holder map has columns {list(table.columns)}; it should be one column of tubes, "
        "or the map in the download does not match the holder on the bench."
    )
    assert table.at["C", "tube"] == "Tumor"


def test_the_hand_editor_reads_tubes_back_as_letters():
    scheme = {"Tumor": "A", "Stroma": "D"}
    recovered = plate.layout_to_saw(plate.placement_dataframe(scheme, plate="tubes"), plate="tubes")
    assert recovered == scheme, (
        f"Editing the tube holder by hand gave {recovered}. A position like 'Atube' is no CapID, "
        "so the edited samples would be cut into nothing."
    )


def test_a_start_tube_is_case_insensitive_and_a_plate_well_is_ignored():
    tubes = plate.acceptable_wells("tubes")
    assert plate.wells_from(tubes, "c") == ["C", "D"], "Typing 'c' should start the holder at tube C."
    assert plate.wells_from(tubes, "C3") == tubes, (
        "A plate well typed as the start of a tube holder must fall back to the first tube, not "
        "collect nothing."
    )


def test_positions_that_do_not_exist_on_the_collector_are_invalid():
    tubes = qc.validate_saw({"a": "C3", "b": "E"}, [], plate="tubes")
    assert tubes.invalid_wells == {"C3", "E"}, (
        f"On a tube holder only A–D exist; {tubes.invalid_wells} were flagged. Unflagged, tissue "
        "goes to a cap the holder does not have."
    )
    strip = qc.validate_saw({"a": "I", "b": "A1"}, [], plate="strip")
    assert strip.invalid_wells == {"I", "A1"}, (
        f"On a strip only A–H exist; {strip.invalid_wells} were flagged."
    )


def test_an_unknown_collector_is_refused():
    with pytest.raises(ValueError):
        plate.collector("1536")


def test_collectors_are_named_explicitly():
    assert plate.plate_names(2, "384") == ["Plate1", "Plate2"]
    assert plate.plate_names(1, "tubes") == ["TubeHolder1"]
    assert plate.plate_names(2, "strip") == ["Strip1", "Strip2"], (
        "Collector names go into file names and COLLECTION_PLAN.txt; a vague name is how a user "
        "loads the wrong holder."
    )


def test_thirteen_samples_fill_four_balanced_tube_holders():
    groups = (
        [f"Tumor_r{n}" for n in range(1, 6)]
        + [f"Stroma_r{n}" for n in range(1, 5)]
        + [f"Immune_r{n}" for n in range(1, 5)]
    )
    tubes = plate.acceptable_wells("tubes")
    assignment = plate.assign_to_plates(groups, tubes, plate.plates_needed(len(groups), len(tubes)), plate="tubes")
    assert set(assignment) == set(groups), f"Samples without a tube: {set(groups) - set(assignment)}"
    holders = {holder for holder, _ in assignment.values()}
    assert holders == {"TubeHolder1", "TubeHolder2", "TubeHolder3", "TubeHolder4"}, holders
    assert {tube for _, tube in assignment.values()} <= set("ABCD"), (
        "A sample was given a position the tube holder does not have."
    )
    per_holder = [sum(where == holder for where, _ in assignment.values()) for holder in holders]
    assert max(per_holder) <= 4, f"A holder was given more than four tubes: {per_holder}"


@pytest.mark.parametrize("key", ["384", "96", "tubes", "strip"])
def test_collector_names_round_trip_through_a_file(key):
    positions = plate.acceptable_wells(key)
    groups = [f"Tumor_r{n}" for n in range(1, len(positions) + 3)]
    assignment = plate.assign_to_plates(groups, positions, 2, plate=key)
    reloaded = plate.assignment_from_scheme(plate.per_plate(assignment), plate=key)
    assert reloaded == assignment, (
        f"The samples-and-wells file of a two-collector {key} download did not load back as the "
        "same collectors and positions, so the experiment could not be reopened for changes."
    )


def test_ten_plates_sort_after_two():
    schemes = plate.per_plate({"x": ("Plate10", "A1"), "y": ("Plate2", "A1")})
    assert list(schemes) == ["Plate2", "Plate10"], (
        f"Collectors came out as {list(schemes)}; instructions and tabs would list them out of order."
    )


def test_an_old_p1_file_loads_as_plate1():
    loaded = plate.assignment_from_scheme({"P1": {"T": "C3"}, "P2": {"S": "C3"}}, plate="384")
    assert loaded == {"T": ("Plate1", "C3"), "S": ("Plate2", "C3")}, (
        f"An experiment downloaded before collectors existed loaded as {loaded}; it must reopen as "
        "the same plates."
    )


def test_a_flat_file_goes_to_the_first_collector():
    assert plate.assignment_from_scheme({"T": "C"}, plate="tubes") == {"T": ("TubeHolder1", "C")}


@pytest.mark.parametrize(
    ("parsed", "chosen", "names"),
    [
        ({"TubeHolder1": {"T": "A"}}, "384", "tube holder"),
        ({"P1": {"T": "C3"}}, "tubes", "Eppendorf tube holder"),
        ({"Plate1": {"T": "C3"}}, "strip", "8-well strip holder"),
    ],
)
def test_a_file_for_another_collector_is_refused_by_name(parsed, chosen, names):
    with pytest.raises(plate.SawParseError) as raised:
        plate.assignment_from_scheme(parsed, plate=chosen)
    message = str(raised.value)
    assert plate.collector(chosen).label in message and names in message, (
        f"Refused, but the message {message!r} does not say which collector the file is for and "
        "which is chosen, so the user cannot tell what to change."
    )


def _td01_into(plate_type):
    """TD_01's four classes into the first positions of a collector, the way the harness does it."""
    gdf, calibration_points, _report = geojson.read_and_qc(str(TD_01))
    names = list(calibration_points)[:3]
    triangle = qc.triangle_qc(gdf, calibration_points, names)
    scheme = dict(zip(sorted(set(gdf[CLASS_NAME])), plate.acceptable_wells(plate_type, margins=1), strict=False))
    plan = plan_from_class_wells(
        gdf=gdf, samples_and_wells=scheme, calibration_names=names,
        calibration_array=triangle.calibration_array, source_file=TD_01.name, session_id="test",
    )
    return plan, scheme, export.build_collection(plan, samples_and_wells=scheme, plate=plate_type)


def test_a_tube_collection_writes_letter_cap_ids():
    _plan, scheme, result = _td01_into("tubes")
    assert set(scheme.values()) == {"A", "B"}, f"TD_01's two classes should take tubes A and B: {scheme}"
    for class_name, tube in scheme.items():
        assert f"<CapID>{tube}</CapID>" in result.xml, (
            f"Tube {tube} is missing from the .xml, so {class_name} would not be collected."
        )
    assert "<CapID>A1</CapID>" not in result.xml, (
        "The tube holder .xml names a plate well; the LMD has no such cap on a tube holder."
    )


def test_a_tube_bundle_names_its_csv_for_tubes():
    plan, scheme, result = _td01_into("tubes")
    names = zipfile.ZipFile(export.build_bundle(plan, result, scheme, plate="tubes")).namelist()
    assert "TD_01_verysmall_mIF_tubes.csv" in names, f"No tube map in the download: {names}"
    plan, scheme, result = _td01_into("384")
    names = zipfile.ZipFile(export.build_bundle(plan, result, scheme, plate="384")).namelist()
    assert "TD_01_verysmall_mIF_384_wellplate.csv" in names, (
        f"The plate map of a single-plate download was renamed: {names}"
    )


def _cut(slide, holder, n_shapes=3):
    plan = SimpleNamespace(calibration_names=["c1", "c2", "c3"], wells_used=["A", "C"])
    return export.Cut(slide, holder, plan, SimpleNamespace(n_shapes=n_shapes))


def test_experiment_files_are_named_after_the_collector():
    cut = _cut("S1", "TubeHolder2")
    assert cut.path(export.CutOrder.BY_PLATE) == "TubeHolder2/TubeHolder2__S1.xml"
    assert cut.path(export.CutOrder.BY_SLIDE) == "slide_S1/S1__TubeHolder2.xml"
    for order in export.CutOrder:
        text = export.cutting_instructions([_cut("S1", "TubeHolder1"), cut], order, plate="tubes")
        assert "Load TubeHolder2" in text and "tubes A–D" in text and "2 tube holders" in text, (
            f"COLLECTION_PLAN.txt does not tell the user which tube holder to load:\n{text}"
        )
        assert "plate" not in text, f"The tube holder instructions talk about a plate:\n{text}"


def test_an_experiment_bundle_names_its_maps_after_the_collector(tmp_path):
    image = tmp_path / "c.png"
    image.write_bytes(b"")
    cuts = [export.Cut("S1", "Strip1", SimpleNamespace(
        calibration_names=["a", "b", "c"], wells_used=["A"], provenance=lambda: {},
        shapes=None,
    ), SimpleNamespace(n_shapes=1, xml="<x/>", image_path=str(image)))]
    with mock.patch.object(export, "sanitize_for_qupath", return_value=mock.MagicMock()):
        buffer = export.build_experiment_bundle(
            cuts, pandas.DataFrame({"sample": ["T"]}), {"Strip1": {"T": "A"}}, plate="strip",
        )
    names = zipfile.ZipFile(io.BytesIO(buffer.getvalue())).namelist()
    assert "Strip1.csv" in names and "slide_S1/S1__Strip1.xml" in names, (
        f"The strip download is missing its map or its .xml: {names}"
    )
