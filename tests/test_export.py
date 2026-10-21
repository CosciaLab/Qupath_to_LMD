"""Building the XML, the cut order, and the download bundle."""

import json
import pathlib
import re
import zipfile

import numpy
import pytest

from qupath_to_lmd import export


def _caps(xml):
    return re.findall(r"<CapID>(.*?)</CapID>", xml)


def _coords(xml):
    return re.findall(r"<[XY]_\d+>(-?\d+)</[XY]_\d+>", xml)


def _runs(caps):
    return 1 + sum(1 for a, b in zip(caps, caps[1:], strict=False) if a != b)


def test_every_cut_order_is_a_permutation_of_all_shapes(make_plan):
    """Reordering must not lose or duplicate a shape. Either would change what gets collected."""
    plan, _saw, _result, _budgets = make_plan(replicates=2, per_replicate=8)
    selected = plan.selected
    for mode in export.PathOrder:
        order = export.order_for_cutting(selected, mode)
        assert sorted(order.tolist()) == list(range(len(selected))), (
            f"Cut order {mode.value!r} is not a permutation of the {len(selected)} selected "
            "shapes, so shapes are being dropped or repeated."
        )


def test_no_reordering_leaves_the_load_order_alone(make_plan):
    plan, _saw, _result, _budgets = make_plan()
    order = export.order_for_cutting(plan.selected, export.PathOrder.NONE)
    assert (order == numpy.arange(len(plan.selected))).all()


def test_the_default_shortens_the_path(make_plan):
    """Stage movement between shapes is a leading cause of cutting misalignment, so the default
    is the option that minimises it rather than the one that preserves historical output."""
    assert export.DEFAULT_PATH_ORDER is export.PathOrder.HILBERT, (
        f"The default cut order is {export.DEFAULT_PATH_ORDER.value!r}. It should shorten the path."
    )


def test_greedy_is_removed_not_merely_hidden():
    """Its dependency was dropped, so a lingering enum member would be a code path that raises
    ModuleNotFoundError if anything reached it.

    Checked against the declared dependencies rather than by attempting `import umap`: a stale
    virtualenv can still have the package even after it is undeclared, so an import test would
    disagree between a developer's machine and a fresh CI environment.
    """
    assert not hasattr(export.PathOrder, "GREEDY"), (
        f"PathOrder still offers GREEDY, but umap-learn is no longer a dependency. "
        f"Modes are {[m.value for m in export.PathOrder]}."
    )
    root = pathlib.Path(__file__).resolve().parent.parent
    for manifest in ("pyproject.toml", "requirements.txt"):
        text = (root / manifest).read_text()
        assert "umap" not in text, (
            f"{manifest} still declares umap-learn. It costs ~354 MB of JIT on first use and is "
            "reinstalled on every Community Cloud reboot, for a solver that is no longer offered."
        )
    source = (root / "src" / "qupath_to_lmd" / "export.py").read_text()
    assert "tsp_greedy_solve" not in source, (
        "export.py still imports or calls tsp_greedy_solve, which will raise ModuleNotFoundError "
        "now that umap-learn is gone."
    )


def test_grouping_moves_the_collector_once_per_well(make_plan):
    """Writing shapes in load order made the LMD move its collector 759 times for 900 shapes
    across 9 wells, where 8 would do."""
    plan, _saw, _result, _budgets = make_plan(replicates=2, per_replicate=8)
    selected = plan.selected
    wells = selected["well"].nunique()

    _, unordered_moves = export.path_stats(selected, export.order_for_cutting(selected, export.PathOrder.NONE))
    _, grouped_moves = export.path_stats(selected, export.order_for_cutting(selected, export.PathOrder.GROUPED))

    assert grouped_moves == wells - 1, (
        f"Grouping {wells} wells should need {wells - 1} collector movements; it needed {grouped_moves}."
    )
    assert grouped_moves <= unordered_moves, (
        f"Grouping increased collector movements from {unordered_moves} to {grouped_moves}."
    )


def test_hilbert_shortens_travel_relative_to_grouping_alone(make_plan):
    """Grouping fixes the collector but ignores position within a well, so it can lengthen the
    stage path. Shortening within each well fixes both."""
    plan, _saw, _result, _budgets = make_plan(replicates=2, per_replicate=12)
    selected = plan.selected
    grouped, _ = export.path_stats(selected, export.order_for_cutting(selected, export.PathOrder.GROUPED))
    hilbert, _ = export.path_stats(selected, export.order_for_cutting(selected, export.PathOrder.HILBERT))
    assert hilbert <= grouped, (
        f"Hilbert ordering travelled {hilbert:,.0f}px against plain grouping's {grouped:,.0f}px. "
        "Shortening within a well should not be worse than not shortening."
    )


def test_reordering_never_moves_a_shape_to_a_different_well(make_plan):
    """The safety property behind changing the default: the collection content is identical and
    only the sequence differs."""
    plan, samples_and_wells, _result, _budgets = make_plan(replicates=2, per_replicate=8)
    selected = plan.selected

    def pairs(mode):
        order = export.order_for_cutting(selected, mode)
        ordered = selected.iloc[order]
        return set(zip(ordered["shape_id"], ordered["well"], strict=True))

    assert pairs(export.PathOrder.NONE) == pairs(export.PathOrder.HILBERT), (
        "Reordering changed which shape goes to which well. Only the order may change."
    )


def test_the_xml_holds_the_same_shapes_in_a_different_order(make_plan):
    plan, samples_and_wells, _result, _budgets = make_plan(replicates=2, per_replicate=8)
    unordered = export.build_collection(plan, samples_and_wells, path_order=export.PathOrder.NONE)
    ordered = export.build_collection(plan, samples_and_wells, path_order=export.PathOrder.HILBERT)

    assert sorted(_coords(unordered.xml)) == sorted(_coords(ordered.xml)), (
        "Reordering changed the coordinates. It must only change the sequence."
    )
    assert sorted(_caps(unordered.xml)) == sorted(_caps(ordered.xml)), "Reordering changed the well assignments."
    assert unordered.xml != ordered.xml, "The two orders produced identical XML, so ordering did nothing."
    assert _runs(_caps(ordered.xml)) <= _runs(_caps(unordered.xml)), (
        "Ordering did not reduce the number of times the collector has to move."
    )


def test_higher_smoothing_tolerance_means_fewer_vertices(make_plan):
    """The outline may move by up to the tolerance; more tolerance means fewer points for the
    stage to trace, at the cost of following the annotation less exactly."""
    plan, samples_and_wells, _result, _budgets = make_plan()
    counts = {
        tolerance: export.build_collection(plan, samples_and_wells, simplify_tolerance=tolerance).n_vertices
        for tolerance in (0.0, 1.0, 5.0)
    }
    assert counts[0.0] > counts[1.0] > counts[5.0], (
        f"Vertex counts did not fall as tolerance rose: {counts}."
    )
    assert export.DEFAULT_SIMPLIFY_TOLERANCE == 1.0, (
        "The default tolerance changed. It has been 1 pixel since before this app was refactored, "
        "and changing it alters output for every existing user."
    )


def test_an_empty_plan_is_rejected(make_plan):
    """Nothing to cut is not a collection, and a zero-shape XML would look valid."""
    plan, samples_and_wells, _result, _budgets = make_plan()
    plan.shapes["well"] = None
    with pytest.raises(ValueError, match="nothing to cut"):
        export.build_collection(plan, samples_and_wells)


def test_the_bundle_carries_everything_needed_to_run_and_reproduce(make_plan):
    plan, samples_and_wells, _result, _budgets = make_plan()
    result = export.build_collection(plan, samples_and_wells, plate="384")
    buffer = export.build_bundle(plan, result, samples_and_wells, plate="384")
    names = zipfile.ZipFile(buffer).namelist()

    expected = {
        "Single_cells.xml": "the cutting file itself",
        "Single_cells_384_wellplate.csv": "the plate scheme to pipette from",
        "samples_and_wells.json": "the class-to-well mapping",
        "provenance.json": "every parameter that determined the output",
        "Single_cells_processed.geojson": "re-importable into QuPath",
        "collection.png": "the QC image",
    }
    for name, why in expected.items():
        assert name in names, f"The bundle is missing {name} ({why}). Contents: {names}"

    provenance = json.loads(zipfile.ZipFile(buffer).read("provenance.json"))
    assert provenance["shapes_selected"] == result.n_shapes


def test_the_plate_csv_is_named_for_the_plate_actually_used(make_plan):
    """It was always called `_384_wellplate.csv` even for 96-well runs."""
    plan, samples_and_wells, _result, _budgets = make_plan()
    result = export.build_collection(plan, samples_and_wells, plate="96")
    names = zipfile.ZipFile(export.build_bundle(plan, result, samples_and_wells, plate="96")).namelist()
    assert any(name.endswith("_96_wellplate.csv") for name in names), (
        f"A 96-well run produced {[n for n in names if n.endswith('.csv')]}."
    )


def test_the_orientation_transform_flips_y(make_plan):
    """QuPath image coordinates grow downward and the LMD stage does not. Without the flip the
    collection is mirrored and every cut lands in the wrong place."""
    assert export.ORIENTATION_TRANSFORM.tolist() == [[1, 0], [0, -1]], (
        f"The orientation transform is {export.ORIENTATION_TRANSFORM.tolist()}, not a Y flip. "
        "This silently mis-cuts every collection."
    )


def test_no_expansion_cuts_exactly_what_was_cut_before(make_plan):
    """Expansion is opt-in: at zero the XML must be byte-identical, or every existing user's
    collection moves without them asking."""
    plan, samples_and_wells, _result, _budgets = make_plan()
    assert (
        export.build_collection(plan, samples_and_wells).xml
        == export.build_collection(plan, samples_and_wells, expand_px=0.0).xml
    ), "Expanding by zero changed the cutting file."


def test_expanding_moves_every_outline_out_by_the_distance():
    """The laser path should sit exactly the asked-for distance outside the drawn outline, all the
    way round — closer and it still burns the cell's edge, further and it takes the neighbour."""
    import geopandas
    from shapely.geometry import LineString, box

    square = box(0, 0, 10, 10)
    grown = export.expand_shapes(geopandas.GeoSeries([square]), 2.0).iloc[0]
    assert grown.contains(square), "The expanded outline does not contain the shape it came from."
    # the rounded corners are chords of a circle, so the closest point sits a hair inside 2 px
    assert square.exterior.distance(grown.exterior) == pytest.approx(2.0, abs=0.02), (
        "The cut path is not 2 px outside the outline at its closest point."
    )
    assert grown.area == pytest.approx(100 + 4 * 10 * 2 + numpy.pi * 4, rel=0.01), (
        f"A 10 px square grown by 2 px should have rounded corners and area ~192.6, got {grown.area:.1f}."
    )
    line = LineString([(0, 0), (10, 0)])
    assert export.expand_shapes(geopandas.GeoSeries([line]), 2.0).iloc[0].equals(line), (
        "A cut line was expanded into a loop, so the laser would cut round the line instead of along it."
    )


def test_an_expanded_collection_cuts_wider_outlines(make_plan):
    plan, samples_and_wells, _result, _budgets = make_plan()
    plain = export.build_collection(plan, samples_and_wells, path_order=export.PathOrder.NONE)
    grown = export.build_collection(plan, samples_and_wells, path_order=export.PathOrder.NONE, expand_px=3.0)
    assert grown.xml != plain.xml, "Expanding by 3 px left the cutting file unchanged."
    assert _caps(grown.xml) == _caps(plain.xml), "Expanding moved a shape to a different well."


def _wells(chain, pattern):
    import pandas

    return pandas.Series([pattern[i % len(pattern)] for i in range(len(chain))], index=chain.index)


def test_expanded_neighbours_in_different_wells_are_counted(near_touching_chain):
    """Squares 0.5 px apart, alternating wells: grown by more than half the gap, every one overlaps
    a square bound for the other well, which mixes the two samples."""
    wells = _wells(near_touching_chain, ["C3", "C5"])
    overlapping = export.expansion_report(near_touching_chain, wells, 0.3)
    assert overlapping.n_into_other_sample == len(near_touching_chain), (
        f"Expected all {len(near_touching_chain)} squares to overlap another sample, got {overlapping.n_into_other_sample}."
    )
    clear = export.expansion_report(near_touching_chain, wells, 0.2)
    assert clear.n_into_other_sample == 0, "Outlines grown by less than half the gap were reported as overlapping."
    assert clear.added_fraction > 0


def test_reaching_into_a_shape_that_is_not_collected_is_counted(near_touching_chain):
    """Every other square collected, all into one well: grown past the gap each one takes a rim of
    an uncollected neighbour, but no two samples meet."""
    import pandas

    wells = _wells(near_touching_chain, ["C3", None]).where(lambda s: s.notna(), pandas.NA)
    report = export.expansion_report(near_touching_chain, wells, 0.6)
    assert report.n_shapes == 6
    assert report.n_into_uncollected == 6, f"Expected all 6 collected squares to reach a neighbour, got {report.n_into_uncollected}."
    assert report.n_into_other_sample == 0, "Shapes 10.5 px apart in one well were reported as overlapping another sample."


def test_the_default_expansion_is_one_micrometre():
    """Changing it changes what a scientist gets when they tick the box: write a decision first."""
    assert export.DEFAULT_EXPANSION_UM == 1.0
