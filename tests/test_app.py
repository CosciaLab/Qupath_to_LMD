"""The whole app, driven headlessly: upload, every stage, and the download.

Streamlit's `AppTest` runs `streamlit_app.py` as a user's browser would, without one. Its file
uploader cannot be driven, so the slide uploader is replaced with one that hands over demo files.
Slow-ish (seconds per run), so marked `slow` alongside the golden gate.
"""

import io
import json
import pathlib
import zipfile

import pytest
import streamlit
from streamlit.testing.v1 import AppTest

REPO = pathlib.Path(__file__).resolve().parent.parent
DEMO = REPO / "demo_Qupath_project"
GOLDEN = REPO / "tools" / "golden"

pytestmark = pytest.mark.slow


class _Upload:
    def __init__(self, path: pathlib.Path):
        self.name = path.name
        self._data = path.read_bytes()
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


def _start(monkeypatch, files: list[str]) -> AppTest:
    uploads = [_Upload(DEMO / name) for name in files]
    real = streamlit.file_uploader

    def uploader(label, *args, **kwargs):
        return uploads if kwargs.get("key") == "slide_uploader" else real(label, *args, **kwargs)

    monkeypatch.setattr(streamlit, "file_uploader", uploader)
    app = AppTest.from_file(str(REPO / "streamlit_app.py"), default_timeout=180)
    app.run()
    return app


def _confirm_calibration(app: AppTest) -> None:
    for box in app.checkbox:
        if box.key and box.key.startswith("calib_confirmed_"):
            box.check()
    app.run()


def _run(monkeypatch, files: list[str], method: str | None = None, actions=()) -> AppTest:
    app = _start(monkeypatch, files)
    _confirm_calibration(app)
    if method:
        app.radio(key="workflow_choice").set_value(method).run()
    for action in actions:
        action(app)
        app.run()
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    process = [button for button in app.button if "Process" in str(button.label)]
    assert process, "No 'Process files' button: a stage before the cut stopped the page."
    process[0].click().run()
    assert not app.exception, f"Processing raised: {[e.value for e in app.exception]}"
    assert app.session_state.zip_buffer is not None, (
        f"Processing produced no download. Errors on the page: {[e.value for e in app.error]}"
    )
    return app


def _contents(app: AppTest) -> dict[str, bytes]:
    archive = zipfile.ZipFile(io.BytesIO(app.session_state.zip_buffer.getvalue()))
    return {name: archive.read(name) for name in archive.namelist()}


def test_one_annotation_file_through_the_app_matches_the_golden_reference(monkeypatch):
    """The annotations workflow moved into the shared stages; what it cuts must not have moved."""
    files = _contents(_run(monkeypatch, ["TD_01_verysmall_mIF.geojson"]))
    assert files["TD_01_verysmall_mIF.xml"] == (GOLDEN / "annotations.xml").read_bytes(), (
        "The app's .xml for the demo annotations differs from tools/golden/annotations.xml. A "
        "single-slide whole-shapes collection would cut differently from before the stages."
    )
    assert files["TD_01_verysmall_mIF_384_wellplate.csv"] == (GOLDEN / "annotations.csv").read_bytes()


def test_one_annotation_file_into_a_tube_holder_cuts_into_tubes(monkeypatch):
    """Choosing the tube holder in Stage 3 reaches the .xml as letter CapIDs, end to end."""
    def tubes(app):
        app.selectbox(key="plate_type").set_value("tubes")

    app = _run(monkeypatch, ["TD_01_verysmall_mIF.geojson"], actions=[tubes])
    assert not [box for box in app.number_input if "Margin" in str(box.label)], (
        "The tube holder still shows a margin control, which does nothing on four tubes."
    )
    files = _contents(app)
    assert files["TD_01_verysmall_mIF.xml"] == (GOLDEN / "tubes.xml").read_bytes(), (
        "The app's tube holder .xml differs from tools/golden/tubes.xml, so choosing tubes in the "
        "app does not cut what the library cuts into tubes."
    )
    assert files["TD_01_verysmall_mIF_tubes.csv"] == (GOLDEN / "tubes.csv").read_bytes()


def test_two_slides_on_two_plates_give_four_cutting_files(monkeypatch):
    def two_plates(app):
        key = next(w.key for w in app.number_input if w.key and w.key.startswith("n_plates_"))
        app.number_input(key=key).set_value(2)

    app = _run(monkeypatch, ["Single_cells.geojson", "multiclass_cells.geojson"], "cells", [two_plates])
    files = _contents(app)
    xmls = sorted(name for name in files if name.endswith(".xml"))
    assert len(xmls) == 4, f"Two slides on two plates should give four .xml files, got {xmls}."
    for expected in ("samples.csv", "Plate1.csv", "Plate2.csv", "COLLECTION_PLAN.txt"):
        assert expected in files, f"{expected} is missing from the experiment download."


def test_regions_across_two_slides_run_to_a_download(monkeypatch):
    files = _contents(_run(monkeypatch, ["Single_cells.geojson", "Single_cells.geojson"], "regions"))
    assert {"slide_Single_cells/Single_cells__Plate1.xml", "slide_Single_cells_2/Single_cells_2__Plate1.xml"} <= set(files), (
        f"Each copy of the slide should get its own .xml: {sorted(files)}"
    )


def test_nothing_goes_further_until_every_slide_is_confirmed(monkeypatch):
    """The suggested points are only a suggestion; a swapped point mis-cuts every shape."""
    app = _start(monkeypatch, ["Single_cells.geojson", "multiclass_cells.geojson"])
    headers = [block.value for block in app.markdown if block.value.startswith("## ")]
    assert "## 2 · Samples" not in headers, "The Samples stage appeared before any calibration was confirmed."

    first = next(box for box in app.checkbox if box.key and box.key.startswith("calib_confirmed_"))
    first.check()
    app.run()
    assert "## 2 · Samples" not in [b.value for b in app.markdown], (
        "The Samples stage appeared with one of two slides still unconfirmed."
    )
    assert any("still to confirm" in info.value for info in app.info), "The app did not say which slide is waiting."

    _confirm_calibration(app)
    assert "## 2 · Samples" in [b.value for b in app.markdown], "Confirming every slide did not open the Samples stage."


def test_extras_are_a_page_of_their_own_not_the_sidebar(monkeypatch):
    """In the sidebar the extras crowded the experiment summary; they are their own page now."""
    app = AppTest.from_file(str(REPO / "streamlit_app.py"), default_timeout=60)
    app.run()
    assert not any("categoricals" in block.value for block in app.sidebar.markdown), (
        "The QuPath classes generator is back in the sidebar."
    )
    app.switch_page("app_pages/extras.py").run()
    assert not app.exception, f"The Extras page raised: {[e.value for e in app.exception]}"
    assert any("categoricals" in block.value for block in app.markdown), "The Extras page shows no classes generator."


def test_extras_take_as_many_categoricals_as_typed_with_one_empty_row_waiting():
    app = AppTest.from_file(str(REPO / "streamlit_app.py"), default_timeout=60)
    app.run()
    app.switch_page("app_pages/extras.py").run()
    assert [box.key for box in app.text_input if box.key.startswith("extras_values_")] == ["extras_values_0"], (
        "The page does not open on a single empty categorical."
    )

    app.text_input(key="extras_values_0").set_value("tumour, stroma").run()
    app.text_input(key="extras_values_1").set_value("control, drug, control").run()
    app.text_input(key="extras_values_2").set_value("day1, day2, day3").run()
    assert not app.exception, f"The Extras page raised: {[e.value for e in app.exception]}"
    assert "extras_values_3" in [box.key for box in app.text_input], (
        "Filling the third categorical did not offer an empty fourth, so users are capped at three."
    )
    assert any("2 × 2 × 3 = 12 combinations × 2 replicates = **24 classes**" in block.value for block in app.markdown), (
        "The class count shown before download does not match three categoricals with a repeated value merged."
    )
    assert any("`control` more than once" in warning.value for warning in app.warning), (
        "A value typed twice was merged without telling the user."
    )
    assert app.get("download_button"), "There is no download for the generated classes."

    app.button(key="extras_remove_1").click().run()
    assert any("2 × 3 = 6 combinations" in block.value for block in app.markdown), (
        "Removing the middle categorical did not take its values out of the class names."
    )
    app.switch_page("streamlit_app.py").run()
    app.switch_page("app_pages/extras.py").run()
    assert any("2 × 3 = 6 combinations" in block.value for block in app.markdown), (
        "Going to Collect and back emptied the classes generator; the user would retype every categorical."
    )


def test_a_look_at_the_tube_holder_does_not_reset_the_plate_margin(monkeypatch):
    """Margin and spacing are hidden for holders, and Streamlit forgets a hidden widget's value."""
    app = _start(monkeypatch, ["TD_01_verysmall_mIF.geojson"])
    _confirm_calibration(app)
    app.number_input(key="plate_margin").set_value(2).run()
    app.number_input(key="plate_step_col").set_value(2).run()
    app.selectbox(key="plate_type").set_value("tubes").run()
    app.selectbox(key="plate_type").set_value("384").run()
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    assert app.number_input(key="plate_margin").value == 2 and app.number_input(key="plate_step_col").value == 2, (
        "Switching to the tube holder and back reset the plate margin or spacing, so the layout "
        "silently uses the edge wells the user had chosen to avoid."
    )
    assert not [w for w in app.warning if "Session State" in str(w.value)], (
        f"Streamlit warned about the restored values: {[w.value for w in app.warning]}"
    )


def test_class_names_with_spaces_reach_their_wells_from_a_hand_typed_file(monkeypatch, tmp_path):
    """Issue 24, end to end: spaces inside class names, a stray trailing space in QuPath and
    another in the samples-and-wells file, and every shape still lands in its well."""
    import json

    document = json.loads((DEMO / "TD_01_verysmall_mIF.geojson").read_text())
    shapes = [f for f in document["features"] if f["geometry"]["type"] != "Point"]
    for k, feature in enumerate(shapes):
        feature["properties"]["classification"] = {"name": ["Immune cells ", "Tumor core"][k % 2], "color": [255, 0, 0]}
    (tmp_path / "spaced.geojson").write_text(json.dumps(document))
    (tmp_path / "saw.txt").write_text("{'Immune cells': 'C3', 'Tumor  core ': 'C5'}")

    slide_upload, saw_upload = _Upload(tmp_path / "spaced.geojson"), _Upload(tmp_path / "saw.txt")
    saw_upload.read = saw_upload.getvalue
    real = streamlit.file_uploader

    def uploader(label, *args, **kwargs):
        if kwargs.get("key") == "slide_uploader":
            return [slide_upload]
        if kwargs.get("key") == "saw_uploader":
            return saw_upload
        return real(label, *args, **kwargs)

    monkeypatch.setattr(streamlit, "file_uploader", uploader)
    app = AppTest.from_file(str(REPO / "streamlit_app.py"), default_timeout=180)
    app.run()
    _confirm_calibration(app)
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    process = [button for button in app.button if "Process" in str(button.label)]
    assert process, f"No 'Process files' button. Warnings: {[w.value[:200] for w in app.warning]}"
    process[0].click().run()
    files = _contents(app)
    xml = files["spaced.xml"].decode()
    assert xml.count("<CapID>C3</CapID>") == 3 and xml.count("<CapID>C5</CapID>") == 3, (
        "Not every shape reached the well its spaced class name was given in the file, so a "
        "class name with a space would be cut into nothing."
    )
    assert not [w for w in app.warning if "no well" in str(w.value)], "A spaced sample was reported as having no well."


def test_expanding_cells_shows_its_cost_and_reaches_the_download(monkeypatch):
    """Issue 31: off by default, and once ticked the added area, the overlaps and an example are
    on screen before the download, and the distance is recorded with the collection."""
    import json

    def expand(app):
        app.checkbox(key="expand_shapes").check()

    app = _run(monkeypatch, ["Single_cells.geojson"], "cells", [expand])
    assert any(w.key == "expand_um" and w.value == 1.0 for w in app.number_input), (
        "With a scale known the expansion should be asked in µm and default to 1 µm."
    )
    assert [d for d in app.dataframe if "Added (%)" in d.value.columns], "The added area is not shown before download."
    assert [w for w in app.warning if "overlap a shape going to a different" in str(w.value)], (
        "Neighbouring cells in different wells overlap once expanded, and the user was not told."
    )
    assert app.get("imgs"), "No before-and-after picture of a shape was shown."
    provenance = json.loads(_contents(app)["provenance.json"])
    assert json.dumps(provenance).count('"expand_um": 1.0'), (
        f"The expansion is not recorded in provenance.json, so the collection cannot be reproduced: {provenance}"
    )


def test_shapes_are_not_expanded_unless_asked(monkeypatch):
    app = _run(monkeypatch, ["TD_01_verysmall_mIF.geojson"])
    assert not [w for w in app.number_input if w.key in ("expand_um", "expand_px")], (
        "The expansion distance is offered before the box is ticked."
    )
    assert "expand" not in _contents(app)["provenance.json"].decode(), "An unexpanded collection records an expansion."


def test_outlines_name_the_samples_and_reach_the_download(monkeypatch):
    """Two patients on one image, kept apart by outlines (`decisions.md` 091), all the way to the zip."""
    import json

    def tick_outlines(app):
        (control,) = [m for m in app.multiselect if m.label == "Classes that outline a group of shapes"]
        control.set_value(["P1", "P2"])

    app = _run(monkeypatch, ["outlines_two_patients.geojson"], actions=[tick_outlines])
    assert any("will not be collected" in w.value for w in app.warning), (
        "The stray shape between the two patients was left out without a word on screen."
    )
    files = _contents(app)
    provenance = json.loads(files["provenance.json"])
    assert provenance["parameters"]["outlines"]["classes"] == ["P1", "P2"], (
        "provenance.json does not record that outlines decided the samples."
    )
    scheme = json.loads(files["samples_and_wells.json"])
    assert sorted(scheme) == ["P1_Gland", "P1_Stroma", "P1_Vessel", "P2_Gland", "P2_Stroma", "P2_Vessel"], (
        f"The plate holds {sorted(scheme)}, not one well per patient and class."
    )


@pytest.mark.parametrize("method", ["legacy", "cells", "regions"])
def test_a_slide_without_outlines_is_warned_about_not_fatal(monkeypatch, method):
    """Ticking outlines empties a slide that has none; the others must still reach a download."""

    def tick_outlines(app):
        (control,) = [m for m in app.multiselect if m.label == "Classes that outline a group of shapes"]
        control.set_value(["P1", "P2"])

    app = _run(monkeypatch, ["outlines_two_patients.geojson", "TD_01_verysmall_mIF.geojson"], method, [tick_outlines])
    assert any("No outline on TD_01_verysmall_mIF" in w.value for w in app.warning), (
        "The slide that lost every shape is not named, so the user cannot tell why it contributes nothing."
    )


def test_outline_classes_ticked_one_at_a_time_are_all_kept(monkeypatch):
    """A lab ticks 13 patients one click at a time; no click may be thrown away."""
    app = _start(monkeypatch, ["outlines_two_patients.geojson"])
    _confirm_calibration(app)
    for chosen in (["P1"], ["P1", "P2"]):
        (control,) = [m for m in app.multiselect if m.label == "Classes that outline a group of shapes"]
        control.set_value(chosen).run()
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    assert app.session_state.outline_classes == ["P1", "P2"], (
        f"After ticking P1 then P2 the outline classes are {app.session_state.outline_classes}: "
        "a click was silently undone, and that patient's shapes would be left out."
    )


def _apply_to_every_class(replicates: int, amount: float | None):
    def apply(app):
        inputs = {control.label.split(" (")[0]: control for control in app.number_input}
        inputs["Replicates"].set_value(replicates)
        if amount is not None:
            inputs["Per replicate"].set_value(amount)
        (button,) = [b for b in app.button if b.label == "Apply to every class"]
        button.click()

    return apply


def test_replicates_and_amount_set_once_reach_every_selected_class(monkeypatch):
    """Thirteen patients by nineteen classes: one Apply instead of hundreds of table edits."""
    app = _run(monkeypatch, ["outlines_two_patients.geojson"], "cells", [_apply_to_every_class(2, 40.0)])
    budgets = {item["class_name"]: (item["replicates"], item["per_replicate"]) for item in app.session_state.budgets}
    assert set(budgets.values()) == {(2, 40.0)}, f"Not every class got the values applied to all: {budgets}"
    scheme = json.loads(_contents(app)["samples_and_wells.json"])
    assert len(scheme) == 2 * len(budgets), f"{len(scheme)} wells for {len(budgets)} classes at 2 replicates each."


def test_replicates_and_area_set_once_reach_every_region_class(monkeypatch):
    def scale(app):
        app.number_input(key="pixel_size_input").set_value(0.5).run()  # the scale is read before the mode is offered

    def circles(app):
        from qupath_to_lmd.ui_collect_regions import CollectMode

        (choice,) = [r for r in app.radio if r.label == "What to collect from each region"]
        choice.set_value(CollectMode.CIRCLES)

    app = _run(monkeypatch, ["TD_01_verysmall_mIF.geojson"], "regions", [scale, circles, _apply_to_every_class(2, 10_000.0)])
    requests = {item["class_name"]: (item["replicates"], item["area_per_replicate_um2"]) for item in app.session_state.region_budgets}
    assert set(requests.values()) == {(2, 10_000.0)}, f"Not every class got the values applied to all: {requests}"


def test_classes_to_collect_ticked_one_at_a_time_are_all_kept(monkeypatch):
    """Untick one class, then another: both clicks must hold, or a class is cut that was dropped."""
    app = _start(monkeypatch, ["outlines_two_patients.geojson"])
    _confirm_calibration(app)
    (control,) = [m for m in app.multiselect if m.label == "Classes to collect"]
    every = list(control.value)
    assert len(every) > 2, "The test needs a class left over after unticking two."
    for chosen in (every[1:], every[2:]):
        (control,) = [m for m in app.multiselect if m.label == "Classes to collect"]
        control.set_value(chosen).run()
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    assert app.session_state.selected_classes == every[2:], (
        f"After unticking {every[0]} then {every[1]} the classes to collect are "
        f"{app.session_state.selected_classes}: a click was silently undone, and a class the user "
        "dropped would be cut."
    )
