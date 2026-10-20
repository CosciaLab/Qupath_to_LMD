"""Vignette 5 (single-cell DVP): real cells -> one cell picked per replicate -> one cell per well.

The same start as vignette 2, but each replicate is a single cell, so the plate fills up:
two rows of tumour cells, two rows of immune cells.

Run from the repo root:  uv run python vignettes/vignette5.py vignettes
"""

import sys
from pathlib import Path

import geopandas
from shapely import affinity
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).parent))
import common

from qupath_to_lmd import plot
from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.selection import SelectionParams, select

OUT = Path(sys.argv[1])
CROP = (350, 1950, 850, 2450)  # the window vignette 2 uses, in px
CLASSES = ["Immune cells", "Tumor"]
ROWS, COLS = 4, 4
PER_CLASS = 2 * COLS  # two plate rows of each class

cells = geopandas.read_parquet(OUT / "cells.parquet")
window = box(*CROP)
cells = cells[cells.within(window)].reset_index(drop=True)
colors = plot.class_colors(CLASSES)

result = select(
    cells,
    [ClassBudget(name, PER_CLASS, 1) for name in CLASSES],
    BudgetMode.CELLS,
    SelectionParams(seed=3),
)
chosen = result.replicate_of.notna()
tissue = cells.buffer(14).union_all().buffer(-6).simplify(2).intersection(window.buffer(-4))
x0, y0, x1, y1 = CROP
corners = [(x0 + 22, y0 + 22), (x1 - 22, y0 + 22), (x0 + 22, y1 - 22)]


def slide(ax, highlight):
    """The cells by class, or with only the picked ones in colour."""
    common.card(ax, *CROP)
    common.draw(ax, [tissue], common.TISSUE, common.TISSUE_EDGE, lw=1, z=1)
    for name in CLASSES:
        of_class = cells.classification_name == name
        if highlight:
            common.draw(ax, cells.geometry[of_class & ~chosen], plot.MUTED, plot.MUTED_EDGE, z=2)
            common.draw(ax, cells.geometry[of_class & chosen], colors[name], "black", lw=0.6, z=3)
        else:
            common.draw(ax, cells.geometry[of_class], colors[name], "white", lw=0.3, z=2)
    common.draw(ax, cells.geometry[~cells.classification_name.isin(CLASSES)], plot.MUTED, plot.MUTED_EDGE, z=2)
    for cx, cy in corners:
        common.cross(ax, cx, cy)


fig, (one, two, three) = common.figure()
slide(one, highlight=False)
slide(two, highlight=True)

common.plate(three, {}, rows=ROWS, cols=COLS)
# a lone cell would be a speck at plate scale; wells are not to scale, so each is enlarged alike
SCALE = 1.4
for first_row, name in [(0, "Tumor"), (2, "Immune cells")]:
    of_class = cells.classification_name == name
    for replicate in range(1, PER_CLASS + 1):
        cell = cells.geometry[of_class & (result.replicate_of == replicate)].iloc[0]
        i, j = first_row + (replicate - 1) // COLS, (replicate - 1) % COLS
        c = cell.centroid
        cell = affinity.scale(cell, SCALE, SCALE, origin=c)
        cell = affinity.translate(cell, j * common.PITCH + common.PITCH / 2 - c.x, i * common.PITCH + common.PITCH / 2 - c.y)
        common.draw(three, [cell], colors[name], "black", lw=0.6, z=3)
common.save(fig, OUT, "vignette5")
print(result.achieved[["classification_name", "replicate", "shapes"]].to_string(index=False), len(cells))
