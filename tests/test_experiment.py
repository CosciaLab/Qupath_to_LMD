"""Experiments over several plates, and the download that cuts them."""

import io
import zipfile

import pytest

from qupath_to_lmd import budget, export, plate, qc, selection, slides
from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.plate import PlateDistribution
from tests.conftest import CELLS_FILE, MULTICLASS_FILE

GROUPS = budget.group_keys([ClassBudget(name, 3, 1) for name in ("Immune", "Stroma", "Tumor")])
SIX_WELLS = plate.acceptable_wells("96", margins=3, step_col=2)


def test_six_wells_fixture_is_what_the_tests_assume():
    assert len(SIX_WELLS) == 6


@pytest.mark.parametrize(
    ("samples", "first", "expected"), [(6, None, 1), (7, None, 2), (12, None, 2), (13, None, 3), (6, 2, 2)]
)
def test_plates_needed(samples, first, expected):
    assert plate.plates_needed(samples, 6, first) == expected, (
        f"{samples} samples on 6-well plates (first plate {first}) need {expected} plates. A wrong "
        "count either drops samples or asks the user for a plate they will not fill."
    )


@pytest.mark.parametrize("randomize", [False, True])
def test_one_plate_is_exactly_the_layout_the_app_always_made(randomize):
    wells = plate.acceptable_wells("384", margins=1)
    old = plate.assign_wells(GROUPS, plate.wells_from(wells, "C5"), randomize=randomize, seed=4)
    new = plate.assign_to_plates(GROUPS, wells, 1, randomize=randomize, seed=4, start_well="C5")
    assert new == {group: ("Plate1", well) for group, well in old.items()}, (
        "With one plate the assignment changed. Every single-plate collection would land in "
        "different wells from the ones it lands in today."
    )


def test_balanced_puts_every_class_on_every_plate():
    """Filling plate 1 first would put whole classes on one plate, so a plate effect would read
    as a difference between classes."""
    assignment = plate.assign_to_plates(GROUPS, SIX_WELLS, 2, PlateDistribution.BALANCED)
    for name in ("Plate1", "Plate2"):
        classes = {group.rsplit("_r", 1)[0] for group, (where, _) in assignment.items() if where == name}
        assert classes == {"Immune", "Stroma", "Tumor"}, (
            f"Plate {name} holds only {sorted(classes)}. Balanced plates must each hold every class "
            "when there are at least as many replicates as plates."
        )
    counts = [sum(where == name for where, _ in assignment.values()) for name in ("Plate1", "Plate2")]
    assert max(counts) - min(counts) <= 1, f"Balanced plates hold {counts} samples — not balanced."


def test_sequential_fills_the_first_plate_before_the_next():
    assignment = plate.assign_to_plates(GROUPS, SIX_WELLS, 2, PlateDistribution.SEQUENTIAL)
    on_first = [group for group, (where, _) in assignment.items() if where == "Plate1"]
    assert on_first == sorted(GROUPS)[:6], f"Sequential put {on_first} on Plate1 instead of the first six."


@pytest.mark.parametrize("distribution", list(PlateDistribution))
def test_no_well_is_used_twice_on_a_plate_and_nothing_vanishes(distribution):
    assignment = plate.assign_to_plates(GROUPS, SIX_WELLS, 2, distribution)
    assert set(assignment) == set(GROUPS), (
        f"{set(GROUPS) - set(assignment)} were dropped although two plates have room for all 9."
    )
    positions = list(assignment.values())
    assert len(positions) == len(set(positions)), "Two samples share a well on the same plate."


@pytest.mark.parametrize("distribution", list(PlateDistribution))
def test_samples_beyond_every_plate_are_left_out_for_the_caller_to_report(distribution):
    assignment = plate.assign_to_plates(GROUPS, SIX_WELLS[:4], 2, distribution)
    assert len(assignment) == 8, (
        f"Two 4-well plates took {len(assignment)} of 9 samples. They hold 8; the ninth must be "
        "absent so the app can name it, not squeezed into a used well."
    )


@pytest.fixture
def experiment():
    """Two different slides, three classes between them, three replicates — nine samples on
    two six-well plates."""
    read = slides.read_slides([CELLS_FILE, MULTICLASS_FILE])
    pools = {slide.name: slide.gdf for slide in read}
    scales = dict.fromkeys(pools)
    classes = ["Immune cells", "Tumor", "single_cells_demo"]
    budgets = [ClassBudget(name, 3, 2) for name in classes]
    pooled = slides.select_across_slides(pools, budgets, BudgetMode.CELLS, selection.SelectionParams(), scales)
    assignment = plate.assign_to_plates(budget.group_keys(budgets), SIX_WELLS, 2)
    calibration = {}
    for slide in read:
        names = list(slide.calibration_points)[:3]
        calibration[slide.name] = (names, qc.triangle_qc(slide.gdf, slide.calibration_points, names).calibration_array)
    samples = slides.selected_samples(pools, pooled, budgets, BudgetMode.CELLS, scales)
    cuts = slides.cuts_for_experiment(samples, read, assignment, calibration, plate="96")
    return cuts, pooled, assignment


def test_every_slide_and_plate_with_something_to_cut_gets_one_xml(experiment):
    cuts, _pooled, _assignment = experiment
    pairs = sorted((cut.slide, cut.plate) for cut in cuts)
    assert pairs == [
        ("Single_cells", "Plate1"), ("Single_cells", "Plate2"), ("multiclass_cells", "Plate1"), ("multiclass_cells", "Plate2"),
    ], f"Expected one .xml per slide and plate, got {pairs}."
    for cut in cuts:
        assert set(cut.plan.wells_used) <= {well for where, well in _assignment.values() if where == cut.plate}, (
            f"{cut.slide} → {cut.plate} cuts into wells that belong to another plate."
        )


@pytest.mark.parametrize(
    ("order", "folder"), [(export.CutOrder.BY_SLIDE, "slide_Single_cells/"), (export.CutOrder.BY_PLATE, "Plate1/")]
)
def test_the_download_follows_the_cutting_order(experiment, order, folder):
    cuts, pooled, assignment = experiment
    bundle = export.build_experiment_bundle(
        cuts, pooled.by_sample(), plate.per_plate(assignment), plate="96", order=order
    )
    names = zipfile.ZipFile(io.BytesIO(bundle.getvalue())).namelist()
    xmls = [name for name in names if name.endswith(".xml")]
    assert len(xmls) == 4 and any(name.startswith(folder) for name in xmls), (
        f"Cutting {order.value} by {order.value} should put the files under {folder}…, got {xmls}."
    )
    for expected in ("samples.csv", "Plate1.csv", "Plate2.csv", "COLLECTION_PLAN.txt", "provenance.json"):
        assert expected in names, f"{expected} is missing from the download."

    steps = zipfile.ZipFile(io.BytesIO(bundle.getvalue())).read("COLLECTION_PLAN.txt").decode()
    for name in xmls:
        assert name in steps, f"COLLECTION_PLAN.txt never tells the user to import {name}."


def test_a_slide_with_nothing_for_a_plate_gets_no_file():
    read = slides.read_slides([CELLS_FILE, MULTICLASS_FILE])
    pools = {slide.name: slide.gdf for slide in read}
    scales = dict.fromkeys(pools)
    budgets = [ClassBudget("single_cells_demo", 1, 2), ClassBudget("Tumor", 1, 2)]
    pooled = slides.select_across_slides(pools, budgets, BudgetMode.CELLS, selection.SelectionParams(), scales)
    assignment = plate.assign_to_plates(budget.group_keys(budgets), SIX_WELLS, 2, PlateDistribution.SEQUENTIAL)
    calibration = {
        slide.name: (list(slide.calibration_points)[:3],
                     qc.triangle_qc(slide.gdf, slide.calibration_points, list(slide.calibration_points)[:3]).calibration_array)
        for slide in read
    }
    samples = slides.selected_samples(pools, pooled, budgets, BudgetMode.CELLS, scales)
    cuts = slides.cuts_for_experiment(samples, read, assignment, calibration, plate="96")
    assert len(cuts) == 2, (
        f"Each class exists on one slide only, so two .xml files cut something; got {len(cuts)}. An "
        "empty file would make the user mount a slide to cut nothing."
    )


def test_an_experiment_download_loads_back_into_the_plates_stage(experiment):
    """`samples_and_wells.json` from a download is how a user returns to change a plate later."""
    import json

    _cuts, _pooled, assignment = experiment
    written = json.dumps(plate.per_plate(assignment))
    reloaded = plate.assignment_from_scheme(plate.parse_saw_file(io.StringIO(written)))
    assert reloaded == assignment, (
        "The samples-and-wells file of a two-plate download did not load back as the same plates "
        "and wells, so the experiment could not be reopened for changes."
    )


def test_a_one_plate_file_still_loads_as_plate_one():
    assert plate.assignment_from_scheme({"Tumor": "C3"}) == {"Tumor": ("Plate1", "C3")}


@pytest.mark.parametrize("bad", [{"P1": {"Tumor": "C3"}, "Stroma": "C5"}, {"plate one": {"Tumor": "C3"}}])
def test_a_malformed_plate_file_is_refused_with_a_reason(bad):
    with pytest.raises(plate.SawParseError):
        plate.assignment_from_scheme(bad)
