"""Projecting cells into regions, and merging them by class.

The assertions here guard two things that would mis-cut tissue silently: a region carrying the
wrong class, and a region reaching out onto blank slide.
"""

import geopandas
import numpy
import pandas
import pytest
import shapely
from shapely.geometry import Point, box

from qupath_to_lmd import geojson, regions
from qupath_to_lmd.model import CLASS_NAME

SPACING = 10.0


def _cells(positions, classes, radius=2.0):
    """A QuPath-shaped frame of round cells at the given positions."""
    return geopandas.GeoDataFrame(
        {
            CLASS_NAME: list(classes),
            "id": [f"c{i}" for i in range(len(positions))],
            "objectType": ["cell"] * len(positions),
            "classification": [str({"name": name, "color": [1, 2, 3]}) for name in classes],
        },
        geometry=[Point(x, y).buffer(radius) for x, y in positions],
        crs=None,
    )


@pytest.fixture
def grid():
    """A 5x5 grid of cells, all one class except the middle one.

    The enclosed class is what produces a hole once the outer class is merged, which is the
    case the export path cannot cut faithfully.
    """
    positions = [(i * SPACING, j * SPACING) for j in range(5) for i in range(5)]
    classes = ["Tumor"] * len(positions)
    classes[12] = "Immune cells"
    return _cells(positions, classes)


@pytest.fixture
def cluster_with_outlier():
    """A dense 4x4 cluster plus one cell far away on its own."""
    positions = [(i * SPACING, j * SPACING) for j in range(4) for i in range(4)]
    positions.append((900.0, 900.0))
    return _cells(positions, ["Tumor"] * len(positions))


def test_every_cell_gets_its_own_region(grid):
    """One region per cell is what lets a region carry that cell's class."""
    result = regions.voronoi_regions(grid, regions.RegionParams())
    assert len(result) == len(grid), (
        f"{len(grid)} cells produced {len(result)} regions. A region count that does not match "
        "the cells means tissue is either unassigned or assigned twice."
    )


def test_each_region_belongs_to_its_own_cell(grid):
    """`voronoi_polygons(ordered=True)` is what maps classes onto regions.

    If that contract breaks, every region silently carries a neighbour's class and the laser
    cuts the wrong biology into every well.
    """
    result = regions.voronoi_regions(grid, regions.RegionParams())
    cells = numpy.c_[grid.geometry.centroid.x.to_numpy(), grid.geometry.centroid.y.to_numpy()]

    # Every point of a Voronoi region is nearer its own cell than any other, and intersecting
    # with the cap and the hull cannot break that. So the nearest cell to a point inside region
    # i must be cell i. Checking the region merely *contains* its cell would not do: a cell on
    # the rim sits on the hull boundary, which is its region's edge rather than its interior.
    misplaced = []
    for position, index in enumerate(result.index):
        inside = result.geometry[index].representative_point()
        nearest = int(numpy.argmin(numpy.linalg.norm(cells - [inside.x, inside.y], axis=1)))
        if nearest != position:
            misplaced.append(index)

    assert not misplaced, (
        f"{len(misplaced)} regions are nearer another cell than the one they were built from, so "
        "they carry the wrong class. Every well would then hold the wrong tissue."
    )


def test_regions_are_single_polygons(grid):
    """The export path cuts one closed outline per shape, so a MultiPolygon cannot be cut."""
    result = regions.voronoi_regions(grid, regions.RegionParams())
    types = set(result.geometry.geom_type)
    assert types == {"Polygon"}, (
        f"Regions came out as {types}. Anything other than a single polygon is dropped by the "
        "reader, so that tissue would go uncollected without being reported."
    )


def test_a_lone_cell_cannot_claim_the_empty_slide_around_it(cluster_with_outlier):
    """The radius cap exists so a cut is never placed on blank glass.

    Without it the outlier's Voronoi region stretches across everything between the cluster and
    the outlier, and a shape placed there cuts slide rather than tissue.
    """
    params = regions.RegionParams(radius_factor=1.0)
    result = regions.voronoi_regions(cluster_with_outlier, params)
    radius, _median_nn = regions.radius_from_spacing(
        numpy.c_[
            cluster_with_outlier.geometry.centroid.x.to_numpy(),
            cluster_with_outlier.geometry.centroid.y.to_numpy(),
        ],
        params,
    )

    outlier = result.geometry.iloc[-1]
    assert outlier.area <= numpy.pi * radius**2 + 1e-6, (
        f"The lone cell's region is {outlier.area:.0f}px², larger than the "
        f"{numpy.pi * radius**2:.0f}px² the radius cap allows. Cuts would be placed on blank "
        "slide between the tissue and this cell."
    )


def test_cells_at_the_same_position_are_reported_not_dropped_silently(grid):
    """Two cells cannot both own the territory around one point, so one has to go.

    Saying nothing would leave the user with fewer regions than cells and no explanation.
    """
    duplicated = geopandas.GeoDataFrame(
        pandas.concat([grid, grid.iloc[[0]]], ignore_index=True),
        geometry="geometry",
        crs=None,
    )
    _patches, report = regions.project(duplicated, regions.RegionParams())
    assert report.n_duplicate_centroids == 1, (
        f"Reported {report.n_duplicate_centroids} cells sharing a position, expected 1. An "
        "unreported duplicate is a cell the user thinks is being collected and is not."
    )


def test_too_few_cells_is_refused_with_a_reason(grid):
    """Two cells have no area between them, so there is no region to cut and no point going on."""
    with pytest.raises(regions.RegionError, match="at least"):
        regions.voronoi_regions(grid.iloc[:2], regions.RegionParams())


def test_an_unwanted_class_still_holds_its_own_territory(grid):
    """Classes are dropped after the tessellation, never before.

    Filtering first would let the kept classes expand into the dropped class's tissue, so a
    well labelled Tumor would hold immune tissue. This is the whole reason `project` takes
    `include` instead of being handed a pre-filtered frame.
    """
    everything, _ = regions.project(grid, regions.RegionParams())
    tumor_only, _ = regions.project(grid, regions.RegionParams(), include=["Tumor"])

    from_everything = everything[everything[CLASS_NAME] == "Tumor"].geometry.area.sum()
    assert tumor_only.geometry.area.sum() == pytest.approx(from_everything), (
        "Excluding a class changed the area of the classes that were kept, so the kept classes "
        "absorbed the excluded class's tissue. Those wells would hold the wrong biology."
    )


def test_merging_joins_touching_regions_of_one_class(grid):
    """A class occupies contiguous areas, and each area is one thing to cut."""
    result = regions.voronoi_regions(grid, regions.RegionParams())
    patches, _filled = regions.merge_by_class(result)
    assert len(patches) < len(result), (
        f"{len(result)} regions merged into {len(patches)} patches — no merging happened. The "
        "user would be asked to cut every cell's territory separately."
    )
    assert set(patches[CLASS_NAME]) == set(result[CLASS_NAME]), (
        "Merging lost or invented a class, so a class the user chose would not be collected."
    )


def test_merging_conserves_area(grid):
    """Merging joins tissue; it must not gain or lose any."""
    result = regions.voronoi_regions(grid, regions.RegionParams())
    patches, _filled = regions.merge_by_class(result)
    assert patches.geometry.area.sum() == pytest.approx(result.geometry.area.sum(), rel=1e-9), (
        "The merged area differs from the regions it came from, so the amount of tissue the "
        "user is told they will collect is wrong."
    )


def test_patch_order_does_not_depend_on_the_union(grid):
    """GEOS decides what order a union comes out in, and that order picks the wells.

    Pinning it is what makes the same file produce the same plate in a later session.
    """
    result = regions.voronoi_regions(grid, regions.RegionParams())
    first, _ = regions.merge_by_class(result)
    second, _ = regions.merge_by_class(result.iloc[::-1])
    assert list(first[CLASS_NAME]) == list(second[CLASS_NAME]), (
        "Patch order changed with the input order, so the same cells would land in different "
        "wells between two sessions."
    )
    assert first.geometry.area.round(6).tolist() == second.geometry.area.round(6).tolist(), (
        "Patch order changed with the input order, so the same cells would land in different "
        "wells between two sessions."
    )


def test_an_enclosed_class_becomes_a_reported_hole(grid):
    """The export path cuts a shape's outer outline only, so a hole is cut through.

    That means collecting the enclosed class along with the surrounding one. It is reported
    rather than blocked, but it must never be silent.
    """
    _patches, report = regions.project(grid, regions.RegionParams(), include=["Tumor"])
    assert report.n_patches_with_holes == 1, (
        f"Reported {report.n_patches_with_holes} regions with an enclosed island, expected 1. "
        "An unreported hole is tissue of another class cut into this class's well."
    )
    assert report.hole_area_px2 > 0, (
        "An enclosed island was found but its area was reported as zero, so the user cannot "
        "judge how much foreign tissue would end up in the well."
    )


def test_the_cell_count_per_region_adds_up(grid):
    """A region of two cells and a region of two hundred are different things to collect."""
    patches, report = regions.project(grid, regions.RegionParams())
    assert int(patches[regions.N_CELLS].sum()) == report.n_cells_kept, (
        f"Cells per region sum to {int(patches[regions.N_CELLS].sum())} but "
        f"{report.n_cells_kept} cells were used. The count on screen would not match the file."
    )


def test_dealing_balances_area_between_replicates():
    """Replicates of one class should be comparable in amount.

    Dealing in order would give replicate 1 every large patch, so replicates would differ by
    an order of magnitude and would not be comparable measurements.
    """
    patches = geopandas.GeoDataFrame(
        {CLASS_NAME: ["Tumor"] * 4},
        geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 48, 8), box(60, 0, 68, 8)],
        crs=None,
    )
    dealt = regions.deal_patches(patches, {"Tumor": 2})
    areas = patches.geometry.area.groupby(dealt).sum()
    assert areas.min() / areas.max() > 0.8, (
        f"Replicate areas are {areas.to_dict()}, which differ by more than 20%. Replicates that "
        "unequal are not comparable measurements."
    )


def test_dealing_is_reproducible(grid):
    """No randomness: the same regions always reach the same wells."""
    patches, _ = regions.project(grid, regions.RegionParams())
    first = regions.deal_patches(patches, {"Tumor": 2, "Immune cells": 2})
    second = regions.deal_patches(patches, {"Tumor": 2, "Immune cells": 2})
    assert first.equals(second), (
        "Dealing the same regions twice gave different replicates, so a collection could not "
        "be reproduced or reported in a methods section."
    )


def test_a_class_with_fewer_regions_than_replicates_leaves_replicates_empty(grid):
    """Asking for more replicates than there are regions cannot be satisfied.

    The empty replicates still have to be visible, because the user asked for them.
    """
    patches, _ = regions.project(grid, regions.RegionParams(), include=["Immune cells"])
    dealt = regions.deal_patches(patches, {"Immune cells": 3})
    assert dealt.nunique() < 3, (
        "Three replicates were filled from fewer regions than that, so a region was collected "
        "into more than one well."
    )


def test_synthesized_shapes_survive_a_qupath_round_trip(grid):
    """The processed GeoJSON has to re-open in QuPath, and these shapes have no QuPath id."""
    patches, _ = regions.project(grid, regions.RegionParams())
    with_fields = geojson.synthesize_qupath_columns(patches, "region", source=grid)

    assert with_fields["id"].is_unique, (
        "Synthesized ids are not unique, so QuPath would merge or reject regions on re-import."
    )
    sanitized = geojson.sanitize_for_qupath(with_fields)
    assert list(sanitized.columns) == ["id", "objectType", "classification", "geometry"], (
        f"Sanitized columns are {list(sanitized.columns)}. Missing any of these means the "
        "processed GeoJSON in the download will not re-open in QuPath."
    )
    assert sanitized["classification"].map(geojson._classification_name).tolist() == list(
        patches[CLASS_NAME]
    ), (
        "Class names did not survive the round trip, so a user re-importing the regions would "
        "see the wrong labels on them."
    )


def test_regions_keep_the_class_colour_qupath_assigned(grid):
    """A re-imported file should look like the one the user exported, not a set of grey outlines."""
    patches, _ = regions.project(grid, regions.RegionParams())
    with_fields = geojson.synthesize_qupath_columns(patches, "region", source=grid)
    assert any("color" in str(value) for value in with_fields["classification"]), (
        "No class colour was carried over, so every region re-imports into QuPath uncoloured "
        "and the user cannot tell the classes apart."
    )


def test_a_real_qupath_export_projects_and_merges(multiclass):
    """The demo export QuPath 0.7 actually produces, multi-class names and all."""
    gdf, _points, _report = multiclass
    patches, report = regions.project(gdf, regions.RegionParams())

    assert report.n_patches > 0, "A real QuPath export produced no regions at all."
    assert set(patches.geometry.geom_type) == {"Polygon"}, (
        "A real export produced regions the export path cannot cut."
    )
    hull = regions.tissue_hull(gdf)
    assert patches.geometry.apply(lambda geometry: hull.buffer(1e-6).contains(geometry)).all(), (
        "A region reaches outside the area the cells cover, so a cut would be placed on blank "
        "slide."
    )


def test_pinhole_gaps_from_the_radius_cap_are_filled_not_warned_about(grid):
    """The cap is a 64-sided polygon, so capped regions leave gaps of a pixel where three meet.

    Those are not enclosed tissue. Reporting them as such would raise a warning about one pixel
    and teach the user to ignore the warning when a real class really is enclosed.
    """
    result = regions.voronoi_regions(grid, regions.RegionParams(radius_factor=1.0))
    patches, filled = regions.merge_by_class(
        result, minimum_hole_area=float(result.geometry.area.min())
    )
    remaining = [
        shapely.Polygon(ring).area for geometry in patches.geometry for ring in geometry.interiors
    ]
    assert not any(area < result.geometry.area.min() for area in remaining), (
        f"Holes smaller than the smallest region survived: {remaining}. Each one raises a "
        "warning about tissue that is not there."
    )
    assert filled >= 0


def test_filling_slivers_never_swallows_a_real_enclosed_class(grid):
    """The threshold is the smallest region in the tessellation, so a real region always clears it.

    If it did not, another class's tissue would be silently folded into this class's well with
    no warning at all — worse than the false warning the threshold exists to prevent.
    """
    patches, report = regions.project(grid, regions.RegionParams(), include=["Tumor"])
    assert report.n_patches_with_holes == 1, (
        f"The enclosed class's region was filled in as a sliver ({report.n_slivers_filled} "
        "filled), so its tissue would be cut into the Tumor well without a word."
    )
    assert report.hole_area_px2 >= report.smallest_region_px2, (
        f"The surviving hole ({report.hole_area_px2:.1f}px²) is smaller than the smallest region "
        f"({report.smallest_region_px2:.1f}px²), so the threshold is not doing what it claims."
    )


def test_filling_a_sliver_does_not_move_the_outline(grid):
    """Only interior rings are dropped; the outline the laser follows must be untouched."""
    result = regions.voronoi_regions(grid, regions.RegionParams(radius_factor=1.0))
    before, _ = regions.merge_by_class(result)
    after, _ = regions.merge_by_class(result, minimum_hole_area=float(result.geometry.area.min()))
    assert [g.exterior.length for g in before.geometry] == pytest.approx(
        [g.exterior.length for g in after.geometry]
    ), "Filling a sliver changed a region's outer outline, so the cut path would move."


def test_no_two_regions_share_any_tissue(grid):
    """The invariant the whole workflow rests on: one piece of tissue, one well.

    If two regions overlapped, the laser would cut the same tissue twice and two wells would
    contain the same material — and on a picture that reads as a broken merge, which is how this
    came to be checked. Same-class regions may touch at a corner, and different-class regions
    must share boundaries; neither is an overlap.
    """
    patches, _report = regions.project(grid, regions.RegionParams())
    geometries = patches.geometry.to_numpy()
    tree = shapely.STRtree(geometries)
    left, right = tree.query(geometries, predicate="intersects")

    worst = 0.0
    for i, j in zip(left, right, strict=True):
        if i < j:
            worst = max(worst, geometries[i].intersection(geometries[j]).area)

    assert worst == pytest.approx(0.0, abs=1e-9), (
        f"Two regions share {worst:.6f} px² of tissue. The laser would cut it twice and two "
        "wells would hold the same material."
    )


def test_same_class_regions_only_ever_touch_at_a_point(grid):
    """Anything more than a point contact between same-class regions means the merge left work.

    Separate components of a union may legitimately meet at isolated corners; sharing an edge
    would mean two regions that should have been one.
    """
    patches, _report = regions.project(grid, regions.RegionParams())
    geometries = patches.geometry.to_numpy()
    classes = patches[CLASS_NAME].to_numpy()
    tree = shapely.STRtree(geometries)
    left, right = tree.query(geometries, predicate="intersects")

    offenders = []
    for i, j in zip(left, right, strict=True):
        if i >= j or classes[i] != classes[j]:
            continue
        kind = geometries[i].intersection(geometries[j]).geom_type
        if kind not in ("Point", "MultiPoint"):
            offenders.append((int(i), int(j), kind))

    assert not offenders, (
        f"Same-class regions meet along more than a point: {offenders[:5]}. They should have "
        "been merged into one region, so the user is being asked to cut one area as two."
    )


def test_a_gap_wider_than_the_cap_is_left_unassigned():
    """Empty space inside tissue — a lumen, a vessel, a tear — must not be handed to a cell.

    Plain Voronoi gives every point to its nearest cell, so a gap would be collected as if it
    were tissue. The radius cap is the only thing preventing that, and this is the test that
    says so: two clusters far apart must not be joined across the space between them.
    """
    left = [(i * SPACING, j * SPACING) for j in range(4) for i in range(4)]
    right = [(400 + i * SPACING, j * SPACING) for j in range(4) for i in range(4)]
    cells = _cells(left + right, ["Tumor"] * 32)

    patches, report = regions.project(cells, regions.RegionParams(radius_factor=1.0))
    gap = shapely.box(200, 0, 300, 30)
    intruding = [geometry for geometry in patches.geometry if geometry.intersects(gap)]

    assert not intruding, (
        f"{len(intruding)} region(s) reach into the empty space between two clusters "
        f"{400 - 3 * SPACING:.0f} px apart, with the cap at {report.max_radius_px:.0f} px. That "
        "space would be cut and collected as if it were tissue."
    )


def test_skipping_the_cap_and_clip_changes_no_tissue(grid):
    """The projection skips both intersections for cells neither can touch, for speed.

    Measured on 60 000 cells only 1.6% need the cap and 1.6% reach the hull, so intersecting
    every cell cost about a second to change almost nothing. That is only safe if the tissue is
    the same, so this compares against doing the work unconditionally rather than trusting the
    reasoning — a wrong skip would leave a region reaching out onto blank slide.

    Compared by area and by symmetric difference rather than by `equals`: a cell that was
    intersected comes back rebuilt by GEOS, which may drop a collinear vertex that a skipped
    cell keeps. That is at most one redundant point in a cut path and no difference in tissue,
    and asserting it away would be asserting the wrong thing (`decisions.md` 074).
    """
    centroids = grid.geometry.centroid
    xy = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]
    hull = regions.tissue_hull(grid)
    params = regions.RegionParams(radius_factor=1.0)
    radius, _ = regions.radius_from_spacing(xy, params)

    tessellation = numpy.asarray(
        shapely.voronoi_polygons(
            shapely.MultiPoint(xy), ordered=True, extend_to=hull.envelope
        ).geoms
    )
    skipped = regions._cap_and_clip(tessellation, xy, radius, hull)
    caps = shapely.buffer(shapely.points(xy), radius, quad_segs=regions.CAP_QUAD_SEGS)
    unskipped = shapely.intersection(shapely.intersection(tessellation, caps), hull)

    for index, (fast, slow) in enumerate(zip(skipped, unskipped, strict=True)):
        assert fast.area == pytest.approx(slow.area, abs=1e-9), (
            f"Region {index} covers {fast.area:.6f} px² on the fast path and {slow.area:.6f} "
            "doing every intersection. Whatever was skipped needed doing."
        )
        assert fast.symmetric_difference(slow).area == pytest.approx(0.0, abs=1e-9), (
            f"Region {index} covers different tissue on the two paths, so the skip is placing "
            "cuts somewhere the full computation would not."
        )
