"""Shared frame for the vignettes: cards, arrows, calibration crosses, the plate corner."""

import geopandas
import numpy
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from shapely import affinity

TISSUE, TISSUE_EDGE = "#f7dfe8", "#e9bfd0"
CARD_EDGE = "#d0d4da"
INK = "#6b7079"
FAINT = "#9aa0a8"
PITCH, SIDE = 125, 108


def figure():
    """A 12x4 in canvas with three panels joined by arrows."""
    fig = Figure(figsize=(12, 4), dpi=150, facecolor="white")
    panels = [fig.add_axes([0.02 + k * 0.335, 0.04, 0.29, 0.92]) for k in range(3)]
    for x in (0.315, 0.65):
        fig.patches.append(FancyArrowPatch((x, 0.5), (x + 0.035, 0.5), transform=fig.transFigure,
                                           arrowstyle="-|>,head_length=8,head_width=5", lw=2.2, color="#8a8f98"))
    return fig, panels


def save(fig, out, name):
    """Write the figure as PNG and SVG."""
    for suffix in ("png", "svg"):
        fig.savefig(out / f"{name}.{suffix}")


def card(ax, x0, y0, x1, y1, pad=18):
    """A white rounded card behind a panel; also fixes the panel's extent, y pointing down."""
    side = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = side / 2 + pad
    ax.add_patch(FancyBboxPatch((cx - half, cy - half), 2 * half, 2 * half,
                                boxstyle="round,pad=0,rounding_size=22", fc="white", ec=CARD_EDGE, lw=1.2,
                                zorder=0))
    ax.set_xlim(cx - half - 2, cx + half + 2)
    ax.set_ylim(cy + half + 2, cy - half - 2)
    ax.set_aspect("equal")
    ax.axis("off")


def draw(ax, geoms, fc, ec, lw=0.4, z=2, alpha=None, ls="-"):
    """Plot shapes in one fill and one edge colour."""
    geoms = list(geoms)
    # an RGB tuple would be read by geopandas as one colour per shape
    fc, ec = (to_hex(c) if isinstance(c, tuple) else c for c in (fc, ec))
    if geoms:
        geopandas.GeoSeries(geoms).plot(ax=ax, fc=fc, ec=ec, lw=lw, zorder=z, alpha=alpha, linestyle=ls)


def cross(ax, cx, cy, arm=11):
    """A low-key calibration point, haloed so it reads on top of anything."""
    for color, width in (("white", 4.5), (INK, 1.6)):
        ax.plot([cx - arm, cx + arm], [cy, cy], color=color, lw=width, zorder=6, solid_capstyle="round")
        ax.plot([cx, cx], [cy - arm, cy + arm], color=color, lw=width, zorder=6, solid_capstyle="round")


def pile(shapes, centre, rng, fill=0.42, cap=0.72):
    """Drop shapes into a well as a loose, non-overlapping heap around its centre.

    Wells are not to scale, so each heap is shrunk to fit: small shapes (cells, circles) by a
    fixed `cap`, big ones (annotations, regions) until their total area covers `fill` of a
    disc in the well.
    """
    shapes = list(shapes)
    total = sum(g.area for g in shapes) or 1
    radius = SIDE * 0.4
    scale = min(cap, numpy.sqrt(fill * numpy.pi * radius**2 / total))
    placed = []
    start = SIDE * 0.05 if len(shapes) > 1 else 0
    for geom in sorted(shapes, key=lambda g: -g.area):
        geom = affinity.scale(geom, scale, scale, origin="centroid")
        c = geom.centroid
        for attempt in range(800):
            grown = start + radius * attempt / 500
            r, t = grown * numpy.sqrt(rng.random()), rng.random() * 2 * numpy.pi
            moved = affinity.rotate(geom, rng.random() * 360, origin=c)
            moved = affinity.translate(moved, centre[0] + r * numpy.cos(t) - c.x, centre[1] + r * numpy.sin(t) - c.y)
            if all(moved.distance(p) > 1.5 for p in placed):
                break
        placed.append(moved)
    return placed


def plate(ax, contents, rows=4, cols=4, row_labels=True, seed=1):
    """A corner of a 384-well plate. `contents` maps (row, col) to (shapes, colour)."""
    w, h = cols * PITCH, rows * PITCH
    card(ax, -40, -40, w, h)
    ax.add_patch(FancyBboxPatch((-30, -30), w + 20, h + 20, boxstyle="round,pad=0,rounding_size=14",
                                fc="#eef0f3", ec="#c3c8cf", lw=1, zorder=1))
    if row_labels:
        for i in range(rows):
            ax.text(-18, i * PITCH + PITCH / 2, "ABCDEFGH"[i], ha="center", va="center", fontsize=7, color=FAINT)
        for j in range(cols):
            ax.text(j * PITCH + PITCH / 2, -18, str(j + 1), ha="center", va="center", fontsize=7, color=FAINT)
    for i in range(rows):
        for j in range(cols):
            x, y = j * PITCH + (PITCH - SIDE) / 2, i * PITCH + (PITCH - SIDE) / 2
            ax.add_patch(Rectangle((x, y), SIDE, SIDE, fc="white", ec="#b8bec6", lw=1, zorder=2))
    rng = numpy.random.default_rng(seed)
    for (i, j), (shapes, color) in contents.items():
        centre = (j * PITCH + PITCH / 2, i * PITCH + PITCH / 2)
        draw(ax, pile(shapes, centre, rng), color, "black", lw=0.6, z=3)
