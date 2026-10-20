"""The whole app, driven headlessly: upload, every stage, and the download.

Streamlit's `AppTest` runs `streamlit_app.py` as a user's browser would, without one. Its file
uploader cannot be driven, so the slide uploader is replaced with one that hands over demo files.
Slow-ish (seconds per run), so marked `slow` alongside the golden gate.
"""

import io
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
