"""Behaviour the UI layer is responsible for: blocking, warning, and what it reports.

These use a stubbed Streamlit rather than a browser. They cover the decisions that protect a
collection — the hard stops, and the difference between a warning and a note.
"""

import io
import json
import pathlib

import numpy
import pytest
import streamlit
from PIL import Image
from shapely.geometry import box as shapely_box

from qupath_to_lmd import (
    budget,
    geojson,
    plate,
    plot,
    regions,
    selection,
    slides,
    ui_collect_regions,
    ui_cut,
    ui_plates,
    ui_samples,
    ui_shared,
    ui_slides,
    ui_summary,
)
from qupath_to_lmd.model import CLASS_NAME
from tests.conftest import MULTICLASS_FILE


class Stopped(Exception):
    """Raised in place of `st.stop()` so a test can tell that the app halted."""


class FakeState(dict):
    """`st.session_state` as a plain dict, supporting both attribute and item access."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as error:
            raise AttributeError(key) from error

    def __setattr__(self, key, value):
        self[key] = value


@pytest.fixture
def fake_streamlit(monkeypatch):
    """Replace the Streamlit calls the UI makes, and record what it showed.

    Returns the recorder: `.errors`, `.warnings`, `.captions`, `.infos`, `.writes`.
    """

    class Recorder:
        def __init__(self):
            self.errors, self.warnings, self.captions, self.infos, self.writes = [], [], [], [], []
            self.metrics: list[tuple[str, str]] = []
            self.tables: list[tuple[object, dict]] = []
            self.state = FakeState()

        def shown(self, kind):
            return " || ".join(getattr(self, kind))

    recorder = Recorder()

    def collect(target):
        def record(*args, **kwargs):
            target.append(str(args[0]) if args else "")

        return record

    def stop():
        raise Stopped

    class _Column:
        """A column, which is also a container the UI may write into directly."""

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def metric(self, label, value, delta=None, **kwargs):
            recorder.metrics.append((str(label), str(value)))

    monkeypatch.setattr(streamlit, "session_state", recorder.state, raising=False)
    monkeypatch.setattr(streamlit, "error", collect(recorder.errors))
    monkeypatch.setattr(streamlit, "warning", collect(recorder.warnings))
    monkeypatch.setattr(streamlit, "caption", collect(recorder.captions))
    monkeypatch.setattr(streamlit, "info", collect(recorder.infos))
    monkeypatch.setattr(streamlit, "write", collect(recorder.writes))
    monkeypatch.setattr(streamlit, "markdown", lambda *a, **k: None)
    monkeypatch.setattr(streamlit, "success", lambda *a, **k: None)
    monkeypatch.setattr(streamlit, "table", lambda *a, **k: None)
    monkeypatch.setattr(
        streamlit,
        "dataframe",
        lambda data=None, **k: recorder.tables.append((data, k.get("column_config") or {})),
    )
    monkeypatch.setattr(streamlit, "stop", stop)
    monkeypatch.setattr(
        streamlit, "metric", lambda label, value, delta=None, **k: recorder.metrics.append((str(label), str(value)))
    )
    monkeypatch.setattr(streamlit, "columns", lambda spec, **k: tuple(_Column() for _ in (spec if hasattr(spec, "__len__") else range(spec))))
    monkeypatch.setattr(streamlit, "selectbox", lambda label, options, index=0, **k: options[index] if index < len(options) else options[0])
    return recorder


def _load(fake_streamlit, path, keep_points=3):
    """Put a file into the fake session, optionally dropping calibration points."""
    import json
    import tempfile

    document = json.loads(open(path).read())
    points = [f for f in document["features"] if f["geometry"]["type"] == "Point"][:keep_points]
    document["features"] = points + [f for f in document["features"] if f["geometry"]["type"] != "Point"]
    handle = tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False)
    json.dump(document, handle)
    handle.close()

    (slide,) = slides.read_slides([handle.name])
    fake_streamlit.state.update(pixel_size_by_slide={})
    return slide


def _context(*loaded):
    """A slides context with each slide calibrated on its first three points."""
    calibration = {}
    for slide in loaded:
        names = list(slide.calibration_points)[:3]
        calibration[slide.name] = (names, None)
    return ui_slides.SlidesContext(slides=list(loaded), calibration=calibration)


def _layout(assignment, plate_type="384"):
    return ui_plates.PlateLayout(plate_type, {"margins": 1, "step_row": 1, "step_col": 1, "randomize": False}, assignment)


@pytest.mark.parametrize("kept", [0, 1, 2])
def test_fewer_than_three_calibration_points_stops_the_app(fake_streamlit, kept):
    """Without three points no cutting file can be meaningful, so this is one of the very few
    places the app refuses to continue rather than warning."""
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson", keep_points=kept)
    with pytest.raises(Stopped):
        ui_slides.calibration_step([slide])
    assert fake_streamlit.errors, (
        f"With {kept} calibration points the app halted without explaining why. The user needs "
        "to be told to add points in QuPath."
    )
    assert "calibration point" in fake_streamlit.shown("errors").lower()


def test_a_degenerate_calibration_triangle_stops_the_app(fake_streamlit, monkeypatch):
    """py-lmd writes a valid-looking XML from three identical points, so nothing downstream
    would catch it."""
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    coordinate = next(iter(slide.calibration_points.values()))
    slide.calibration_points = dict.fromkeys(("a", "b", "c"), coordinate)
    monkeypatch.setattr(streamlit, "selectbox", lambda label, options, index=0, **k: options[index])

    with pytest.raises(Stopped):
        ui_slides.calibration_step([slide])
    assert "triangle" in fake_streamlit.shown("errors").lower(), (
        f"A degenerate triangle should be explained as such; errors were: {fake_streamlit.errors}"
    )


def test_three_valid_points_do_not_stop_the_app(fake_streamlit):
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    calibration = ui_slides.calibration_step([slide])
    assert not fake_streamlit.errors, f"A valid calibration raised errors: {fake_streamlit.errors}"
    assert calibration[slide.name][1] is not None


def test_a_hard_stop_on_one_of_several_slides_names_that_slide(fake_streamlit, monkeypatch):
    """With several slides, "this file has no calibration points" does not say which file."""
    good = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    bad = _load(fake_streamlit, "demo_Qupath_project/TD_01_verysmall_mIF.geojson", keep_points=1)
    bad.name = "slide_B"

    monkeypatch.setattr(streamlit, "tabs", lambda names: [streamlit.container() for _ in names])
    with pytest.raises(Stopped):
        ui_slides.calibration_step([good, bad])
    assert "slide_B" in fake_streamlit.shown("errors"), (
        f"The stop did not name the slide lacking points: {fake_streamlit.errors}"
    )


def test_a_large_file_warns_about_running_locally(fake_streamlit):
    """A whole-slide export needs more memory than the hosted app has, so the user should be
    told before spending ten minutes finding out."""
    shapes = ui_shared.HOSTED_COMFORTABLE_SHAPES + 1
    ui_shared.report_scale(shapes)
    assert fake_streamlit.warnings, "A file above the comfortable threshold produced no warning."

    shown = fake_streamlit.shown("warnings")
    # Assert on content the user needs, not on particular wording.
    assert f"{shapes:,}" in shown, f"The warning does not say how many shapes the file has: {shown[:200]}"
    assert "uv run streamlit run" in shown, (
        "The warning offers no command for running the app locally, which is the actual advice."
    )
    assert str(ui_shared.HOSTED_MEMORY_CEILING_MB) in shown.replace(",", ""), (
        "The warning does not state the hosted memory ceiling, so the user cannot judge the risk."
    )
    assert any(str(n) in shown.replace(",", "") for n, *_ in ui_shared.SCALE_BENCHMARKS), (
        "The warning shows none of the measured benchmarks, so the numbers are not there to compare against."
    )


def test_a_small_file_does_not_warn(fake_streamlit):
    """Warning on the ordinary case is how warnings stop being read."""
    ui_shared.report_scale(ui_shared.HOSTED_COMFORTABLE_SHAPES - 1)
    assert not fake_streamlit.warnings, (
        f"A file below the threshold warned anyway: {fake_streamlit.warnings}"
    )


def test_unselected_shapes_are_a_note_in_the_cell_workflow(fake_streamlit, cells):
    """Most shapes are deliberately not selected — that is the point of the workflow. Warning
    about it fired on every single collection and trained users to ignore warnings."""
    gdf, _points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    budgets = [budget.ClassBudget(classes[0], 1, 3)]
    pooled = slides.select_across_slides(
        {"A": gdf}, budgets, budget.BudgetMode.CELLS, selection.SelectionParams(seed=1), {"A": 0.3467}
    )
    sample_set = slides.selected_samples({"A": gdf}, pooled, budgets, budget.BudgetMode.CELLS, {"A": 0.3467})
    layout = _layout(plate.assign_to_plates(sample_set.samples, plate.acceptable_wells("384", margins=1)))

    ui_cut._report_excluded(sample_set, layout)
    assert fake_streamlit.captions, "Shapes not selected should be noted quietly, not warned about."
    assert not fake_streamlit.warnings, (
        f"The cell workflow warned about its own intended outcome: {fake_streamlit.warnings}"
    )
    assert "as intended" in fake_streamlit.shown("captions")


def test_classes_left_out_warn_when_collecting_whole_shapes(fake_streamlit, cells):
    """There it usually means the user forgot a class, which is worth interrupting for."""
    gdf, _points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    sample_set = slides.whole_shape_samples({"A": gdf}, [classes[0]], {"A": None})
    layout = _layout({classes[0]: ("P1", "C3")})
    ui_cut._report_excluded(sample_set, layout)
    assert fake_streamlit.warnings, "Classes left out of a whole-shapes collection should warn."


def test_shapes_whose_sample_got_no_well_always_warn(fake_streamlit, cells):
    """These are shapes the user asked to collect that will not be cut, in any method."""
    gdf, _points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    budgets = [budget.ClassBudget(name, 2, 2) for name in classes]
    pooled = slides.select_across_slides(
        {"A": gdf}, budgets, budget.BudgetMode.CELLS, selection.SelectionParams(seed=1), {"A": 0.3467}
    )
    sample_set = slides.selected_samples({"A": gdf}, pooled, budgets, budget.BudgetMode.CELLS, {"A": 0.3467})
    layout = _layout(plate.assign_to_plates(sample_set.samples, ["B2"]))
    ui_cut._report_excluded(sample_set, layout)
    assert any("no well" in w for w in fake_streamlit.warnings), (
        f"Samples that got no well must be warned about; warnings were: {fake_streamlit.warnings}"
    )


def test_the_workflow_suggestion_follows_the_object_types(fake_streamlit, monkeypatch):
    """A file of cells should default to the cell workflow, and annotations to the other, but
    both remain changeable because a file can contain both."""
    cells_slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    assert ui_samples._suggest(_context(cells_slide)) == "cells", (
        "A file of 121 cells should suggest selecting shapes."
    )
    annotations = _load(fake_streamlit, "demo_Qupath_project/TD_01_verysmall_mIF.geojson")
    assert ui_samples._suggest(_context(annotations)) == "legacy", (
        "A file of annotations only should suggest collecting whole shapes."
    )


def test_the_shape_fingerprint_changes_when_classes_are_exploded(fake_streamlit, cells_gdf):
    """Caches key off this. A filename alone would serve a stale selection after exploding,
    because exploding rewrites the class names in place."""
    def fingerprint(gdf, file_name):
        slide = slides.Slide("A", gdf, {}, geojson.GeojsonReport(), source_file=file_name)
        return ui_slides.SlidesContext([slide], {"A": ([], None)}).fingerprint()

    before = fingerprint(cells_gdf, "a.geojson")
    after = fingerprint(geojson.explode_classes(cells_gdf, ["single_cells_demo"]), "a.geojson")
    assert before != after, (
        "Exploding a class did not change the cache fingerprint, so a cached selection from "
        "before the explode would be reused."
    )
    assert fingerprint(cells_gdf, "b.geojson") != before, "A different file gave the same fingerprint."


def test_the_shape_fingerprint_changes_when_a_re_export_reclassifies_cells(fake_streamlit, tmp_path):
    """A classifier run again in QuPath: same file name, same cell count, same class names, but
    cells in different classes. Only the file's content tells the two exports apart."""
    swap = {"Tumor": "Immune cells", "Immune cells": "Tumor"}
    data = json.loads(pathlib.Path(MULTICLASS_FILE).read_text())
    for feature in data["features"]:
        classification = feature["properties"].get("classification") or {}
        if classification.get("name") in swap:
            classification["name"] = swap[classification["name"]]
    re_export = tmp_path / pathlib.Path(MULTICLASS_FILE).name
    re_export.write_text(json.dumps(data))

    before, = slides.read_slides([MULTICLASS_FILE])
    after, = slides.read_slides([str(re_export)])
    assert before.source_file == after.source_file and len(before.gdf) == len(after.gdf)
    fingerprints = [ui_slides.SlidesContext([slide], {}).fingerprint() for slide in (before, after)]
    assert fingerprints[0] != fingerprints[1], (
        "A re-export with cells in different classes got the same cache fingerprint, so the "
        "selection and regions cached from the old classes would be reused — cells now in one "
        "class could be cut into another class's well."
    )


def test_the_scale_is_estimated_when_the_file_allows_it(fake_streamlit):
    """The estimate has been right on every file where it could be computed, and the input was
    the step users stumbled on. So where measurements exist the app uses them and says so."""
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")

    value, source = ui_slides.resolve_pixel_size(slide)
    assert source == "estimated", (
        f"This file carries QuPath measurements, so the scale should be estimated; got {source!r}."
    )
    assert value == pytest.approx(0.3467, abs=5e-4), f"Estimated scale came out as {value}."


def test_a_typed_scale_overrides_the_estimate(fake_streamlit):
    """The estimate is a default, not a decision the app makes for the user."""
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    fake_streamlit.state.pixel_size_by_slide = {slide.name: 0.5}

    value, source = ui_slides.resolve_pixel_size(slide)
    assert (value, source) == (0.5, "entered"), (
        f"A typed scale must win over the estimate; resolver returned {value} from {source!r}."
    )


def test_no_scale_is_available_for_a_file_without_measurements(fake_streamlit):
    """Annotation exports carry no areas, so there is nothing to estimate from and the app must
    fall back to asking rather than guessing."""
    slide = _load(fake_streamlit, "demo_Qupath_project/TD_01_verysmall_mIF.geojson")

    value, source = ui_slides.resolve_pixel_size(slide)
    assert (value, source) == (None, "none"), (
        f"With no measurements there is nothing to estimate; resolver returned {value} from {source!r}."
    )


def test_a_wide_implied_spread_is_warned_about(fake_streamlit):
    """A scale that disagrees between objects suggests the export mixes images or was rescaled,
    which makes every area suspect."""
    report = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson").report
    report.pixel_size_spread = ui_shared.WIDE_SPREAD * 2

    ui_shared.report_pixel_size(0.3467, "estimated", 0.3467, report)
    assert any("varies by" in w for w in fake_streamlit.warnings), (
        f"A {report.pixel_size_spread:.0%} spread should be warned about; warnings were "
        f"{fake_streamlit.warnings}"
    )


def test_a_typed_scale_that_disagrees_with_the_file_is_warned_about(fake_streamlit):
    """A 2x error in scale is a 4x error in every area, so this is worth interrupting for."""
    report = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson").report

    ui_shared.report_pixel_size(3.467, "entered", report.implied_pixel_size_um, report)
    assert any("×" in w or "x what this file implies" in w for w in fake_streamlit.warnings), (
        f"A ten-fold disagreement should warn; warnings were {fake_streamlit.warnings}"
    )


def test_the_plate_caption_names_a_well_that_is_actually_free(fake_streamlit, monkeypatch):
    """It said "start at E7" while E7 was already in use.

    The check compared the usable wells against the assignment's *keys* — the group names —
    so nothing ever matched and it always named the first usable well. That is worse than no
    advice: following it would overwrite the slide just collected.
    """
    captions = []
    monkeypatch.setattr(streamlit, "caption", lambda *a, **k: captions.append(str(a[0]) if a else ""))
    monkeypatch.setattr(streamlit, "download_button", lambda *a, **k: None)
    monkeypatch.setattr(streamlit, "checkbox", lambda *a, **k: False)

    wells = plate.acceptable_wells("384", margins=1)
    groups = [f"slide1_r{i}" for i in range(1, 7)]
    assignment = plate.assign_wells(groups, wells)

    ui_shared.plate_preview(assignment, "384", wells=wells, key_suffix="test")

    caption = " ".join(captions)
    assert "start at" in caption, f"The caption gives no next well: {caption!r}"
    suggested = caption.split("start at")[1].strip().strip("*.").split()[0].strip("*")
    assert suggested not in set(assignment.values()), (
        f"The caption suggests starting at {suggested}, which is already in use by "
        f"{[g for g, w in assignment.items() if w == suggested]}. Following it would overwrite "
        "the slide just collected."
    )
    assert suggested in wells, f"{suggested} is not one of the usable wells."


def test_a_full_plate_says_so_rather_than_naming_a_well(fake_streamlit, monkeypatch):
    """With no wells left there is no honest answer to "where next", so it must not invent one."""
    captions = []
    monkeypatch.setattr(streamlit, "caption", lambda *a, **k: captions.append(str(a[0]) if a else ""))
    monkeypatch.setattr(streamlit, "download_button", lambda *a, **k: None)
    monkeypatch.setattr(streamlit, "checkbox", lambda *a, **k: False)

    wells = ["B2", "B3"]
    assignment = plate.assign_wells(["a", "b"], wells)
    ui_shared.plate_preview(assignment, "384", wells=wells, key_suffix="full")

    caption = " ".join(captions)
    assert "full" in caption, f"A plate with no free wells should say so; caption was {caption!r}"
    assert "start at" not in caption, "A full plate must not suggest a well to start at."






def test_a_class_with_too_few_regions_warns_and_still_continues(fake_streamlit):
    """Asking for more replicates than a class has regions cannot be satisfied.

    Warned rather than blocked: the empty replicates keep their wells, so the plate still
    matches what the user asked for and they can decide.
    """
    import geopandas
    from shapely.geometry import box

    patches = geopandas.GeoDataFrame(
        {CLASS_NAME: ["Tumor", "Tumor", "Stroma"], regions.N_CELLS: [5, 4, 3]},
        geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10)],
        crs=None,
    )
    replicates = {"Tumor": 2, "Stroma": 3}
    replicate_of = regions.deal_patches(patches, replicates)
    ui_collect_regions.report_starved_replicates({"A": patches}, replicates, {"A": replicate_of})

    shown = fake_streamlit.shown("warnings")
    assert "fewer regions than replicates" in shown, (
        "A class that cannot fill its replicates was not reported, so two of its wells would "
        "arrive empty with no warning."
    )
    assert "Stroma" in shown and "Tumor" not in shown, (
        f"The warning named the wrong classes. Shown: {shown!r}. Tumor has enough regions for "
        "its two replicates; only Stroma is short."
    )


def _region_frame():
    """The regions a packing result came from, which the report compares against."""
    import geopandas

    return geopandas.GeoDataFrame(
        {CLASS_NAME: ["Tumor"], regions.N_CELLS: [50]},
        geometry=[shapely_box(0, 0, 400, 400)],
        crs=None,
    )


def _packed(area=10_000.0, seed=0, max_attempts=None, **circle):
    """A small packing result, for the reporting functions."""
    from qupath_to_lmd import packing

    params = packing.PackingParams(
        seed=seed, **({"max_attempts": max_attempts} if max_attempts else {})
    )
    requests = [packing.ClassPacking("Tumor", 1, area, **circle)]
    return packing.pack(_region_frame(), requests, params, 1.0), params, requests


def test_a_replicate_that_could_not_be_filled_warns_and_still_exports(fake_streamlit):
    """Under-delivering silently is the one thing this app must never do.

    The user may accept a partly-filled replicate, so it warns rather than blocks
    (`decisions.md` 003).
    """
    result, _params, requests = _packed(area=10_000_000, max_attempts=150)
    ui_collect_regions.report_packing(result, requests)

    shown = fake_streamlit.shown("warnings")
    assert "could not be filled" in shown, (
        "A replicate that fell short of its requested area was not reported, so the user would "
        "believe the well holds the amount they asked for."
    )
    assert "µm²" in shown, "The shortfall was not stated as an area, so it is not actionable."


def test_a_filled_replicate_does_not_warn(fake_streamlit):
    """Warning on the ordinary case is how warnings stop being read."""
    result, _params, requests = _packed(area=2_000)
    ui_collect_regions.report_packing(result, requests)
    assert "could not be filled" not in fake_streamlit.shown("warnings"), (
        f"A fully-filled replicate warned anyway: {fake_streamlit.shown('warnings')!r}"
    )


def test_regions_too_narrow_for_a_circle_are_reported(fake_streamlit):
    """Those regions contribute nothing, and the fix is a smaller minimum circle size.

    From a circle count alone the user cannot tell that tissue was skipped.
    """
    from qupath_to_lmd import packing

    result = packing.PackingResult(n_regions_too_small=17)
    ui_collect_regions.report_packing(result, [packing.ClassPacking("Tumor", 1, 100.0)])
    shown = fake_streamlit.shown("warnings")
    assert "too narrow to hold even one circle" in shown and "17" in shown, (
        f"Skipped regions were not reported with their count. Shown: {shown!r}"
    )


def test_the_smoothing_loss_is_warned_about_when_it_is_large(fake_streamlit):
    """Area per replicate is this workflow's whole budget, and smoothing eats into it.

    Small circles lose about 10% of their area at the default 1 px tolerance, so every well
    would hold less than the table above it says.
    """
    result, _params, requests = _packed(
        area=4_000, min_circle_area_um2=100, max_circle_area_um2=150
    )
    ui_collect_regions.report_packing(result, requests)
    shown = fake_streamlit.shown("warnings") + " " + fake_streamlit.shown("captions")
    assert "moothing" in shown, (
        "Nothing was said about smoothing taking area off the circles, so the amounts shown are "
        "larger than what the laser will actually collect."
    )


def test_only_number_columns_are_given_a_number_format(fake_streamlit):
    """A `NumberColumn` on a text column marks every cell with a red warning triangle.

    Streamlit renders "this value cannot be interpreted as a number" over the class names, which
    reads as an error in a table that is perfectly fine — so the config is built from the
    numeric columns only.
    """
    import pandas

    ui_shared.show_amounts(
        pandas.DataFrame({"Class": ["Tumor", "Immune cells"], "Collected (µm²)": [10_004.4, 9_998.1]})
    )
    assert fake_streamlit.tables, "Nothing was shown at all."
    data, config = fake_streamlit.tables[-1]

    assert "Class" not in config, (
        "The class-name column was given a number format, so Streamlit flags every class name "
        "as not being a number."
    )
    assert "Collected (µm²)" in config, (
        "The amount column lost its format, so it shows a long float tail with no separator."
    )
    assert data["Collected (µm²)"].tolist() == [10_004, 9_998], (
        f"Amounts came out as {data['Collected (µm²)'].tolist()}; they should be whole numbers, "
        "since a fraction of a square micrometre is noise in a number the user has to read."
    )


def test_slide_controls_do_not_appear_for_one_slide(fake_streamlit, monkeypatch):
    """One slide and one plate must look as the app always has (`decisions.md` 076)."""
    def no_radio(*args, **kwargs):
        raise AssertionError("The slide strategy was offered with only one slide.")

    monkeypatch.setattr(streamlit, "radio", no_radio)
    slide = _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    strategy, order = ui_samples.strategy_control(_context(slide), key="test")
    assert order == [slide.name]


def test_plate_distribution_does_not_appear_for_one_plate(fake_streamlit, monkeypatch):
    def no_radio(*args, **kwargs):
        raise AssertionError("Plate distribution was offered with only one plate.")

    monkeypatch.setattr(streamlit, "radio", no_radio)
    monkeypatch.setattr(streamlit, "number_input", lambda label, value=None, **k: value)
    wells = plate.acceptable_wells("384", margins=1)
    n_plates, _distribution = ui_plates._plates_control(
        ["Tumor_r1", "Tumor_r2"], {"usable": wells, "first_plate": wells}
    )
    assert n_plates == 1


def test_more_samples_than_a_plate_holds_ask_for_a_second_plate(fake_streamlit, monkeypatch):
    """The default number of plates is the fewest that hold every sample."""
    monkeypatch.setattr(streamlit, "number_input", lambda label, value=None, **k: value)
    monkeypatch.setattr(streamlit, "radio", lambda label, options, **k: options[0])
    wells = plate.acceptable_wells("96", margins=3, step_col=2)
    samples = [f"Tumor_r{n}" for n in range(1, len(wells) + 2)]
    n_plates, _distribution = ui_plates._plates_control(samples, {"usable": wells, "first_plate": wells})
    assert n_plates == 2, f"{len(samples)} samples on {len(wells)}-well plates defaulted to {n_plates} plate(s)."


def _settings_for(plate_type, monkeypatch):
    """Stage 3's settings with every widget at its default, and `plate_type` chosen."""
    monkeypatch.setattr(streamlit, "selectbox", lambda *a, **k: plate_type)
    monkeypatch.setattr(streamlit, "number_input", lambda label, value=None, **k: value)
    monkeypatch.setattr(streamlit, "text_input", lambda *a, **k: "")
    monkeypatch.setattr(streamlit, "toggle", lambda *a, **k: False)
    return ui_plates.settings_step()


def test_margin_and_spacing_are_not_offered_for_tubes(fake_streamlit, monkeypatch):
    """On a holder of four tubes a margin or spacing would only leave tubes unused."""
    offered = []
    monkeypatch.setattr(streamlit, "selectbox", lambda *a, **k: "tubes")
    monkeypatch.setattr(streamlit, "number_input", lambda label, value=None, **k: offered.append(label) or value)
    monkeypatch.setattr(streamlit, "text_input", lambda *a, **k: "")
    monkeypatch.setattr(streamlit, "toggle", lambda *a, **k: False)
    settings = ui_plates.settings_step()
    assert not [label for label in offered if "Margin" in label or "Space" in label], (
        f"Margin or spacing was offered for a tube holder: {offered}. It does nothing there and "
        "invites a user to think a tube is being skipped."
    )
    assert settings["usable"] == ["A", "B", "C", "D"]


def test_several_collectors_warn_about_separate_cutting_runs(fake_streamlit, monkeypatch):
    settings = _settings_for("tubes", monkeypatch)
    samples = [f"Tumor_r{n}" for n in range(1, 14)]
    assignment = plate.assign_to_plates(samples, settings["usable"], 4, plate="tubes")
    ui_plates._capacity_report(samples, settings, 4, assignment)
    assert "4 separate cutting runs" in fake_streamlit.shown("warnings"), (
        "Thirteen samples need four tube holders, which is four runs with a holder change between "
        f"each; the user was not told. Warnings: {fake_streamlit.warnings}"
    )
    assert "13 tubes" in fake_streamlit.shown("writes") and "4 tube holders" in fake_streamlit.shown("writes")


def test_one_collector_does_not_warn_about_cutting_runs(fake_streamlit, monkeypatch):
    settings = _settings_for("tubes", monkeypatch)
    samples = ["Tumor_r1", "Tumor_r2", "Tumor_r3"]
    assignment = plate.assign_to_plates(samples, settings["usable"], 1, plate="tubes")
    ui_plates._capacity_report(samples, settings, 1, assignment)
    assert "separate cutting runs" not in fake_streamlit.shown("warnings"), (
        "One tube holder is one run; warning about several would teach users to ignore warnings."
    )


def test_hundreds_of_samples_on_tubes_do_not_exceed_the_collector_limit(fake_streamlit, monkeypatch):
    """Streamlit raises when a number input's value is above its maximum."""
    seen = {}
    monkeypatch.setattr(streamlit, "number_input", lambda label, **k: seen.update(k) or k["value"])
    monkeypatch.setattr(streamlit, "radio", lambda label, options, **k: options[0])
    tubes = plate.acceptable_wells("tubes")
    samples = [f"Tumor_r{n}" for n in range(1, 301)]
    n_plates, _ = ui_plates._plates_control(samples, {"usable": tubes, "first_plate": tubes, "plate_type": "tubes"})
    assert seen["value"] == 75 == n_plates
    assert seen["max_value"] >= seen["value"], (
        f"300 samples need 75 tube holders but the control stops at {seen['max_value']}; Streamlit "
        "would raise and Stage 3 would not render."
    )


def test_a_file_for_plates_is_refused_when_tubes_are_chosen(fake_streamlit, monkeypatch):
    import contextlib
    import io

    monkeypatch.setattr(streamlit, "expander", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(streamlit, "file_uploader", lambda *a, **k: io.BytesIO(b"{'P1': {'Tumor_r1': 'C3'}}"))
    assert ui_plates._custom_assignment(["Tumor_r1"], "tubes") is None, (
        "A plate file was accepted with a tube holder chosen; C3 does not exist on a tube holder."
    )
    assert "tube holder" in fake_streamlit.shown("errors"), (
        f"The refusal does not say a tube holder is chosen: {fake_streamlit.errors}"
    )


def test_the_tube_caption_counts_tubes_on_the_named_holder(fake_streamlit, monkeypatch):
    captions = []
    monkeypatch.setattr(streamlit, "caption", lambda *a, **k: captions.append(str(a[0]) if a else ""))
    monkeypatch.setattr(streamlit, "download_button", lambda *a, **k: None)
    ui_shared.plate_preview(
        {"a": "A", "b": "C"}, "tubes", wells=["A", "B", "C", "D"], key_suffix="t", plate_name="TubeHolder2"
    )
    caption = " ".join(captions)
    assert "2 of 4 tubes in use on TubeHolder2" in caption, caption
    assert "start at **B**" in caption, (
        f"The caption should name tube B as the next free one: {caption!r}"
    )


def test_the_cutting_runs_count_the_collectors_that_receive_samples(fake_streamlit, monkeypatch):
    """Sequential filling of four holders with five samples uses two; the warning must say two."""
    settings = _settings_for("tubes", monkeypatch)
    samples = [f"Tumor_r{n}" for n in range(1, 6)]
    assignment = plate.assign_to_plates(
        samples, settings["usable"], 4, plate.PlateDistribution.SEQUENTIAL, plate="tubes"
    )
    ui_plates._capacity_report(samples, settings, 4, assignment)
    warnings = fake_streamlit.shown("warnings")
    assert "2 separate cutting runs" in warnings and "4 separate" not in warnings, (
        "Five samples filled two of four tube holders, but the warning counted the number box. "
        f"The download has two .xml files, so the screen disagrees with it: {warnings}"
    )


def test_a_custom_file_on_one_holder_does_not_warn_about_several_runs(fake_streamlit, monkeypatch):
    settings = _settings_for("tubes", monkeypatch)
    assignment = {"Tumor_r1": ("TubeHolder1", "A"), "Tumor_r2": ("TubeHolder1", "B")}
    ui_plates._capacity_report(["Tumor_r1", "Tumor_r2"], settings, 4, assignment)
    assert "separate cutting runs" not in fake_streamlit.shown("warnings"), (
        "An uploaded file puts everything on one tube holder, yet the warning speaks of several runs."
    )


def test_pictures_fit_the_width_streamlit_shows(cells_gdf, calibration, touching_chain):
    """Streamlit decodes, shrinks and re-encodes any picture wider than it shows, on every rerun,
    cached or not — the cost that made a margin change take seconds (`decisions.md` 085)."""
    from streamlit.elements.lib.image_utils import MAXIMUM_CONTENT_WIDTH

    assert ui_shared.PICTURE_MAX_WIDTH_PX == MAXIMUM_CONTENT_WIDTH, (
        "Streamlit changed the widest picture it shows; pictures are now either re-encoded on every "
        "rerun or blurrier than they could be. Set PICTURE_MAX_WIDTH_PX to the new value."
    )
    figures = {
        "classes and selection": plot.plot_shapes(cells_gdf, calibration_array=calibration, title="t"),
        "regions": plot.plot_regions_and_circles(touching_chain, title="t"),
    }
    for name, figure in figures.items():
        width, _height = Image.open(io.BytesIO(ui_shared.picture_png(figure))).size
        assert ui_shared.PICTURE_MAX_WIDTH_PX - 100 < width <= ui_shared.PICTURE_MAX_WIDTH_PX, (
            f"The {name} picture is {width} px wide: wider and Streamlit re-encodes it on every "
            "rerun, much narrower and it is blurrier than the page could show."
        )


def test_a_picture_is_drawn_once_and_again_only_when_what_it_shows_changes(monkeypatch, cells_gdf):
    """Redrawing every slide's pictures was nearly all of a rerun's cost; a stale picture would
    show a selection or classes the collection no longer has."""
    monkeypatch.setattr(streamlit, "image", lambda *a, **k: None)
    ui_shared._cached_picture.clear()
    drawn = []

    def show(labels, calibration, palette):
        def draw():
            drawn.append(1)
            return plot.plot_shapes(cells_gdf.head(5), labels=labels.head(5))

        ui_shared.show_picture(draw, ui_shared.picture_key("selection", ("A",), labels, calibration, palette))

    labels = cells_gdf[CLASS_NAME].copy()
    calibration = numpy.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    show(labels, calibration, {"x": "#000000"})
    show(labels.copy(), calibration.copy(), {"x": "#000000"})
    assert len(drawn) == 1, "An unchanged picture was drawn again: every rerun pays for it."

    reselected = labels.copy()
    reselected.iloc[0] = None
    for changed in [
        (reselected, calibration, {"x": "#000000"}),
        (labels, calibration + 1, {"x": "#000000"}),
        (labels, calibration, {"x": "#ffffff"}),
    ]:
        before = len(drawn)
        show(*changed)
        assert len(drawn) == before + 1, (
            "A picture whose selection, calibration or colours changed was not redrawn, so it "
            "shows something other than what will be cut."
        )


def test_a_picture_key_sees_moved_geometry(touching_chain):
    """The regions pictures key on their geometry: a region that moved must be drawn where it is."""
    moved = touching_chain.copy()
    moved.geometry = moved.geometry.translate(5, 0)
    assert ui_shared.picture_key(touching_chain.geometry) != ui_shared.picture_key(moved.geometry), (
        "Moved regions gave the same picture key, so the old map would be shown."
    )


def test_the_summary_forgets_a_stage_that_will_report_again():
    """Stages 3 and 4 rerun on their own and reuse the summary; one that now stops early (no
    usable wells) must not leave its last line claiming a collector is set."""
    summary = ui_summary.Summary()
    summary.lines.update({"Collector": "18 of 18 samples placed", "Cut": "ready to download"})
    summary.figures["plates"] = "**1 plate**"
    summary.forget("Collector", "Cut")
    assert "Collector" not in summary.lines and "Cut" not in summary.lines, (
        "The sidebar would still say the collector is set and the download ready after stage 3 stopped."
    )
    assert "plates" not in summary.figures, "The sidebar headline would still count plates."

