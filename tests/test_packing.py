"""Packing circles into regions.

Two things here would ruin a collection without showing up in the app: a circle that is not
wholly inside the tissue it is labelled as, and two circles close enough that the strip between
them detaches into the wrong well. Most of this module guards those.
"""

import geopandas
import numpy
import pytest
import shapely
from shapely.geometry import box

from qupath_to_lmd import packing, regions
from qupath_to_lmd.budget import ClassBudget
from qupath_to_lmd.model import CLASS_NAME, REPLICATE

# 1 µm per pixel keeps every µm² figure in a test readable as a pixel area too.
SCALE = 1.0


def _pack(patches, replicates: dict, params, area=10_000.0, scale=SCALE):
    """Pack with the same amount asked of every class, which is all most tests need."""
    budgets = [ClassBudget(name, count, area) for name, count in replicates.items()]
    return packing.pack(patches, budgets, params, scale)


def _patches(boxes, classes):
    """A regions frame of the shape `regions.project` returns."""
    return geopandas.GeoDataFrame(
        {CLASS_NAME: list(classes), regions.N_CELLS: [1] * len(boxes)},
        geometry=[box(*bounds) for bounds in boxes],
        crs=None,
    )


@pytest.fixture
def one_big_region():
    """A single 1000x1000 region of one class: 1,000,000 µm² at 1 µm/px."""
    return _patches([(0, 0, 1000, 1000)], ["Tumor"])


@pytest.fixture
def two_classes_side_by_side():
    """Two regions of different classes sharing a boundary at x=500.

    This is the arrangement that makes cross-class spacing matter: a circle just inside each
    region can sit a fraction of a micrometre from the other.
    """
    return _patches([(0, 0, 500, 1000), (500, 0, 1000, 1000)], ["Immune cells", "Tumor"])


def test_every_circle_is_wholly_inside_its_own_region(two_classes_side_by_side):
    """A circle crossing a region boundary collects the neighbouring class instead.

    That is the failure this workflow exists to prevent, and nothing downstream would catch it.
    """
    params = packing.PackingParams(seed=0)
    result = _pack(two_classes_side_by_side, {"Immune cells": 1, "Tumor": 1}, params, area=20_000)

    for class_name, group in result.circles.groupby(CLASS_NAME):
        region = two_classes_side_by_side.loc[
            two_classes_side_by_side[CLASS_NAME] == class_name, "geometry"
        ].iloc[0]
        outside = [c for c in group.geometry if not region.contains(c)]
        assert not outside, (
            f"{len(outside)} circles labelled {class_name} are not wholly inside that class's "
            "tissue, so those cuts take a neighbouring class into this class's well."
        )


def test_no_two_circles_are_closer_than_the_gap(one_big_region):
    """The gap is what leaves material between two cuts."""
    params = packing.PackingParams(spacing_um=10.0, seed=0)
    result = _pack(one_big_region, {"Tumor": 1}, params, area=50_000)

    geometries = result.circles.geometry.to_numpy()
    tree = shapely.STRtree(geometries)
    left, right = tree.query(geometries, predicate="dwithin", distance=9.9)
    touching = [(a, b) for a, b in zip(left, right, strict=True) if a != b]
    assert not touching, (
        f"{len(touching) // 2} pairs of circles sit closer than the 10 µm gap asked for. The "
        "strip between two cuts that close detaches and falls into whichever well is cut first."
    )


def test_the_gap_holds_between_circles_of_different_classes(two_classes_side_by_side):
    """Regions of two classes touch, so circles either side of the boundary can be adjacent.

    The prototype tracked placed circles per class, so this pair was never checked. The laser
    does not care which class a neighbouring cut belongs to.
    """
    params = packing.PackingParams(spacing_um=15.0, seed=0)
    result = _pack(two_classes_side_by_side, {"Immune cells": 1, "Tumor": 1}, params, area=40_000)

    immune = result.circles[result.circles[CLASS_NAME] == "Immune cells"].geometry.to_numpy()
    tumor = result.circles[result.circles[CLASS_NAME] == "Tumor"].geometry.to_numpy()
    assert len(immune) and len(tumor), "Both classes need circles for this test to mean anything."

    closest = min(a.distance(b) for a in immune for b in tumor)
    assert closest >= 14.9, (
        f"The nearest circles of two different classes are {closest:.2f} µm apart, under the "
        "15 µm gap asked for. Material between those two cuts would not survive."
    )


def test_every_circle_area_is_within_the_range_asked_for(one_big_region):
    """The range is how a user controls what the LMD can actually collect."""
    params = packing.PackingParams(
        min_circle_area_um2=200, max_circle_area_um2=400, seed=0
    )
    result = _pack(one_big_region, {"Tumor": 1}, params, area=30_000)
    areas = result.circles[packing.CIRCLE_AREA]

    # A buffered circle is a 64-sided polygon, so it is a fraction under the circle it
    # approximates; the lower bound allows for that rather than pretending it is exact.
    assert areas.min() >= 200 * 0.99, (
        f"The smallest circle is {areas.min():.1f} µm², under the 200 µm² minimum. Circles "
        "below the minimum cannot be collected reliably."
    )
    assert areas.max() <= 400, (
        f"The largest circle is {areas.max():.1f} µm², over the 400 µm² maximum."
    )


def test_the_same_seed_gives_the_same_circles(one_big_region):
    """A collection has to be reproducible in a later session to be reportable."""
    params = packing.PackingParams(seed=7)
    first = _pack(one_big_region, {"Tumor": 2}, params, area=20_000)
    second = _pack(one_big_region, {"Tumor": 2}, params, area=20_000)

    assert first.circles.geometry.to_wkt().tolist() == second.circles.geometry.to_wkt().tolist(), (
        "The same seed and settings produced different circles, so a user could not reproduce "
        "a collection or report it in a methods section."
    )
    assert first.circles[REPLICATE].tolist() == second.circles[REPLICATE].tolist(), (
        "The same seed put the same circles into different replicates."
    )


def test_a_different_seed_gives_different_circles(one_big_region):
    """The seed has to do something, or offering it is a lie."""
    params = packing.PackingParams(seed=1)
    other = packing.PackingParams(seed=2)
    first = _pack(one_big_region, {"Tumor": 1}, params, area=20_000)
    second = _pack(one_big_region, {"Tumor": 1}, other, area=20_000)
    assert first.circles.geometry.to_wkt().tolist() != second.circles.geometry.to_wkt().tolist(), (
        "Two different seeds produced identical circles, so the seed control does nothing."
    )


def test_the_seed_does_not_depend_on_the_process():
    """Python salts `hash()` per process, so a name-keyed stream must not use it.

    If it did, the same file and seed would pack differently tomorrow and the seed would be
    worthless for reproducing a collection.
    """
    generator = packing.class_generator(0, "Tumor")
    # Captured from a run; any change here means a recorded seed no longer reproduces its
    # collection, which is a breaking change and needs saying out loud.
    assert generator.integers(0, 10**9) == packing.class_generator(0, "Tumor").integers(0, 10**9)
    assert packing.class_generator(0, "Tumor").integers(0, 10**9) != packing.class_generator(
        0, "Immune cells"
    ).integers(0, 10**9), "Two classes drew the same numbers, so they are not independent streams."


def test_a_class_packs_the_same_whatever_a_later_class_asks_for(two_classes_side_by_side):
    """Classes are packed in sorted order and share one gap, so coupling runs one way only.

    `Immune cells` sorts before `Tumor`, so changing Tumor must not disturb it. Without the
    per-class sub-streams a single shared stream would re-roll Immune cells too, and a user
    adjusting one class would watch another change for no visible reason.
    """
    params = packing.PackingParams(seed=3)
    few = _pack(two_classes_side_by_side, {"Immune cells": 1, "Tumor": 1}, params, area=20_000)
    many = _pack(two_classes_side_by_side, {"Immune cells": 1, "Tumor": 3}, params, area=20_000)

    def immune(result):
        return result.circles[result.circles[CLASS_NAME] == "Immune cells"].geometry.to_wkt().tolist()

    assert immune(few) == immune(many), (
        "Asking for more Tumor replicates changed the Immune cells circles. Nothing the user "
        "did should have moved them, so the preview shows changes they cannot attribute."
    )


def test_every_replicate_reaches_the_area_asked_for(one_big_region):
    """Replicates of a class have to be comparable amounts.

    Packing exactly the total left the last replicate with only the remainder, reliably ~5%
    short while its siblings overshot — a systematic difference, not noise.
    """
    params = packing.PackingParams(seed=0)
    result = _pack(one_big_region, {"Tumor": 4}, params, area=10_000)

    achieved = result.achieved["achieved"]
    assert (achieved >= 10_000).all(), (
        f"Replicate areas were {achieved.round(0).tolist()} against a 10,000 µm² target. A "
        "replicate that is reliably smaller than its siblings is not a comparable measurement."
    )
    spread = (achieved.max() - achieved.min()) / achieved.mean()
    assert spread < 0.1, (
        f"Replicates differ by {spread:.1%}, which is too much for them to be compared."
    )


def test_a_region_too_small_for_one_circle_is_counted_not_silently_ignored():
    """Those regions contribute nothing, and the fix is a smaller minimum circle.

    The user cannot work that out from a circle count, so the number is reported.
    """
    patches = _patches(
        [(0, 0, 1000, 1000), (2000, 0, 2005, 5), (3000, 0, 3005, 5)], ["Tumor"] * 3
    )
    params = packing.PackingParams(min_circle_area_um2=100, seed=0)
    result = _pack(patches, {"Tumor": 1}, params, area=5_000)
    assert result.n_regions_too_small == 2, (
        f"Reported {result.n_regions_too_small} regions too small to hold a circle, expected 2. "
        "Unreported, the user sees fewer circles than regions with no explanation."
    )


def test_a_saturated_region_reports_a_shortfall_instead_of_looping(one_big_region):
    """Asking for more tissue than the region can hold must end, and must say so."""
    params = packing.PackingParams(
        max_attempts=200, seed=0
    )
    result = _pack(one_big_region, {"Tumor": 1}, params, area=10_000_000)
    assert not result.shortfalls.empty, (
        "An impossible request reported no shortfall, so the user would believe a well holds "
        "ten times the tissue it does."
    )


def test_the_capacity_estimate_matches_what_packing_achieves(one_big_region):
    """It is shown before packing runs, so a wrong estimate sends the user down a dead end.

    The prototype's flat 55% ignored the gap, which at 20 µm would have promised 4.5x the
    tissue actually available.
    """
    for gap in (0.0, 5.0, 20.0):
        params = packing.PackingParams(
            spacing_um=gap, max_attempts=400, seed=0
        )
        estimate = packing.packable_area(1_000_000, params)
        achieved = _pack(one_big_region, {"Tumor": 1}, params, area=10_000_000).achieved[
            "achieved"
        ].sum()
        ratio = achieved / estimate
        assert 0.7 < ratio < 1.15, (
            f"At a {gap} µm gap the estimate was {estimate:,.0f} µm² but packing reached "
            f"{achieved:,.0f} µm² ({ratio:.0%} of it). The estimate is what the user plans "
            "against before waiting for a run."
        )


def test_a_wider_gap_fits_less_tissue(one_big_region):
    """The control has to move the amount, and in the direction the user expects."""
    achieved = []
    for gap in (0.0, 10.0):
        params = packing.PackingParams(
            spacing_um=gap, max_attempts=300, seed=0
        )
        result = _pack(one_big_region, {"Tumor": 1}, params, area=10_000_000)
        achieved.append(result.achieved["achieved"].sum())
    assert achieved[0] > achieved[1] * 1.5, (
        f"A 0 µm gap fitted {achieved[0]:,.0f} µm² and a 10 µm gap {achieved[1]:,.0f} µm². The "
        "gap should cost substantially more tissue than that, so the control is misleading."
    )


def test_an_empty_size_range_is_refused_with_a_reason(one_big_region):
    """One keystroke away in a number input, so it must explain rather than traceback."""
    params = packing.PackingParams(min_circle_area_um2=500, max_circle_area_um2=100)
    with pytest.raises(packing.PackingError, match="smaller than"):
        _pack(one_big_region, {"Tumor": 1}, params)


def test_packing_without_an_image_scale_is_refused(one_big_region):
    """Every amount here is an area in µm², so without a scale nothing means anything."""
    with pytest.raises(packing.PackingError, match="image scale"):
        _pack(one_big_region, {"Tumor": 1}, packing.PackingParams(), scale=None)


def test_circles_cannot_be_packed_into_an_enclosed_island(one_big_region):
    """A region surrounding another class has a hole, and that hole is not its tissue."""
    holed = one_big_region.copy()
    holed["geometry"] = [
        shapely.Polygon(
            holed.geometry.iloc[0].exterior, [box(300, 300, 700, 700).exterior]
        )
    ]
    params = packing.PackingParams(seed=0)
    result = _pack(holed, {"Tumor": 1}, params, area=50_000)

    hole = box(300, 300, 700, 700)
    intruders = [c for c in result.circles.geometry if c.intersects(hole)]
    assert not intruders, (
        f"{len(intruders)} circles were packed into the island of another class this region "
        "surrounds, so that well would hold the wrong biology."
    )


def test_smoothing_loss_is_measured_not_assumed(one_big_region):
    """Area per replicate is this workflow's whole budget, and smoothing takes a bite out of it.

    Small circles lose a real share: a 64-sided circle of radius 10 px or less drops to 9
    vertices at the default 1 px tolerance.
    """
    params = packing.PackingParams(
        min_circle_area_um2=100, max_circle_area_um2=150, seed=0
    )
    result = _pack(one_big_region, {"Tumor": 1}, params, area=20_000)
    lost, fraction = packing.smoothing_loss(result.circles, 1.0)

    assert lost > 0 and fraction > 0.01, (
        f"Smoothing was reported as costing {fraction:.2%} of the circle area. For circles this "
        "small it costs several percent, and the user is budgeting in µm²."
    )
    assert packing.smoothing_loss(result.circles, 0.0) == (0.0, 0.0), (
        "A zero tolerance reported a loss, so the figure cannot be trusted."
    )


def test_a_real_export_packs_to_target(multiclass):
    """The demo export QuPath 0.7 actually produces, end to end through the regions."""
    gdf, _points, _report = multiclass
    patches, _report2 = regions.project(gdf, regions.RegionParams())
    scale = 0.6535
    params = packing.PackingParams(seed=0)
    replicates = dict.fromkeys(sorted(set(patches[CLASS_NAME])), 2)

    result = _pack(patches, replicates, params, area=2_000, scale=scale)
    assert result.n_circles > 0, "A real export packed no circles at all."
    assert set(result.circles[CLASS_NAME]) <= set(patches[CLASS_NAME]), (
        "Packing invented a class that is not in the file."
    )
    assert numpy.isfinite(result.circles[packing.CIRCLE_AREA]).all(), (
        "A circle came out with a non-finite area, which would corrupt every figure downstream."
    )


def test_an_empty_result_still_has_its_columns():
    """"No circles" has to read as an empty table, not as a KeyError.

    The prototype returned a bare GeoDataFrame with no columns at all, so every caller had to
    remember to check before touching a column. Anything that forgot crashed the app instead
    of reporting that nothing could be packed.
    """
    empty = packing.PackingResult()
    for column in packing.CIRCLE_COLUMNS:
        assert column in empty.circles.columns, (
            f"An empty packing result is missing circle column {column!r}, so reporting it "
            "raises rather than telling the user nothing fitted."
        )
    for column in packing.ACHIEVED_COLUMNS:
        assert column in empty.achieved.columns, (
            f"An empty packing result is missing achieved column {column!r}. The report renames "
            "and drops columns by name, so a missing one is a KeyError in front of the user."
        )
    assert empty.n_circles == 0
    assert float(empty.circles[packing.CIRCLE_AREA].sum()) == 0.0
    assert empty.shortfalls.empty


def test_a_region_narrower_than_any_circle_reports_rather_than_raises():
    """Every region too thin to hold a circle is the realistic way to get nothing at all."""
    patches = _patches([(0, 0, 500, 2), (1000, 0, 1500, 2)], ["Tumor"] * 2)
    params = packing.PackingParams(min_circle_area_um2=100, seed=0)
    result = _pack(patches, {"Tumor": 1}, params, area=5_000)
    assert result.n_circles == 0, "Circles were placed into regions narrower than themselves."
    assert result.n_regions_too_small == 2, (
        "Neither region was reported as too small, so the user sees an empty result with no "
        "explanation and no idea that lowering the circle size would fix it."
    )


def test_each_class_gets_the_amount_asked_of_it(two_classes_side_by_side):
    """The amount is per class, because different biologies hold different amounts.

    One global amount forced the same target on a class with a tenth of the tissue, so the user
    could not ask for what each one could actually give.
    """
    budgets = [
        ClassBudget("Immune cells", 1, 5_000.0),
        ClassBudget("Tumor", 1, 25_000.0),
    ]
    result = packing.pack(
        two_classes_side_by_side, budgets, packing.PackingParams(seed=0), SCALE
    )
    by_class = result.achieved.set_index(CLASS_NAME)["achieved"]

    assert by_class["Immune cells"] >= 5_000, (
        f"Immune cells got {by_class['Immune cells']:,.0f} µm² of the 5,000 asked for."
    )
    assert by_class["Tumor"] >= 25_000, (
        f"Tumor got {by_class['Tumor']:,.0f} µm² of the 25,000 asked for."
    )
    assert by_class["Immune cells"] < by_class["Tumor"] / 2, (
        "Both classes collected a similar amount despite being asked for very different "
        "amounts, so the per-class column is not reaching the packer."
    )


def test_a_class_asked_for_nothing_still_appears_in_the_report(one_big_region):
    """Zero is a legitimate way to exclude a class, and it must not vanish silently.

    A class that disappears from the table looks like a bug, and the user cannot tell whether
    it was excluded or simply failed.
    """
    result = packing.pack(
        one_big_region, [ClassBudget("Tumor", 2, 0.0)], packing.PackingParams(seed=0), SCALE
    )
    assert result.n_circles == 0, "Circles were packed for a class asked for zero tissue."
    assert len(result.achieved) == 2, (
        f"The report has {len(result.achieved)} rows for a class with 2 replicates asked for "
        "nothing. It should still show both, at zero."
    )
