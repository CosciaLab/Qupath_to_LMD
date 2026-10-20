"""Plate geometry, well assignment, and reading layouts back."""

import io

import pytest

from qupath_to_lmd import plate
from tests.conftest import SAW_FILE


def test_margin_excludes_the_outer_wells():
    """The LMD7 collects unreliably into the outermost wells of a 384 plate.

    A margin that did not actually exclude them would send tissue to wells that lose it.
    """
    full = plate.acceptable_wells("384", margins=0)
    trimmed = plate.acceptable_wells("384", margins=2)
    assert len(full) == 384, f"A 384 plate should offer 384 wells at margin 0, got {len(full)}."
    assert "A1" in full and "A1" not in trimmed, "Margin 2 still offers A1, which the LMD7 collects poorly."
    assert "P24" in full and "P24" not in trimmed
    assert set(trimmed) < set(full), "Trimmed wells are not a subset of the full plate."


def test_spacing_leaves_blanks_between_samples():
    """Row and column steps exist so a pipette can reach a well without touching its neighbour."""
    spaced = plate.acceptable_wells("384", margins=0, step_row=2, step_col=2)
    assert "A1" in spaced and "A2" not in spaced, (
        f"Column spacing of 2 should skip A2; wells start {spaced[:4]}."
    )
    assert "B1" not in spaced, "Row spacing of 2 should skip row B."


@pytest.mark.parametrize(("plate_type", "expected"), [("384", 384), ("96", 96)])
def test_both_supported_plates_have_the_right_well_count(plate_type, expected):
    assert len(plate.acceptable_wells(plate_type, margins=0)) == expected


def test_an_unsupported_plate_is_rejected():
    """Silently defaulting to 384 would put tissue in wells that do not exist on the real plate."""
    with pytest.raises(ValueError, match="Collector must be one of"):
        plate.plate_dimensions("1536")


def test_assignment_is_deterministic_so_a_rerun_is_stable():
    """Streamlit re-runs the script constantly; an unstable assignment would move tissue
    between wells while the user watched."""
    groups = ["Tumor_r1", "Tumor_r2", "Immune_r1"]
    wells = plate.acceptable_wells("384", margins=1)
    first = plate.assign_wells(groups, wells)
    assert first == plate.assign_wells(groups, wells), (
        "Two identical calls gave different well assignments, so a rerun would move samples."
    )
    assert sorted(first) == sorted(groups), "Not every group was assigned a well."


def test_randomized_assignment_is_seeded_and_reproducible():
    """Randomizing guards against plate-position effects, but an unseeded shuffle could never
    be reported in a methods section."""
    groups = [f"g{i}" for i in range(5)]
    wells = plate.acceptable_wells("384", margins=1)
    ordered = plate.assign_wells(groups, wells)
    one = plate.assign_wells(groups, wells, randomize=True, seed=1)
    one_again = plate.assign_wells(groups, wells, randomize=True, seed=1)
    two = plate.assign_wells(groups, wells, randomize=True, seed=2)

    assert one == one_again, "The same seed gave a different layout, so a randomized run is not reproducible."
    assert one != two, "Different seeds gave the same layout, so the seed is being ignored."
    assert one != ordered, "Randomizing produced the ordered layout, so it did nothing."
    assert set(one.values()) <= set(wells), "Randomizing assigned a well outside the usable set."


def test_more_groups_than_wells_are_reported_not_dropped():
    """Silently losing a group means a sample the user asked for never gets cut."""
    groups = [f"g{i}" for i in range(10)]
    assignment = plate.assign_wells(groups, ["B2", "B3"])
    assert len(assignment) == 2, f"Only two wells were offered, so only two groups can be placed; got {len(assignment)}."
    unplaced = set(groups) - set(assignment)
    assert len(unplaced) == 8, "The caller must be able to see which groups did not fit."


def test_layout_round_trips_through_the_saw_dictionary():
    """The plate table the user sees and the dictionary the export uses must agree."""
    layout, unplaced = plate.sample_layout(["A", "B", "C"], plate="96", wells=["A1", "A2", "A3"])
    assert unplaced == []
    recovered = plate.layout_to_saw(layout)
    assert recovered == {"A": "A1", "B": "A2", "C": "A3"}, (
        f"Reading the layout back gave {recovered}, which does not match what was placed."
    )


def test_saw_file_parses_a_python_dict_literal():
    """The documented format is a Python dict literal, trailing commas and all."""
    parsed = plate.parse_saw_file(SAW_FILE)
    assert parsed["class_1"] == "C3", f"Expected class_1 in C3, got {parsed.get('class_1')}."
    assert len(parsed) == 4


@pytest.mark.parametrize(
    ("content", "why"),
    [
        ('{"a": "C3"', "a missing closing brace"),
        ("", "an empty file"),
        ("[1, 2]", "a list instead of a dictionary"),
        ("{}", "an empty dictionary"),
    ],
)
def test_a_broken_saw_file_raises_rather_than_returning_nothing(content, why):
    """It used to return `{}` and log, so the app reported success for an empty scheme —
    the user would then process a collection with no wells assigned."""
    with pytest.raises(plate.SawParseError):
        plate.parse_saw_file(io.BytesIO(content.encode()))


def test_placement_table_puts_each_group_in_its_well():
    """This table becomes the CSV in the download bundle, which is what the user pipettes from."""
    table = plate.placement_dataframe({"Tumor": "C3", "Immune": "D5"}, plate="384")
    assert table.at["C", "3"] == "Tumor", f"Expected Tumor at C3, found {table.at['C', '3']!r}."
    assert table.at["D", "5"] == "Immune"
    assert table.at["A", "1"] == "", "Unused wells should be blank, not carry a stale name."


def test_wells_start_from_the_requested_one():
    """For collecting several slides into one plate: run the first from B2, note where it
    ended, then start the next after it."""
    wells = plate.acceptable_wells("384", margins=1)
    assert wells[0] == "B2", f"Fixture assumption broken: margin 1 should start at B2, got {wells[0]}."

    from_b10 = plate.wells_from(wells, "B10")
    assert from_b10[0] == "B10", f"Expected filling to start at B10, got {from_b10[0]}."
    assert len(from_b10) == len(wells) - wells.index("B10")
    assert set(from_b10) <= set(wells), "Starting later offered a well outside the usable set."


def test_a_blank_start_well_changes_nothing():
    wells = plate.acceptable_wells("384", margins=1)
    assert plate.wells_from(wells, None) == wells
    assert plate.wells_from(wells, "") == wells


def test_an_unusable_start_well_degrades_to_the_beginning():
    """A typo should not silently collect nothing, and A1 is excluded by a margin."""
    wells = plate.acceptable_wells("384", margins=1)
    for bad in ("A1", "ZZ99", "not a well"):
        assert plate.wells_from(wells, bad) == wells, (
            f"Start well {bad!r} is unusable, so filling should begin at the start rather than "
            "producing an empty or partial plate."
        )


def test_the_start_well_is_case_insensitive():
    wells = plate.acceptable_wells("384", margins=1)
    assert plate.wells_from(wells, "b10")[0] == "B10", "A lowercase well name was not recognised."


def test_starting_later_leaves_room_for_a_second_slide():
    """The multi-slide case end to end: two runs into one plate must not collide."""
    wells = plate.acceptable_wells("384", margins=1)
    first = plate.assign_wells([f"slide1_r{i}" for i in range(1, 5)], wells)
    last_used = max(first.values(), key=lambda w: (w[0], int(w[1:])))
    remaining = plate.wells_from(wells, wells[wells.index(last_used) + 1])
    second = plate.assign_wells([f"slide2_r{i}" for i in range(1, 5)], remaining)

    overlap = set(first.values()) & set(second.values())
    assert not overlap, (
        f"Two slides collected into the same plate reused wells {overlap}, which would mix samples."
    )
