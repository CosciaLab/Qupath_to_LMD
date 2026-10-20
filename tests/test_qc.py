"""Quality checks. These decide whether a collection is safe to make at all."""

import numpy
import pytest

from qupath_to_lmd import qc


def test_triangle_coverage_is_reported(cells):
    """Shapes far outside the calibration triangle are distorted by the coordinate transform,
    so the fraction inside is the user's warning that they may cut the wrong tissue."""
    gdf, points, _report = cells
    report = qc.triangle_qc(gdf, points, list(points)[:3])
    assert 0 < report.fraction_inside <= 1, f"Coverage came out as {report.fraction_inside}."
    assert report.n_shapes == len(gdf)
    assert not report.is_degenerate, "A valid three-point triangle was reported as degenerate."


@pytest.mark.parametrize(
    ("corners", "why"),
    [
        ([[0, 0], [0, 0], [0, 0]], "the same point three times"),
        ([[0, 0], [100, 0], [100, 0]], "one point repeated"),
        ([[0, 0], [50, 50], [100, 100]], "three collinear points"),
    ],
)
def test_degenerate_calibration_is_detected(cells, corners, why):
    """py-lmd accepts a degenerate triangle and writes a perfectly well-formed XML — no
    exception, no NaN. The user gets a file that looks correct and cuts in the wrong place,
    so this is the only place it can be caught."""
    gdf, _points, _report = cells
    points = {f"p{i}": corner for i, corner in enumerate(corners)}
    report = qc.triangle_qc(gdf, points, list(points))
    assert report.is_degenerate, (
        f"Calibration with {why} was not flagged as degenerate (triangle area "
        f"{report.triangle_area}). py-lmd will happily write a meaningless cutting file from it."
    )


def test_a_missing_calibration_point_raises(cells):
    gdf, points, _report = cells
    with pytest.raises(KeyError, match="not found"):
        qc.triangle_qc(gdf, points, ["calib1", "calib2", "nope"])


def test_wells_are_validated_against_the_chosen_plate():
    """Validating against a hard-coded 384 grid let `K5` pass on a 96-well plate, which has no
    row K — the tissue would go nowhere."""
    on_96 = qc.validate_saw({"a": "K5", "b": "C3"}, ["a", "b"], plate="96")
    on_384 = qc.validate_saw({"a": "K5", "b": "C3"}, ["a", "b"], plate="384")
    assert on_96.invalid_wells == {"K5"}, (
        f"K5 does not exist on a 96 plate but was accepted; invalid wells reported: {on_96.invalid_wells}."
    )
    assert not on_384.invalid_wells, "K5 exists on a 384 plate and should be accepted there."
    assert not on_96.is_usable, "A scheme naming a non-existent well must not be usable."


def test_classes_missing_from_the_scheme_are_reported():
    """These shapes will not be collected. In the annotations workflow that usually means the
    user forgot a class, so it has to be visible."""
    report = qc.validate_saw({"a": "C3"}, ["a", "b", "c"], plate="384")
    assert report.missing_classes == {"b", "c"}, (
        f"Expected b and c reported as absent from the scheme, got {report.missing_classes}."
    )
    assert report.is_usable, "Missing classes are a warning, not a blocker (decisions.md 003)."


def test_two_classes_in_one_well_are_reported():
    """Two classes sharing a well mixes two samples, which is almost never intended."""
    report = qc.validate_saw({"a": "C3", "b": "C3"}, ["a", "b"], plate="384")
    assert report.duplicate_wells == {"C3": ["a", "b"]}, (
        f"Expected C3 flagged as receiving two classes, got {report.duplicate_wells}."
    )


@pytest.mark.parametrize(
    ("entered", "concerning", "why"),
    [
        (0.3467, False, "the value this file implies"),
        (3.467, True, "a factor-of-ten slip"),
        (0.65, True, "a plausible but wrong value from another microscope"),
        (0.3500, False, "within a percent of the implied value"),
    ],
)
def test_pixel_size_mismatches_are_flagged(entered, concerning, why):
    """A wrong scale gives a correct-looking collection of the wrong amount of tissue, and a
    2x error in scale is a 4x error in every area."""
    report = qc.compare_pixel_size(entered, implied_um_per_px=0.3467, n_objects=121, relative_spread=0.002)
    assert report.is_concerning is concerning, (
        f"Entering {entered} µm/px against an implied 0.3467 ({why}) was "
        f"{'not ' if concerning else ''}flagged; ratio was {report.ratio:.2f}x."
    )


def test_pixel_size_check_degrades_when_there_is_nothing_to_compare():
    """Most exports carry no measurements, so this is the normal case and must not look like
    an error."""
    report = qc.compare_pixel_size(0.5, implied_um_per_px=None)
    assert report.implied_um_per_px is None
    assert report.ratio is None
    assert not report.is_concerning, "With nothing to compare against, nothing can be concerning."


def test_triangle_report_survives_having_no_shapes(cells):
    """A file whose shapes were all dropped still has to produce a report rather than divide
    by zero."""
    gdf, points, _report = cells
    empty = gdf.iloc[:0]
    report = qc.triangle_qc(empty, points, list(points)[:3])
    assert report.fraction_inside == 0.0
    assert not report.is_concerning, "An empty frame should not raise a distortion warning."
    assert isinstance(report.calibration_array, numpy.ndarray)


ISSUE_25_NAME = (
    "GEOJSON file_01_XP24_sm30BL3_tiles25x19_20x_entiere lung_with stiching 10% maxshift and % "
    "minimal overlap.geojson"
)


def test_the_file_name_from_issue_25_is_flagged():
    """The name a user really uploaded: it becomes paths in the download long enough that Windows
    will not unzip them, so the cutting file can go missing on the LMD computer."""
    report = qc.file_name_qc(ISSUE_25_NAME)
    assert report.too_long, f"A {len(report.stem)}-character name was not flagged as too long."
    assert report.unusual_characters == ["%"], (
        f"`%` was not reported, so the user is not told which character to remove: {report.unusual_characters}"
    )
    assert report.is_concerning


@pytest.mark.parametrize(
    "name", ["TD_01_verysmall_mIF.geojson", "P1_S1.geojson", "Lung slide 3 (repeat).geojson", "core-A1.v2.geojson"]
)
def test_ordinary_file_names_are_not_flagged(name):
    """Warning on every upload is how warnings stop being read."""
    report = qc.file_name_qc(name)
    assert not report.is_concerning, f"{name!r} was flagged: {report}"


@pytest.mark.parametrize(
    "name", [ISSUE_25_NAME, "file_02 lung (copy) #2 & ü.geojson", " padded .geojson", "%%%.geojson", "a" * 90 + ".geojson"]
)
def test_the_suggested_name_passes_its_own_check(name):
    """The warning tells the user what to rename the file to; a suggestion that would itself be
    flagged sends them round in a circle."""
    suggestion = qc.file_name_qc(name).suggestion
    assert suggestion, f"No name was suggested for {name!r}."
    assert not qc.file_name_qc(suggestion + ".geojson").is_concerning, (
        f"The name suggested for {name!r}, {suggestion!r}, is flagged by the same check."
    )


def test_the_longest_safe_name_keeps_the_download_inside_the_windows_path_limit():
    """Three copies of the slide name sit in the longest path of an experiment download, under a
    Downloads folder; at the limit that path must still fit in Windows' 260 characters."""
    name = "n" * qc.LONGEST_SAFE_FILE_NAME
    downloads = "C:\\Users\\lmd-operator\\Downloads\\"
    longest = f"{downloads}{name}_experiment\\slide_{name}\\{name}__TubeHolder10.xml"
    assert len(longest) <= 260, (
        f"A {qc.LONGEST_SAFE_FILE_NAME}-character name gives a {len(longest)}-character path once "
        "unzipped, so a name the app calls safe could still lose its .xml on Windows."
    )


def test_spellings_of_one_class_are_grouped_across_slides():
    """`Tumor` on one slide and `tumor` on another never pool; the user has to be told."""
    groups = qc.similar_class_names({"A": ["Tumor", "Immune cells", "Stroma"], "B": ["tumor", "Immune_cells", "Stroma"]})
    assert groups == [{"Immune cells": ["A"], "Immune_cells": ["B"]}, {"Tumor": ["A"], "tumor": ["B"]}], (
        f"Two spellings of one class were not grouped by slide: {groups}"
    )


def test_distinct_classes_are_not_called_similar():
    assert not qc.similar_class_names({"A": ["Tumor", "Tumor_core", "T cell", "B cell", "Immune cells--Tumor"]})
