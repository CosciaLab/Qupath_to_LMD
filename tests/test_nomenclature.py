"""Keeps the vocabulary from drifting back.

The same thing was being called a shape, a polygon, an object and a contour in different parts
of the app. GLOSSARY.md settles it; these tests make it enforceable rather than aspirational.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = sorted((ROOT / "src" / "qupath_to_lmd").glob("*.py")) + [ROOT / "streamlit_app.py"]
GLOSSARY = ROOT / "GLOSSARY.md"
# Every module that shows the user text: all of the UI layer, found rather than listed, so a new
# stage or collection method is checked without anyone remembering to add it.
UI_MODULES = sorted(path.name for path in (ROOT / "src" / "qupath_to_lmd").glob("ui_*.py"))


def _sources_without_mock():
    """Every source file we own. `mock_streamlit.py` is legacy and excluded from lint too."""
    return [p for p in SOURCE if p.name != "mock_streamlit.py"]


def test_contour_is_not_used_anywhere():
    """It was a fourth word for a shape, in an image caption and the README.

    Reintroducing it means a user sees two names for the same thing in one session.
    """
    offenders = {
        path.name: [line for line in path.read_text().splitlines() if "contour" in line.lower()]
        for path in _sources_without_mock()
    }
    offenders = {name: lines for name, lines in offenders.items() if lines}
    assert not offenders, (
        "'contour' is a fourth name for a shape and should not appear. Found in:\n"
        + "\n".join(f"  {name}: {lines}" for name, lines in offenders.items())
        + "\nUse 'shape' — see GLOSSARY.md."
    )


def test_the_shape_limit_is_named_for_what_it_counts():
    """It was `POLYGON_LIMIT`, but it counts shapes, and a shape is not the same as a polygon."""
    from qupath_to_lmd import plot

    assert hasattr(plot, "SHAPE_LIMIT"), "plot.SHAPE_LIMIT is missing."
    assert not hasattr(plot, "POLYGON_LIMIT"), (
        "plot.POLYGON_LIMIT is back. It counts shapes, not polygons — 'polygon' is reserved for "
        "the geometry type (GLOSSARY.md)."
    )


def test_plate_dimensions_does_not_borrow_the_word_shape():
    """`plate_shape` read as though a plate were something the laser cuts."""
    from qupath_to_lmd import plate

    assert hasattr(plate, "plate_dimensions"), "plate.plate_dimensions is missing."
    assert not hasattr(plate, "plate_shape"), (
        "plate.plate_shape is back. In this app a shape is something the laser cuts, so a "
        "plate's rows and columns are its dimensions (GLOSSARY.md)."
    )


@pytest.mark.parametrize(
    "term",
    ["shape", "object", "polygon", "class", "group", "replicate", "well",
     "calibration point", "pixel size", "neighbour", "collection", "collector", "tube"],
)
def test_the_glossary_defines_every_canonical_term(term):
    """A term used in the interface but absent from the glossary is a term nobody can look up."""
    assert GLOSSARY.exists(), "GLOSSARY.md is missing."
    text = GLOSSARY.read_text().lower()
    assert f"**{term}**" in text, (
        f"GLOSSARY.md does not define {term!r}. Every term the app uses in its messages should "
        "be defined there, or a reader has no way to check what it means."
    )


def test_the_glossary_is_linked_from_the_places_people_start():
    """An unlinked glossary is a file nobody opens."""
    for document in ("README.md", "CLAUDE.md"):
        text = (ROOT / document).read_text()
        assert "GLOSSARY.md" in text, (
            f"{document} does not link GLOSSARY.md, so neither users nor contributors will find it."
        )


def test_qupath_field_names_are_left_alone():
    """`objectType` is QuPath's field, not our vocabulary. Renaming it would break reading."""
    text = (ROOT / "src" / "qupath_to_lmd" / "geojson.py").read_text()
    assert '"objectType"' in text or "'objectType'" in text, (
        "The reader no longer refers to QuPath's `objectType` field. That field name comes from "
        "QuPath and must match the file exactly, whatever we call things internally."
    )


# Words that legitimately appear in UI source for reasons other than naming a cuttable thing.
ALLOWED_IN_UI = ("objectType", "objective")


def _ui_strings(path: pathlib.Path) -> list[tuple[int, str]]:
    """Lines of a UI module that contain a quoted string, with their line numbers."""
    return [
        (number, line)
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if ('"' in line or "'" in line) and not line.lstrip().startswith("#")
    ]


@pytest.mark.parametrize("module", UI_MODULES)
def test_no_user_facing_text_says_object(module):
    """A user reading one screen should not see three words for the same thing.

    The distinction between "QuPath object" and "our shape" is real in the code, but invisible
    and unhelpful in a message. Jose caught the app saying "5608 objects have no QuPath
    classification" two lines from "8537 shapes available" (`decisions.md` 063).
    """
    path = ROOT / "src" / "qupath_to_lmd" / module
    offenders = [
        f"  line {number}: {line.strip()[:90]}"
        for number, line in _ui_strings(path)
        if re.search(r"\bobjects?\b", line, re.IGNORECASE)
        and not any(allowed in line for allowed in ALLOWED_IN_UI)
    ]
    assert not offenders, (
        f"{module} shows the user the word 'object'. In messages, everything the app reads or "
        "cuts is a shape — 'object' is QuPath's word and belongs in code and docs only.\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("module", UI_MODULES)
def test_no_user_facing_text_leads_with_a_geometry_type(module):
    """"14145 Polygons" told the user about shapely, not about their tissue.

    Geometry types may be *mentioned* to explain a problem — "several separate outlines
    (MultiPolygon geometry)" — but a count of them is not a useful thing to show.
    """
    path = ROOT / "src" / "qupath_to_lmd" / module
    offenders = [
        f"  line {number}: {line.strip()[:90]}"
        for number, line in _ui_strings(path)
        if re.search(r"\{[^}]*\}\s*(Polygon|Point|LineString)s?\b", line)
        or re.search(r"(Polygon|LineString)s\b(?!.*geometry)", line)
        and "MultiPolygon geometry" not in line
    ]
    assert not offenders, (
        f"{module} reports counts of geometry types to the user. Report shapes; mention a "
        "geometry type only to explain why something cannot be cut.\n" + "\n".join(offenders)
    )


def test_the_shape_count_is_reported_by_the_reader():
    """The count shown after upload comes from the report, so it is testable."""
    from qupath_to_lmd import geojson

    gdf, _points, report = geojson.read_and_qc("demo_Qupath_project/Single_cells.geojson")
    assert report.n_shapes_in_file == 128, (
        f"The file holds 128 non-point geometries; the report says {report.n_shapes_in_file}."
    )
    assert report.n_shapes_in_file >= report.n_shapes_kept, (
        "More shapes were kept than the file contained, which cannot happen."
    )
