"""Prototype of vignette 2 (selected shapes): real cells -> spread selection -> wells.

Run from the repo root:  uv run python <this file> <scratchpad dir>
"""

import sys
from pathlib import Path

import geopandas
import numpy
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from shapely import affinity
from shapely.geometry import box

from qupath_to_lmd import plot
from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.selection import SelectionParams, select

OUT = Path(sys.argv[1])
CROP = (350, 1950, 850, 2450)  # a mixed tumour/immune window of the Exemplar001 core, in px
CLASSES = ["Immune cells", "Tumor"]
REPLICATES, PER_REPLICATE = 2, 18
TISSUE, TISSUE_EDGE = "#f7dfe8", "#e9bfd0"
CARD_EDGE = "#d0d4da"

cells = geopandas.read_parquet(OUT / "cells.parquet")
window = box(*CROP)
cells = cells[cells.within(window)].reset_index(drop=True)
colors = plot.class_colors(CLASSES)

result = select(
    cells,
    [ClassBudget(name, REPLICATES, PER_REPLICATE) for name in CLASSES],
    BudgetMode.CELLS,
    SelectionParams(seed=3),
)
chosen = result.replicate_of.notna()

tissue = cells.buffer(14).union_all().buffer(-6).simplify(2).intersection(window.buffer(-4))


def card(ax, x0, y0, x1, y1):
    """A white rounded card behind a panel; also fixes the panel's extent, y pointing down."""
    pad = 18
    ax.add_patch(FancyBboxPatch((x0 - pad, y0 - pad), x1 - x0 + 2 * pad, y1 - y0 + 2 * pad,
                                boxstyle="round,pad=0,rounding_size=22", fc="white", ec=CARD_EDGE, lw=1.2,
                                zorder=0))
    ax.set_xlim(x0 - pad - 2, x1 + pad + 2)
    ax.set_ylim(y1 + pad + 2, y0 - pad - 2)
    ax.set_aspect("equal")
    ax.axis("off")


def draw(ax, geoms, fc, ec, lw=0.4, z=2):
    """Plot shapes in one fill and one edge colour."""
    geopandas.GeoSeries(list(geoms)).plot(ax=ax, fc=fc, ec=ec, lw=lw, zorder=z)


def slide(ax, highlight):
    """The cells by class, or with only the selected ones in colour."""
    x0, y0, x1, y1 = CROP
    card(ax, x0, y0, x1, y1)
    draw(ax, [tissue], TISSUE, TISSUE_EDGE, lw=1, z=1)
    for name in CLASSES:
        of_class = cells.classification_name == name
        if highlight:
            draw(ax, cells.geometry[of_class & ~chosen], plot.MUTED, plot.MUTED_EDGE, z=2)
            draw(ax, cells.geometry[of_class & chosen], colors[name], "black", lw=0.6, z=3)
        else:
            draw(ax, cells.geometry[of_class], colors[name], "white", lw=0.3, z=2)
    other = ~cells.classification_name.isin(CLASSES)
    draw(ax, cells.geometry[other], plot.MUTED, plot.MUTED_EDGE, z=2)
    # three low-key calibration crosses, as the user would place them in QuPath
    for cx, cy in [(x0 + 22, y0 + 22), (x1 - 22, y0 + 22), (x0 + 22, y1 - 22)]:
        for color, width in (("white", 4.5), ("#6b7079", 1.6)):
            ax.plot([cx - 11, cx + 11], [cy, cy], color=color, lw=width, zorder=5, solid_capstyle="round")
            ax.plot([cx, cx], [cy - 11, cy + 11], color=color, lw=width, zorder=5, solid_capstyle="round")


def pile(shapes, centre, radius, rng):
    """Drop shapes into a well as a loose, non-overlapping heap around its centre."""
    placed, out = [], []
    for geom in shapes:
        # wells are not to scale anyway; smaller cells keep the heap inside its well
        geom = affinity.scale(geom, 0.72, 0.72, origin="centroid")
        c = geom.centroid
        for attempt in range(800):
            grown = radius * (1 + attempt / 400)
            r, t = grown * numpy.sqrt(rng.random()), rng.random() * 2 * numpy.pi
            moved = affinity.rotate(geom, rng.random() * 360, origin=c)
            moved = affinity.translate(moved, centre[0] + r * numpy.cos(t) - c.x, centre[1] + r * numpy.sin(t) - c.y)
            if all(moved.distance(p) > 1.5 for p in placed):
                break
        placed.append(moved)
        out.append(moved)
    return out


def plate(ax):
    """A corner of the plate with each replicate in its well."""
    pitch, side, rows, cols = 125, 108, "ABCD", 4
    w, h = cols * pitch, len(rows) * pitch
    card(ax, -40, -40, w, h)
    ax.add_patch(FancyBboxPatch((-30, -30), w + 20, h + 20, boxstyle="round,pad=0,rounding_size=14",
                                fc="#eef0f3", ec="#c3c8cf", lw=1, zorder=1))
    for i, row in enumerate(rows):
        ax.text(-18, i * pitch + pitch / 2, row, ha="center", va="center", fontsize=7, color="#9aa0a8")
    for j in range(cols):
        ax.text(j * pitch + pitch / 2, -18, str(j + 1), ha="center", va="center", fontsize=7, color="#9aa0a8")
    wells = {("Tumor", 1): (1, 1), ("Tumor", 2): (1, 2), ("Immune cells", 1): (2, 1), ("Immune cells", 2): (2, 2)}
    rng = numpy.random.default_rng(1)
    for i in range(len(rows)):
        for j in range(cols):
            x, y = j * pitch + (pitch - side) / 2, i * pitch + (pitch - side) / 2
            ax.add_patch(Rectangle((x, y), side, side, fc="white", ec="#b8bec6", lw=1, zorder=2))
    for (name, replicate), (i, j) in wells.items():
        members = cells.geometry[(cells.classification_name == name) & (result.replicate_of == replicate)]
        centre = (j * pitch + pitch / 2, i * pitch + pitch / 2)
        draw(ax, pile(members, centre, side * 0.22, rng), colors[name], "black", lw=0.6, z=3)


figure = Figure(figsize=(12, 4), dpi=150, facecolor="white")
panels = [figure.add_axes([0.02 + k * 0.335, 0.04, 0.29, 0.92]) for k in range(3)]
slide(panels[0], highlight=False)
slide(panels[1], highlight=True)
plate(panels[2])
for x in (0.315, 0.65):
    figure.patches.append(FancyArrowPatch((x, 0.5), (x + 0.035, 0.5), transform=figure.transFigure,
                                          arrowstyle="-|>,head_length=8,head_width=5", lw=2.2, color="#8a8f98"))
for suffix in ("png", "svg"):
    figure.savefig(OUT / f"vignette2.{suffix}")
print(result.achieved[["classification_name", "replicate", "shapes"]].to_string(index=False), len(cells))
