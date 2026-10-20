"""Several slides into the same samples: the split between slides, and collecting slide by slide."""

import io
import zipfile

import pandas
import pytest

from qupath_to_lmd import budget, export, plate, qc, selection, slides
from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.model import CLASS_NAME
from qupath_to_lmd.slides import SlideStrategy
from tests.conftest import CELLS_FILE, CELLS_PIXEL_SIZE, MULTICLASS_FILE


def _available(**held):
    """A one-class availability table: slide name -> amount held."""
    return pandas.DataFrame({name: {"Tumor": amount} for name, amount in held.items()})


def _shares(result):
    return {name: items[0].per_replicate for name, items in result.items()}


@pytest.mark.parametrize("strategy", list(SlideStrategy))
@pytest.mark.parametrize("held", [10.0, 450.0, 10_000.0])
def test_one_slide_gets_exactly_what_was_asked(strategy, held):
    """One slide must behave as the app did before slides existed, including asking for more
    than the slide holds — the engine then reports the shortfall, as it always has."""
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 150)], _available(A=held), strategy, ["A"], BudgetMode.CELLS
    )
    assert _shares(result) == {"A": 150}, (
        f"With one slide the {strategy.value} split asked for {_shares(result)} instead of the "
        "user's 150 per replicate. A single-slide collection would then differ from today's."
    )


def test_priority_takes_the_first_slide_before_touching_the_next():
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 150)], _available(A=300, B=600), SlideStrategy.PRIORITY, ["A", "B"],
        BudgetMode.AREA,
    )
    assert _shares(result) == {"A": 100, "B": 50}, (
        f"Priority split 150 per replicate as {_shares(result)}. Slide A holds 100 per replicate, "
        "so it should give all of that and B only the remaining 50."
    )


def test_priority_follows_the_order_given_not_the_upload_order():
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 150)], _available(A=300, B=600), SlideStrategy.PRIORITY, ["B", "A"],
        BudgetMode.AREA,
    )
    assert _shares(result) == {"B": 150, "A": 0}, (
        f"With B first, B can cover the whole 150 per replicate, but the split was {_shares(result)}."
    )


def test_proportional_takes_from_each_slide_in_proportion_to_what_it_holds():
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 150)], _available(A=300, B=600), SlideStrategy.PROPORTIONAL, ["A", "B"],
        BudgetMode.AREA,
    )
    assert _shares(result) == pytest.approx({"A": 50, "B": 100}), (
        f"A holds a third of the tissue, so it should give a third of each replicate: {_shares(result)}."
    )


def test_equal_share_lets_the_other_slides_cover_a_slide_that_runs_out():
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 150)], _available(A=120, B=600), SlideStrategy.EQUAL, ["A", "B"],
        BudgetMode.AREA,
    )
    assert _shares(result) == pytest.approx({"A": 40, "B": 110}), (
        f"A can give only 40 per replicate, so B should cover the rest of 150: {_shares(result)}."
    )


@pytest.mark.parametrize("strategy", list(SlideStrategy))
def test_a_request_beyond_every_slide_asks_each_for_all_it_has(strategy):
    """The slides together hold less than asked. Every slide must be asked for at least all of
    its tissue, or the experiment comes up shorter than the slides could have made it."""
    result = slides.split_budgets(
        [ClassBudget("Tumor", 3, 500)], _available(A=300, B=600), strategy, ["A", "B"], BudgetMode.AREA
    )
    shares = _shares(result)
    assert shares["A"] >= 100 and shares["B"] >= 200, (
        f"{strategy.value} asked for {shares} when the slides hold 100 and 200 per replicate. A slide "
        "asked for less than it holds leaves tissue uncut while the sample is short."
    )
    assert sum(shares.values()) == pytest.approx(500), "The shares no longer add up to the request."


@pytest.mark.parametrize("strategy", list(SlideStrategy))
def test_shape_counts_split_into_whole_shapes_that_add_up(strategy):
    result = slides.split_budgets(
        [ClassBudget("Tumor", 2, 101)], _available(A=70, B=70, C=70), strategy, ["A", "B", "C"],
        BudgetMode.CELLS,
    )
    shares = _shares(result)
    assert all(share == int(share) for share in shares.values()), (
        f"Shape counts were split into fractions: {shares}. A replicate cannot take a third of a cell."
    )
    assert sum(shares.values()) == 101, f"The whole-shape split {shares} does not add up to 101."


def test_a_class_missing_from_a_slide_is_taken_from_the_others():
    available = pandas.DataFrame({"A": {"Tumor": 300.0, "Stroma": 0.0}, "B": {"Tumor": 300.0, "Stroma": 300.0}})
    result = slides.split_budgets(
        [ClassBudget("Stroma", 3, 60)], available, SlideStrategy.PROPORTIONAL, ["A", "B"], BudgetMode.AREA
    )
    assert _shares(result) == pytest.approx({"A": 0, "B": 60}), (
        f"Stroma exists only on B, yet the split was {_shares(result)}."
    )


def _zip(entries: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    buffer.seek(0)
    buffer.name = "export.zip"
    return buffer


def test_a_zip_of_exports_becomes_one_slide_per_geojson():
    """The QuPath project script exports one file per image; zipping that folder is the upload."""
    with open(CELLS_FILE, "rb") as cells, open(MULTICLASS_FILE, "rb") as multiclass:
        upload = _zip({
            "lmd_export/slide_A.geojson": cells.read(),
            "lmd_export/slide_B.geojson": multiclass.read(),
            "lmd_export/notes.txt": b"not a slide",
            "__MACOSX/lmd_export/._slide_A.geojson": b"resource fork",
        })
    read = slides.read_slides([upload])
    assert [slide.name for slide in read] == ["slide_A", "slide_B"], (
        f"The zip gave slides {[slide.name for slide in read]}. Non-GeoJSON files and macOS "
        "resource forks must be skipped, and every GeoJSON must become a slide."
    )
    assert len(read[0].gdf) > 0 and read[0].calibration_points, "A slide lost its shapes or points."


def test_two_uploads_with_the_same_name_stay_two_slides():
    read = slides.read_slides([CELLS_FILE, CELLS_FILE])
    assert [slide.name for slide in read] == ["Single_cells", "Single_cells_2"], (
        "Two slides with the same file name must get distinct names, or one would overwrite the "
        "other's calibration and plan."
    )


def test_a_zip_with_no_geojson_is_refused_with_its_name():
    with pytest.raises(slides.SlideError, match="export.zip"):
        slides.read_slides([_zip({"readme.txt": b"nothing"})])


@pytest.fixture
def two_copies():
    """The same slide twice. Their shapes overlap exactly in pixel space, which is the worst
    case for anything that would mix slides into one coordinate frame."""
    return slides.read_slides([CELLS_FILE, CELLS_FILE])


def _pools(read):
    return {slide.name: slide.gdf for slide in read}


def _scales(read):
    return dict.fromkeys((slide.name for slide in read), CELLS_PIXEL_SIZE)


def test_two_slides_supply_what_one_cannot(two_copies):
    pools = _pools(two_copies)
    one = next(iter(pools.values()))
    held = float(one[one[CLASS_NAME] == "single_cells_demo"].geometry.area.sum()) * CELLS_PIXEL_SIZE**2
    budgets = [ClassBudget("single_cells_demo", 1, 1.5 * held)]
    pooled = slides.select_across_slides(
        pools, budgets, BudgetMode.AREA, selection.SelectionParams(), _scales(two_copies)
    )
    sample = pooled.by_sample()
    assert sample["achieved"].iloc[0] >= 1.5 * held - 1e-6, (
        f"One sample asked for 1.5× what one slide holds and got {sample['achieved'].iloc[0]:.0f} of "
        f"{1.5 * held:.0f} µm² from two slides that hold 2× between them."
    )
    assert (sample[["Single_cells", "Single_cells_2"]] > 0).all(axis=None), (
        "Proportional pooling took nothing from one of two identical slides."
    )


def test_each_slide_runs_the_unchanged_selection_on_its_own_shapes(two_copies):
    """Stacking slides would make the copies' shapes neighbours of each other. Running the
    engine per slide must give, for each slide, exactly what the engine gives on that slide
    alone — the same shapes and the same neighbour count."""
    pools = _pools(two_copies)
    budgets = [ClassBudget("single_cells_demo", 2, 20)]
    params = selection.SelectionParams(seed=3)
    pooled = slides.select_across_slides(pools, budgets, BudgetMode.CELLS, params, _scales(two_copies))
    for name, result in pooled.per_slide.items():
        alone = selection.select(pools[name], pooled.shares[name], BudgetMode.CELLS, params, CELLS_PIXEL_SIZE)
        assert result.replicate_of.equals(alone.replicate_of), (
            f"Slide {name} selected different shapes when run alongside another slide. Slides "
            "must not influence each other's selection, or shapes on one slide are judged "
            "against neighbours that are on another."
        )
        assert result.n_with_collected_neighbour == alone.n_with_collected_neighbour


def test_each_slide_gets_its_own_xml_with_its_own_calibration_into_shared_wells(two_copies):
    pools = _pools(two_copies)
    budgets = [ClassBudget("single_cells_demo", 2, 10)]
    pooled = slides.select_across_slides(
        pools, budgets, BudgetMode.CELLS, selection.SelectionParams(), _scales(two_copies)
    )
    saw = plate.assign_wells(budget.group_keys(budgets), plate.acceptable_wells("384", margins=1))

    first, second = two_copies
    names = list(first.calibration_points)[:3]
    array = qc.triangle_qc(first.gdf, first.calibration_points, names).calibration_array
    calibration = {first.name: (names, array), second.name: (names, array + 50.0)}

    plans = slides.plans_for_slides(two_copies, pooled, saw, calibration, _scales(two_copies))
    xmls = {
        name: export.build_collection(plan, samples_and_wells=saw).xml for name, plan in plans.items()
    }

    for name, plan in plans.items():
        assert set(plan.wells_used) == set(saw.values()), (
            f"Slide {name} cuts into {plan.wells_used}, not the shared wells {sorted(saw.values())}. "
            "Pooling works only if the same sample lands in the same well from every slide."
        )
        assert (plan.calibration_array == calibration[name][1]).all(), (
            f"Slide {name}'s plan carries another slide's calibration, so its .xml would cut in the "
            "wrong place."
        )
    assert xmls[first.name] != xmls[second.name], (
        "Two slides with different calibration produced the same .xml."
    )


def test_the_sample_sheet_adds_each_slide_and_totals_them(two_copies):
    frames = _pools(two_copies)
    sample_set = slides.whole_shape_samples(frames, ["single_cells_demo"], _scales(two_copies))
    sheet = sample_set.sheet().set_index("sample")
    row = sheet.loc["single_cells_demo"]
    assert row["shapes"] == row["Single_cells shapes"] + row["Single_cells_2 shapes"] == 2 * 121, (
        f"Pooling two copies of a 121-cell slide should give 242 shapes in the sample: {row.to_dict()}"
    )
    assert row["µm²"] == pytest.approx(row["Single_cells µm²"] + row["Single_cells_2 µm²"])


def test_no_area_total_when_a_slide_has_no_scale(two_copies):
    """A total that silently leaves out a slide would understate the sample."""
    frames = _pools(two_copies)
    sample_set = slides.whole_shape_samples(frames, ["single_cells_demo"], {"Single_cells": CELLS_PIXEL_SIZE})
    assert "µm²" not in sample_set.sheet().columns, (
        "One slide has no scale, so a total µm² would count only the other slide's tissue."
    )


def test_one_well_per_shape_stays_distinct_across_slides(two_copies):
    """Without the slide in the name, cell 001 of every slide would share a well."""
    from qupath_to_lmd import geojson

    names = []
    for slide in two_copies:
        exploded = geojson.explode_classes(slide.gdf, ["single_cells_demo"], label=slide.name)
        names.append(set(exploded.loc[exploded["original_classification_name"] == "single_cells_demo", CLASS_NAME]))
    assert not (names[0] & names[1]), f"Exploded names collide across slides: {sorted(names[0] & names[1])[:3]}"


def test_the_sample_set_route_cuts_exactly_what_the_old_route_cut(cells, calibration):
    """Every method now ends in `SampleSet.plan`; for one slide it must equal the builder the
    golden harness checks, or the app would cut differently from its reference."""
    from qupath_to_lmd.model import plan_from_selection

    gdf, points, _report = cells
    budgets = [ClassBudget("single_cells_demo", 2, 10)]
    params = selection.SelectionParams(seed=2)
    result = selection.select(gdf, budgets, BudgetMode.CELLS, params, CELLS_PIXEL_SIZE)
    saw = plate.assign_wells(budget.group_keys(budgets), plate.acceptable_wells("384", margins=1))
    names = list(points)[:3]
    old, _ = plan_from_selection(
        gdf=gdf, replicate_of=result.replicate_of, wells=[], samples_and_wells=saw,
        calibration_names=names, calibration_array=calibration,
    )
    pooled = slides.select_across_slides({"A": gdf}, budgets, BudgetMode.CELLS, params, {"A": CELLS_PIXEL_SIZE})
    new = slides.selected_samples({"A": gdf}, pooled, budgets, BudgetMode.CELLS, {"A": CELLS_PIXEL_SIZE}).plan(
        "A", saw, names, calibration
    )
    assert export.build_collection(new, saw).xml == export.build_collection(old, saw).xml, (
        "The sample-set route wrote a different .xml from the plan builder the golden harness guards."
    )


def test_whole_shapes_do_not_repeat_the_sample_as_a_class(two_copies):
    """For whole shapes a sample is its class; a second identical column only adds width."""
    sheet = slides.whole_shape_samples(_pools(two_copies), ["single_cells_demo"], _scales(two_copies)).sheet()
    assert "class" not in sheet.columns, f"Whole-shape sheet still has a class column: {list(sheet.columns)}"


def test_replicates_keep_their_class_column(two_copies):
    pools = _pools(two_copies)
    budgets = [ClassBudget("single_cells_demo", 2, 5)]
    pooled = slides.select_across_slides(pools, budgets, BudgetMode.CELLS, selection.SelectionParams(), _scales(two_copies))
    sheet = slides.selected_samples(pools, pooled, budgets, BudgetMode.CELLS, _scales(two_copies)).sheet()
    assert list(sheet["class"].unique()) == ["single_cells_demo"] and "replicate" in sheet.columns
