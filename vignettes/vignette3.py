"""Vignette 3 (regions and circles): neighbourhoods of cells -> regions packed with circles -> wells.

Run from the repo root:  uv run python <scratchpad>/vignette3.py <scratchpad>
"""

import sys
from pathlib import Path

import geopandas
from matplotlib.colors import to_rgb
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).parent))
import common

from qupath_to_lmd import packing, plot, regions

OUT = Path(sys.argv[1])
PIXEL_SIZE_UM = 0.6535
CROP = (950, 850, 1550, 1450)  # where the core's immune and tumour neighbourhoods meet, in px
CLASSES = ["Immune cells", "Tumor"]
REPLICATES, PER_REPLICATE_UM2 = 2, 4500

cells = geopandas.read_parquet(OUT / "cells.parquet")
window = box(*CROP)
cells = cells[cells.intersects(window.buffer(60))].reset_index(drop=True)
colors = plot.class_colors(CLASSES)
# lighter than the app's region fill, so a circle stands out on a region of its own class
fills = {name: tuple(0.4 * c + 0.6 for c in to_rgb(colors[name])) for name in CLASSES}

patches, _ = regions.project(cells, regions.RegionParams(), include=CLASSES)
patches = patches.assign(geometry=patches.intersection(window.buffer(-4)))
patches = patches[~patches.is_empty]
result = packing.pack(
    patches,
    [packing.ClassPacking(name, REPLICATES, PER_REPLICATE_UM2, 250, 700, 4) for name in CLASSES],
    packing.PackingParams(seed=2),
    PIXEL_SIZE_UM,
)
circles = result.circles
inside = cells[cells.within(window.buffer(-4))]

fig, (one, two, three) = common.figure()
x0, y0, x1, y1 = CROP
corners = [(x0 + 26, y0 + 26), (x1 - 26, y0 + 26), (x0 + 26, y1 - 26)]

common.card(one, *CROP)
tissue = inside.buffer(14).union_all().buffer(-6).intersection(window.buffer(-4))
common.draw(one, [tissue], common.TISSUE, common.TISSUE_EDGE, lw=1, z=1)
for name in CLASSES:
    common.draw(one, inside.geometry[inside.classification_name == name], colors[name], "white", lw=0.3)
common.draw(one, inside.geometry[~inside.classification_name.isin(CLASSES)], plot.MUTED, plot.MUTED_EDGE)

common.card(two, *CROP)
common.draw(two, [tissue], common.TISSUE, common.TISSUE_EDGE, lw=1, z=1)
for name in CLASSES:
    common.draw(two, patches.geometry[patches.classification_name == name], fills[name], colors[name], lw=1.2, z=2)
    common.draw(two, circles.geometry[circles.classification_name == name], colors[name], "black", lw=0.7, z=3)

for ax in (one, two):
    for cx, cy in corners:
        common.cross(ax, cx, cy)

wells = {}
for row, name in enumerate(["Tumor", "Immune cells"], start=1):
    for replicate in range(1, REPLICATES + 1):
        chosen = circles[(circles.classification_name == name) & (circles.replicate == replicate)]
        wells[(row, replicate)] = (chosen.geometry, colors[name])
common.plate(three, wells)
common.save(fig, OUT, "vignette3")
print(result.achieved.to_string(index=False))
