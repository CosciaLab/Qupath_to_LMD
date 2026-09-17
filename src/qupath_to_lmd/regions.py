"""Projecting cells into regions: a Voronoi tessellation, then merging by class.

A single cell is far too small to collect as mini-bulk, and a whole class outlined by hand is
one enormous irregular cut. This module turns classified cells into **regions**: contiguous
areas of tissue that belong to one class, built from the territory around each cell.

Two things about the geometry are load-bearing and neither is obvious.

**The tessellation is computed over every classified cell, not only the chosen classes.** A
Voronoi region is the territory closest to its cell. If a class were dropped before
tessellating, its neighbours' regions would expand into the space it occupied, and the laser
would then cut one class's tissue under another class's label. So all cells take part, and
regions of unwanted classes are discarded afterwards.

**Every region is capped at a disc around its own cell.** A Voronoi cell on the rim of the
tissue is unbounded, so left alone it reaches out across blank slide and a cut placed there
would take glass rather than tissue. Capping at radius `R` bounds every region locally, and
clipping to the convex hull of the cell outlines bounds the rim. Voronoi cells, discs and the hull are
all convex, so a region is always a single polygon — MultiPolygons only appear once regions of
the same class are merged, and those are exploded into one row per contiguous patch.
"""

from dataclasses import dataclass, field

import geopandas
import numpy
import pandas
import shapely
from loguru import logger
from scipy.spatial import cKDTree

from qupath_to_lmd.model import CLASS_NAME

# How far a region may reach from its cell, as a multiple of the median distance between
# neighbouring cells. Three is generous in dense tissue — the Voronoi boundary is reached long
# before the cap — and only bites where cells are sparse, which is exactly where an uncapped
# region would wander onto blank slide.
DEFAULT_RADIUS_FACTOR = 3.0

# Circle resolution for the radius cap. The cap is a soft, data-derived bound rather than a
# measured outline, so a coarse disc is fine here; packed circles use a finer one.
CAP_QUAD_SEGS = 16

# Below this a convex hull has no area, so there is no region to cut.
MINIMUM_CELLS = 3

# How many cells contributed to a patch. Not a canonical column — it is reported, not exported.
N_CELLS = "n_cells"


class RegionError(Exception):
    """The cells cannot be projected into regions at all."""


@dataclass(frozen=True)
class RegionParams:
    """Everything that determines the regions, recorded so they can be reproduced."""

    radius_factor: float = DEFAULT_RADIUS_FACTOR
    max_radius_px: float | None = None


@dataclass
class RegionReport:
    """What the projection did. Everything here is shown to the user.

    `hole_area_px2` exists because the export path cuts a shape's outer outline only
    (`geojson.extract_coordinates`), so a patch enclosing an island of another class would
    take that island too. The user has to be told how much tissue that is.
    """

    n_cells: int = 0
    n_cells_kept: int = 0
    n_duplicate_centroids: int = 0
    n_empty_regions: int = 0
    median_nn_distance_px: float = 0.0
    max_radius_px: float = 0.0
    n_patches: int = 0
    n_patches_with_holes: int = 0
    hole_area_px2: float = 0.0
    n_slivers_filled: int = 0
    smallest_region_px2: float = 0.0
    per_class: pandas.DataFrame = field(default_factory=pandas.DataFrame)

    def summary(self, pixel_size_um: float | None = None) -> pandas.DataFrame:
        """Per class: cells, regions and area, for showing instead of a paragraph.

        Left unrounded: the UI layer decides how to display a number, and these run from tens to
        millions of µm² where no decimal is meaningful.
        """
        if self.per_class.empty:
            return self.per_class
        table = self.per_class.copy()
        area = table.pop("area_px2")
        table["Total area (µm²)" if pixel_size_um else "Total area (px²)"] = (
            area * pixel_size_um**2 if pixel_size_um else area
        )
        return table.rename(columns={"cells": "Cells", "patches": "Regions"})


def tissue_hull(gdf: geopandas.GeoDataFrame):
    """The convex hull of the cell outlines, which is as far as any region may reach.

    Of the cell **bodies**, not their centroids: a cell on the rim of the tissue would
    otherwise have its own outline cut in half by the hull, and that cell's tissue is exactly
    as collectable as any other's.

    Hulled from a geometry collection rather than from a union — the answer is identical and
    `union_all().convex_hull` costs 9.1 s at 100 000 cells against 0.09 s for this.
    """
    return shapely.convex_hull(shapely.geometrycollections(gdf.geometry.to_numpy()))


def median_cell_spacing(gdf: geopandas.GeoDataFrame) -> float:
    """How far apart neighbouring cells are, in pixels: the median nearest-neighbour distance.

    Exposed on its own because the interface needs it before any tessellation runs, to offer a
    sensible default reach in a unit the user recognises.
    """
    centroids = gdf.geometry.centroid
    xy = numpy.unique(numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()], axis=0)
    if len(xy) < 2:
        return 0.0
    distances, _ = cKDTree(xy).query(xy, k=2)
    return float(numpy.median(distances[:, 1]))


def radius_from_spacing(xy: numpy.ndarray, params: RegionParams) -> tuple[float, float]:
    """The radius cap, and the median distance between neighbouring cells it came from.

    An explicit `max_radius_px` wins. Otherwise the cap scales with how far apart the cells
    actually are, so the same factor behaves sensibly on a dense tumour and a sparse stroma
    without the user having to know either scale.
    """
    distances, _ = cKDTree(xy).query(xy, k=2)
    median_nn = float(numpy.median(distances[:, 1]))
    if params.max_radius_px:
        return float(params.max_radius_px), median_nn
    return params.radius_factor * median_nn, median_nn


def voronoi_regions(gdf: geopandas.GeoDataFrame, params: RegionParams) -> geopandas.GeoDataFrame:
    """The territory around each cell, capped at a disc and clipped to the cells' convex hull.

    Args:
        gdf: QC'd shapes carrying `classification_name`. **All** of them take part — see the
            module docstring for why filtering before this point would mis-assign tissue.
        params: the radius cap.

    Returns:
        One row per cell whose region is non-empty, carrying `classification_name` and the
        source index, in the input's order.

    Raises:
        RegionError: fewer than `MINIMUM_CELLS` distinct cell positions, so there is no area
            to tessellate.
    """
    centroids = gdf.geometry.centroid
    xy = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]

    # Two cells at the same position cannot both own the territory around it, and shapely
    # returns an empty polygon for the duplicate. Keeping the first occurrence makes the choice
    # explicit and countable rather than leaving an empty region to explain later.
    _, first = numpy.unique(xy, axis=0, return_index=True)
    keep = numpy.sort(first)
    if len(keep) < MINIMUM_CELLS:
        raise RegionError(
            f"Only {len(keep)} cell position(s) in this file, and at least {MINIMUM_CELLS} are "
            "needed to divide the tissue into regions."
        )

    xy = xy[keep]
    source = gdf.iloc[keep]
    points = shapely.MultiPoint(xy)
    hull = tissue_hull(source)

    radius, median_nn = radius_from_spacing(xy, params)
    logger.info(
        f"Tessellating {len(xy)} cells; median neighbour distance {median_nn:.1f}px, "
        f"region radius capped at {radius:.1f}px"
    )

    # `ordered=True` (shapely >= 2.1) guarantees the i-th polygon belongs to the i-th point, so
    # class labels map by position. Without it the output order is GEOS's business and every
    # region could carry the wrong class — which would cut the wrong tissue silently.
    tessellation = shapely.voronoi_polygons(points, ordered=True, extend_to=hull.envelope)
    caps = shapely.buffer(shapely.points(xy), radius, quad_segs=CAP_QUAD_SEGS)
    geometries = shapely.intersection(
        shapely.intersection(numpy.asarray(tessellation.geoms), caps), hull
    )

    regions = geopandas.GeoDataFrame(
        {CLASS_NAME: source[CLASS_NAME].to_numpy()},
        geometry=list(geometries),
        index=source.index,
        crs=None,
    )
    return regions[~regions.geometry.is_empty]


def close_slivers(geometry, minimum_area: float) -> tuple[object, int]:
    """Fill the pinhole gaps merging leaves behind, and report how many were filled.

    The radius cap is a 64-sided polygon while Voronoi edges are exact, so where three capped
    regions meet they leave a gap of a pixel or two that belongs to no region. Merging turns
    those into interior rings, and they are not enclosed tissue — they are an artefact of
    approximating a disc.

    A genuine hole is another cell's territory, so it cannot be smaller than the smallest
    region in the tessellation. That makes the threshold a property of the data rather than a
    tuned constant. Measured across the demo files, slivers came out at 0-8 px² against
    smallest regions of 48-702 px², so the two groups do not overlap.

    Filling them matters twice over: the tissue in a gap does belong to the class, and a
    "region surrounds another class" warning raised over one pixel would teach the user to
    ignore the warning when it is real.
    """
    if not geometry.interiors:
        return geometry, 0

    kept = [ring for ring in geometry.interiors if shapely.Polygon(ring).area >= minimum_area]
    filled = len(geometry.interiors) - len(kept)
    if not filled:
        return geometry, 0
    return shapely.Polygon(geometry.exterior, kept), filled


def merge_by_class(
    regions: geopandas.GeoDataFrame, minimum_hole_area: float = 0.0
) -> tuple[geopandas.GeoDataFrame, int]:
    """Merge touching regions of the same class into one patch per contiguous area.

    Returns one row per patch, not one per class: a class usually occupies several separate
    areas of a slide, and each is its own thing to cut, count and pack into.

    Patches are sorted by class and then by position. GEOS decides what order a union comes
    out in, and that order determines which patch gets which well and, later, the order random
    circle placement consumes its numbers in — so it is pinned here rather than inherited.

    Args:
        regions: one row per cell, from `voronoi_regions`.
        minimum_hole_area: interior rings smaller than this are filled as merge artefacts.
            Pass the smallest region area of the **whole** tessellation, not of the classes
            being kept — a hole may be the region of a class that was left out, and that class
            can hold smaller regions than any of the kept ones.

    Returns:
        The patches, and how many sliver holes were filled.
    """
    merged = (
        regions[[CLASS_NAME, "geometry"]]
        .dissolve(by=CLASS_NAME)
        .explode(index_parts=False)
        .reset_index()
    )

    n_filled = 0
    if minimum_hole_area > 0:
        closed = [close_slivers(geometry, minimum_hole_area) for geometry in merged.geometry]
        merged = merged.set_geometry([geometry for geometry, _ in closed])
        n_filled = sum(filled for _, filled in closed)

    bounds = merged.geometry.bounds.round(6)
    order = numpy.lexsort((bounds["miny"], bounds["minx"], merged[CLASS_NAME]))
    merged = merged.iloc[order].reset_index(drop=True)

    logger.info(
        f"Merged {len(regions)} regions into {len(merged)} patches"
        + (f", filling {n_filled} sliver hole(s)" if n_filled else "")
    )
    return merged, n_filled


def cells_per_patch(patches: geopandas.GeoDataFrame, regions: geopandas.GeoDataFrame) -> numpy.ndarray:
    """How many cells contributed to each patch.

    A patch of two cells and a patch of two hundred are very different things to collect, and
    the count is the cheapest way to tell them apart on screen.
    """
    if patches.empty or regions.empty:
        return numpy.zeros(len(patches), dtype=int)

    # A representative point is guaranteed to be inside its own region, and a region is a subset
    # of the patch that absorbed it, so each point lands in exactly one patch.
    inside = regions.geometry.representative_point().to_numpy()
    tree = shapely.STRtree(patches.geometry.to_numpy())
    # `query` returns input indices first and tree indices second; it is the tree indices —
    # the patches — that are being counted here.
    _, patch_positions = tree.query(inside, predicate="intersects")
    return numpy.bincount(patch_positions, minlength=len(patches))


def project(
    gdf: geopandas.GeoDataFrame,
    params: RegionParams,
    include: list[str] | None = None,
) -> tuple[geopandas.GeoDataFrame, RegionReport]:
    """Turn classified cells into one patch per contiguous same-class area.

    Args:
        gdf: every QC'd shape in the file. All of them are tessellated.
        params: the radius cap.
        include: classes to keep. Regions of other classes are discarded **after** the
            tessellation, so they still hold their own territory. `None` keeps everything.

    Returns:
        The patches, with `classification_name`, `n_cells` and `geometry`, and a report.

    Raises:
        RegionError: the tessellation is impossible, or nothing survives the class filter.
    """
    regions = voronoi_regions(gdf, params)

    wanted = regions if include is None else regions[regions[CLASS_NAME].isin(include)]
    if wanted.empty:
        raise RegionError(
            "None of the classes you chose have a region on this slide, so there is nothing "
            "to collect."
        )

    # The smallest region of the whole tessellation, so a hole that is an excluded class's
    # region is never mistaken for a merge artefact.
    smallest_region = float(regions.geometry.area.min())
    patches, n_filled = merge_by_class(wanted, minimum_hole_area=smallest_region)
    patches[N_CELLS] = cells_per_patch(patches, wanted)

    interiors = patches.geometry.map(lambda geometry: len(geometry.interiors))
    hole_area = float(
        sum(
            shapely.Polygon(ring).area
            for geometry in patches.geometry
            for ring in geometry.interiors
        )
    )

    xy_unique = len(numpy.unique(numpy.c_[gdf.geometry.centroid.x, gdf.geometry.centroid.y], axis=0))
    radius, median_nn = radius_from_spacing(
        numpy.c_[gdf.geometry.centroid.x.to_numpy(), gdf.geometry.centroid.y.to_numpy()], params
    )

    per_class = pandas.DataFrame(
        {
            "cells": patches.groupby(CLASS_NAME)[N_CELLS].sum(),
            "patches": patches.groupby(CLASS_NAME).size(),
            "area_px2": patches.groupby(CLASS_NAME).geometry.apply(lambda group: group.area.sum()),
        }
    )

    report = RegionReport(
        n_cells=len(gdf),
        n_cells_kept=len(wanted),
        n_duplicate_centroids=len(gdf) - xy_unique,
        n_empty_regions=xy_unique - len(regions),
        median_nn_distance_px=median_nn,
        max_radius_px=radius,
        n_patches=len(patches),
        n_patches_with_holes=int((interiors > 0).sum()),
        hole_area_px2=hole_area,
        n_slivers_filled=n_filled,
        smallest_region_px2=smallest_region,
        per_class=per_class,
    )
    logger.success(
        f"Projected {report.n_cells_kept} cells into {report.n_patches} regions across "
        f"{len(per_class)} classes"
    )
    return patches, report


def deal_patches(patches: geopandas.GeoDataFrame, replicates: dict[str, int]) -> pandas.Series:
    """Spread each class's patches across its replicates, balancing area.

    Largest patch first into whichever replicate currently holds the least area. Replicates of
    one class should be comparable in amount, and dealing in order would instead give replicate
    1 every large patch. No randomness: the same patches always deal the same way, which is
    what makes a collection reproducible in a later session.

    Returns:
        The replicate number of every patch, NA for a patch whose class has no replicates.
    """
    replicate_of = pandas.Series(pandas.NA, index=patches.index, dtype="Int64")

    for class_name, group in patches.groupby(CLASS_NAME):
        wanted = int(replicates.get(str(class_name), 0))
        if wanted < 1:
            continue
        filled = dict.fromkeys(range(1, wanted + 1), 0.0)
        for position in group.geometry.area.sort_values(ascending=False).index:
            lightest = min(filled, key=lambda replicate: (filled[replicate], replicate))
            replicate_of.at[position] = lightest
            filled[lightest] += float(patches.geometry.at[position].area)

    dealt = int(replicate_of.notna().sum())
    logger.info(f"Dealt {dealt} of {len(patches)} patches across replicates")
    return replicate_of
