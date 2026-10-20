"""Vignette 4 (several slides): three slides -> choose tissue on each -> one well per class.

In the app the same class from different slides goes into the same well, so each well here
holds one piece from every slide, tagged with the slide it came from.

Run from the repo root:  uv run python <scratchpad>/vignette4.py <scratchpad>
"""

import sys
from pathlib import Path

import geopandas
import numpy
from matplotlib.patches import FancyBboxPatch, Rectangle
from shapely import affinity
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).parent))
import common

from qupath_to_lmd import plot, regions

OUT = Path(sys.argv[1])
CLASSES = ["Immune cells", "Tumor"]
# three sections of the core, each turned differently so they read as three different slides
SECTIONS = [((900, 1500), 0), ((1500, 2500), 140), ((700, 2600), 250)]
CORE_RADIUS = 420
SLIDE_W, SLIDE_H, GAP = 250, 560, 28
LABEL_H = 120

cells = geopandas.read_parquet(OUT / "cells.parquet")
colors = plot.class_colors(CLASSES)
fills = plot.class_fill_colors(CLASSES)
patches, _ = regions.project(cells, regions.RegionParams(), include=CLASSES)
# at slide scale cell-level regions are confetti; close and open each class into a few blobs,
# the way a pathologist would outline them
blobs = []
for name in CLASSES:
    merged = patches[patches.classification_name == name].union_all().buffer(18).buffer(-30).buffer(12)
    blobs.append(geopandas.GeoDataFrame({"classification_name": [name]}, geometry=[merged]))
patches = geopandas.pd.concat(blobs).explode(index_parts=False).reset_index(drop=True)
patches = patches[patches.area > 1500]


def section(centre, angle, k):
    """One round section of tissue, moved onto slide k, with its regions by class."""
    disc = Point(centre).buffer(CORE_RADIUS, 64)
    piece = patches.assign(geometry=patches.intersection(disc))
    piece = piece[piece.area > 2000].copy()
    scale = (SLIDE_W - 22) / (2 * CORE_RADIUS)
    target = (k * (SLIDE_W + GAP) + SLIDE_W / 2, LABEL_H + (SLIDE_H - LABEL_H) / 2)
    moved = []
    for geom in piece.geometry:
        geom = affinity.rotate(geom, angle, origin=centre)
        geom = affinity.scale(geom, scale, scale, origin=centre)
        geom = affinity.translate(geom, target[0] - centre[0], target[1] - centre[1])
        # at slide scale the cell-level ragged edge is noise; a gentle open-close keeps the shape
        moved.append(geom.simplify(0.6))
    return piece.assign(geometry=moved).explode(index_parts=False).reset_index(drop=True)


slides = [section(centre, angle, k) for k, (centre, angle) in enumerate(SECTIONS)]

# on every slide, one chunk of each class, the way a user would outline a bit of it: a lumpy
# loop around a point a little way in from the class edge, clipped to the class, so it keeps
# some of the tissue's own outline and stays clear of the section's rim
CHUNK = 24
rng = numpy.random.default_rng(4)


def outline(centre):
    """A lumpy closed loop around `centre`, as drawn by hand."""
    t = numpy.linspace(0, 2 * numpy.pi, 96, endpoint=False)
    a, b, p, q = rng.uniform(0.1, 0.22), rng.uniform(0.06, 0.14), *rng.uniform(0, 2 * numpy.pi, 2)
    r = CHUNK * (1 + a * numpy.sin(2 * t + p) + b * numpy.sin(3 * t + q))
    return Polygon(numpy.column_stack([centre.x + r * numpy.cos(t), centre.y + r * numpy.sin(t)]))


chosen = []
for piece in slides:
    rim = piece.union_all().convex_hull
    x0, y0, x1, y1 = rim.bounds
    grid = [Point(x, y) for x in numpy.arange(x0, x1, 3) for y in numpy.arange(y0, y1, 3)]
    chunks = []
    for name in CLASSES:
        of_class = piece[piece.classification_name == name].union_all()
        inside = [g for g in grid if of_class.contains(g) and rim.exterior.distance(g) > CHUNK + 4]
        best = min(inside, key=lambda g: abs(of_class.boundary.distance(g) - 0.7 * CHUNK))
        chunks.append((name, of_class.intersection(outline(best))))
    chosen.append(geopandas.GeoDataFrame({"classification_name": [n for n, _ in chunks]},
                                         geometry=[g for _, g in chunks]))


def glass(ax, highlight):
    """The three slides, by class, or with only the chosen chunks in colour."""
    width = 3 * SLIDE_W + 2 * GAP
    common.card(ax, 0, 0, width, SLIDE_H)
    for k, piece in enumerate(slides):
        x = k * (SLIDE_W + GAP)
        ax.add_patch(FancyBboxPatch((x, 0), SLIDE_W, SLIDE_H, boxstyle="round,pad=0,rounding_size=10",
                                    fc="#f4f8fb", ec="#b8c4cf", lw=1.2, zorder=1))
        # the frosted label end of a glass slide
        ax.add_patch(Rectangle((x + 10, 10), SLIDE_W - 20, LABEL_H - 30, fc="#e3e8ee", ec="none", zorder=1))
        ax.text(x + SLIDE_W / 2, (LABEL_H - 10) / 2, str(k + 1), ha="center", va="center", fontsize=11,
                color=common.FAINT, zorder=2)
        tissue = piece.union_all().buffer(3)
        common.draw(ax, [tissue], common.TISSUE, common.TISSUE_EDGE, lw=0.8, z=2)
        for name in CLASSES:
            of_class = piece.classification_name == name
            if highlight:
                common.draw(ax, piece.geometry[of_class], plot.MUTED, plot.MUTED_EDGE, lw=0.3, z=3)
                mine = chosen[k]
                common.draw(ax, mine.geometry[mine.classification_name == name], colors[name], "black",
                            lw=0.7, z=4)
            else:
                common.draw(ax, piece.geometry[of_class], colors[name], "white", lw=0.3, z=3)
        for cx, cy in [(x + 24, LABEL_H + 10), (x + SLIDE_W - 24, LABEL_H + 10), (x + 24, SLIDE_H - 24)]:
            common.cross(ax, cx, cy, arm=8)


fig, (one, two, three) = common.figure()
glass(one, highlight=False)
glass(two, highlight=True)

common.plate(three, {})
# three fixed spots per well rather than a heap, so the pieces from each slide stay apart and
# carry their slide number
SPOTS = [(-22, -20), (26, -20), (2, 26)]
for row, name in enumerate(["Tumor", "Immune cells"], start=1):
    centre = (common.PITCH + common.PITCH / 2, row * common.PITCH + common.PITCH / 2)
    for k, (piece, (dx, dy)) in enumerate(zip(chosen, SPOTS, strict=False)):
        geom = piece.geometry[piece.classification_name == name].iloc[0]
        x0, y0, x1, y1 = geom.bounds
        scale = 40 / max(x1 - x0, y1 - y0)
        c = geom.centroid
        geom = affinity.scale(geom, scale, scale, origin=c)
        geom = affinity.translate(geom, centre[0] + dx - c.x, centre[1] + dy - c.y)
        common.draw(three, [geom], colors[name], "black", lw=0.6, z=3)
        x0, y0, _, _ = geom.bounds
        three.text(x0 - 4, y0 + 2, str(k + 1), ha="center", va="center", fontsize=6, color=common.INK, zorder=5,
                   bbox={"boxstyle": "circle,pad=0.15", "fc": "white", "ec": common.FAINT, "lw": 0.5})
common.save(fig, OUT, "vignette4")
print([round(g.area) for c in chosen for g in c.geometry])
