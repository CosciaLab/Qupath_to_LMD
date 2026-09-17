"""Behaviour the UI layer is responsible for: blocking, warning, and what it reports.

These use a stubbed Streamlit rather than a browser. They cover the decisions that protect a
collection — the hard stops, and the difference between a warning and a note.
"""

import pytest
import streamlit
from shapely.geometry import box as shapely_box

from qupath_to_lmd import budget, geojson, plate, regions, selection, ui_cells, ui_packing, ui_shared
from qupath_to_lmd.model import CLASS_NAME, plan_from_class_wells, plan_from_selection


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

    gdf, calibration_points, report = geojson.read_and_qc(handle.name)
    fake_streamlit.state.update(gdf=gdf, calibration_points=calibration_points, geojson_report=report,
                               calibs=None, calib_array=None, file_name="test.geojson")
    return gdf


@pytest.mark.parametrize("kept", [0, 1, 2])
def test_fewer_than_three_calibration_points_stops_the_app(fake_streamlit, kept):
    """Without three points no cutting file can be meaningful, so this is one of the very few
    places the app refuses to continue rather than warning."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson", keep_points=kept)
    with pytest.raises(Stopped):
        ui_shared.calibration_step()
    assert fake_streamlit.errors, (
        f"With {kept} calibration points the app halted without explaining why. The user needs "
        "to be told to add points in QuPath."
    )
    assert "calibration point" in fake_streamlit.shown("errors").lower()


def test_a_degenerate_calibration_triangle_stops_the_app(fake_streamlit, monkeypatch):
    """py-lmd writes a valid-looking XML from three identical points, so nothing downstream
    would catch it."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    first = list(fake_streamlit.state.calibration_points)[0]
    coordinate = fake_streamlit.state.calibration_points[first]
    fake_streamlit.state.calibration_points = dict.fromkeys(("a", "b", "c"), coordinate)
    monkeypatch.setattr(streamlit, "selectbox", lambda label, options, index=0, **k: options[index])

    with pytest.raises(Stopped):
        ui_shared.calibration_step()
    assert "triangle" in fake_streamlit.shown("errors").lower(), (
        f"A degenerate triangle should be explained as such; errors were: {fake_streamlit.errors}"
    )


def test_three_valid_points_do_not_stop_the_app(fake_streamlit):
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    ui_shared.calibration_step()
    assert not fake_streamlit.errors, f"A valid calibration raised errors: {fake_streamlit.errors}"
    assert fake_streamlit.state.calib_array is not None


def test_a_large_file_warns_about_running_locally(fake_streamlit):
    """A whole-slide export needs more memory than the hosted app has, so the user should be
    told before spending ten minutes finding out."""
    shapes = ui_shared.HOSTED_COMFORTABLE_SHAPES + 1
    ui_shared._report_scale(shapes)
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
    ui_shared._report_scale(ui_shared.HOSTED_COMFORTABLE_SHAPES - 1)
    assert not fake_streamlit.warnings, (
        f"A file below the threshold warned anyway: {fake_streamlit.warnings}"
    )


def test_unselected_shapes_are_a_note_in_the_cell_workflow(fake_streamlit, cells, calibration):
    """Most shapes are deliberately not selected — that is the point of the workflow. Warning
    about it fired on every single collection and trained users to ignore warnings."""
    gdf, points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    budgets = [budget.ClassBudget(classes[0], 1, 3)]
    result = selection.select(gdf, budgets, budget.BudgetMode.CELLS, selection.SelectionParams(seed=1), 0.3467)
    wells = plate.acceptable_wells("384", margins=1)
    plan, _saw = plan_from_selection(
        gdf=gdf, replicate_of=result.replicate_of, wells=wells,
        samples_and_wells=plate.assign_wells(budget.group_keys(budgets), wells),
        calibration_names=list(points)[:3], calibration_array=calibration,
    )

    ui_shared._report_excluded(plan)
    assert fake_streamlit.captions, "Shapes not selected should be noted quietly, not warned about."
    assert not fake_streamlit.warnings, (
        f"The cell workflow warned about its own intended outcome: {fake_streamlit.warnings}"
    )
    assert "as intended" in fake_streamlit.shown("captions")


def test_classes_absent_from_the_scheme_warn_in_the_annotations_workflow(fake_streamlit, cells, calibration):
    """There it usually means the user forgot a class, which is worth interrupting for."""
    gdf, points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    plan = plan_from_class_wells(
        gdf=gdf, samples_and_wells={classes[0]: "C3"},
        calibration_names=list(points)[:3], calibration_array=calibration,
    )
    ui_shared._report_excluded(plan)
    assert fake_streamlit.warnings, (
        "Classes missing from the samples-and-wells scheme should warn in the annotations workflow."
    )


def test_shapes_whose_group_got_no_well_always_warn(fake_streamlit, cells, calibration):
    """These are shapes the user asked to collect that will not be cut, in either workflow."""
    gdf, points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    budgets = [budget.ClassBudget(name, 2, 2) for name in classes]
    result = selection.select(gdf, budgets, budget.BudgetMode.CELLS, selection.SelectionParams(seed=1), 0.3467)
    plan, _saw = plan_from_selection(
        gdf=gdf, replicate_of=result.replicate_of, wells=["B2"],
        samples_and_wells=plate.assign_wells(budget.group_keys(budgets), ["B2"]),
        calibration_names=list(points)[:3], calibration_array=calibration,
    )
    ui_shared._report_excluded(plan)
    assert any("no well" in w for w in fake_streamlit.warnings), (
        f"Groups that got no well must be warned about; warnings were: {fake_streamlit.warnings}"
    )


def test_the_workflow_suggestion_follows_the_object_types(fake_streamlit, monkeypatch):
    """A file of cells should default to the cell workflow, and annotations to the other, but
    both remain changeable because a file can contain both."""
    monkeypatch.setattr(streamlit, "radio", lambda label, options, index=0, **k: options[index])
    fake_streamlit.state.workflow = "legacy"

    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    assert ui_cells and ui_shared.workflow_step() == "cells", (
        "A file of 121 cells should suggest the cell workflow."
    )

    _load(fake_streamlit, "demo_Qupath_project/TD_01_verysmall_mIF.geojson")
    assert ui_shared.workflow_step() == "legacy", (
        "A file of annotations only should suggest the annotations workflow."
    )


def test_the_shape_fingerprint_changes_when_classes_are_exploded(fake_streamlit, cells_gdf):
    """Caches key off this. A filename alone would serve a stale selection after exploding,
    because exploding rewrites the class names in place."""
    fake_streamlit.state.file_name = "a.geojson"
    before = ui_shared.shape_fingerprint(cells_gdf)
    after = ui_shared.shape_fingerprint(geojson.explode_classes(cells_gdf, ["single_cells_demo"]))
    assert before != after, (
        "Exploding a class did not change the cache fingerprint, so a cached selection from "
        "before the explode would be reused."
    )
    fake_streamlit.state.file_name = "b.geojson"
    assert ui_shared.shape_fingerprint(cells_gdf) != before, "A different file gave the same fingerprint."


def test_the_scale_is_estimated_when_the_file_allows_it(fake_streamlit):
    """The estimate has been right on every file where it could be computed, and the input was
    the step users stumbled on. So where measurements exist the app uses them and says so."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    fake_streamlit.state.pixel_size_um = None

    value, source = ui_shared.resolve_pixel_size()
    assert source == "estimated", (
        f"This file carries QuPath measurements, so the scale should be estimated; got {source!r}."
    )
    assert value == pytest.approx(0.3467, abs=5e-4), f"Estimated scale came out as {value}."


def test_a_typed_scale_overrides_the_estimate(fake_streamlit):
    """The estimate is a default, not a decision the app makes for the user."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    fake_streamlit.state.pixel_size_um = 0.5

    value, source = ui_shared.resolve_pixel_size()
    assert (value, source) == (0.5, "entered"), (
        f"A typed scale must win over the estimate; resolver returned {value} from {source!r}."
    )


def test_no_scale_is_available_for_a_file_without_measurements(fake_streamlit):
    """Annotation exports carry no areas, so there is nothing to estimate from and the app must
    fall back to asking rather than guessing."""
    _load(fake_streamlit, "demo_Qupath_project/TD_01_verysmall_mIF.geojson")
    fake_streamlit.state.pixel_size_um = None

    value, source = ui_shared.resolve_pixel_size()
    assert (value, source) == (None, "none"), (
        f"With no measurements there is nothing to estimate; resolver returned {value} from {source!r}."
    )


def test_a_wide_implied_spread_is_warned_about(fake_streamlit):
    """A scale that disagrees between objects suggests the export mixes images or was rescaled,
    which makes every area suspect."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    report = fake_streamlit.state.geojson_report
    report.pixel_size_spread = ui_shared.WIDE_SPREAD * 2

    ui_shared._report_pixel_size(0.3467, "estimated", 0.3467, report)
    assert any("varies by" in w for w in fake_streamlit.warnings), (
        f"A {report.pixel_size_spread:.0%} spread should be warned about; warnings were "
        f"{fake_streamlit.warnings}"
    )


def test_a_typed_scale_that_disagrees_with_the_file_is_warned_about(fake_streamlit):
    """A 2x error in scale is a 4x error in every area, so this is worth interrupting for."""
    _load(fake_streamlit, "demo_Qupath_project/Single_cells.geojson")
    report = fake_streamlit.state.geojson_report

    ui_shared._report_pixel_size(3.467, "entered", report.implied_pixel_size_um, report)
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
    ui_packing._report_starved_replicates(patches, replicates, replicate_of)

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
    ui_packing._report_packing(result, requests)

    shown = fake_streamlit.shown("warnings")
    assert "could not be filled" in shown, (
        "A replicate that fell short of its requested area was not reported, so the user would "
        "believe the well holds the amount they asked for."
    )
    assert "µm²" in shown, "The shortfall was not stated as an area, so it is not actionable."


def test_a_filled_replicate_does_not_warn(fake_streamlit):
    """Warning on the ordinary case is how warnings stop being read."""
    result, _params, requests = _packed(area=2_000)
    ui_packing._report_packing(result, requests)
    assert "could not be filled" not in fake_streamlit.shown("warnings"), (
        f"A fully-filled replicate warned anyway: {fake_streamlit.shown('warnings')!r}"
    )


def test_regions_too_narrow_for_a_circle_are_reported(fake_streamlit):
    """Those regions contribute nothing, and the fix is a smaller minimum circle size.

    From a circle count alone the user cannot tell that tissue was skipped.
    """
    from qupath_to_lmd import packing

    result = packing.PackingResult(n_regions_too_small=17)
    ui_packing._report_packing(result, [packing.ClassPacking("Tumor", 1, 100.0)])
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
    ui_packing._report_packing(result, requests)
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

    from qupath_to_lmd import ui_packing

    ui_packing._show_table(
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
