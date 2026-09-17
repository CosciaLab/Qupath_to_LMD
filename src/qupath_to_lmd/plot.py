"""Drawing shapes for the class overview, the selection preview and the export QC image.

One function serves all three (`decisions.md` 017).

Uses `matplotlib.figure.Figure` directly rather than `pyplot`, because pyplot keeps every
figure in a global registry and Streamlit reruns would leak them.
"""

import geopandas
import numpy
import pandas
from loguru import logger
from matplotlib import colormaps
from matplotlib.collections import PathCollection, PolyCollection
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.path import Path
from shapely.geometry.polygon import orient

from qupath_to_lmd.model import CLASS_NAME

# Okabe-Ito, which stays distinguishable for the common forms of colour blindness.
PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#D55E00", "#CC79A7", "#F0E442"]
MUTED = "#dcdcdc"
MUTED_EDGE = "#b4b4b4"

# Above this many shapes, draw one dot per shape instead of its outline. Drawing outlines is
# ~2s at 50k shapes and ~8s at 200k; centroids are 0.14s at 200k.
SHAPE_LIMIT = 20_000

# Two variables share one picture — which class a shape belongs to and which replicate it goes
# into — so they are on two channels that cannot be confused. Hue alone is not enough: a full
# palette for each collides, and at that point an outline can be the same colour as the fill it
# sits on. Measured across every pair, the tightest contrast with two full palettes is 1.00,
# meaning literally the same colour (`decisions.md` 070).
#
# So the channels differ in lightness as well as hue. Fills are the class, tinted toward white;
# outlines are the replicate, shaded toward black. Those two factors were chosen by scanning
# them against the WCAG contrast of every fill-outline pair and the RGB separation of every pair
# of outlines: they give contrast 1.78 everywhere, with outlines 0.198 apart from each other.
CLASS_FILL_TINT = 0.55
REPLICATE_SHADE = 0.25

# tab10 rather than tab20: shading compresses a palette, and tab20's twenty entries end up too
# close together to tell apart once darkened. Ten replicates is already more than a plate makes
# sense for, and the palette cycles beyond that.
REPLICATE_COLORMAP = "tab10"

# A circle is a class-coloured disc with a replicate-coloured ring, and on a whole-core view it
# is only a few pixels across — so the ring has to carry most of the weight.
CIRCLE_EDGE_WIDTH = 1.5
# Regions are the backdrop the cuts are judged against, not the subject. The circles use the
# same fill palette at full opacity, so the two layers separate by weight rather than by needing
# a third set of colours.
REGION_FILL_ALPHA = 0.45


def class_colors(classes: list[str]) -> dict[str, str]:
    """Stable colour per class: sorted, so a class keeps its colour across redraws."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sorted(classes))}


def class_fill_colors(classes: list[str]) -> dict[str, tuple]:
    """The same hue per class, tinted toward white, for use as a fill under a dark outline.

    One source of truth for a class's hue — `class_colors` — rendered two ways, so a class looks
    like itself whichever picture it appears in.
    """
    return {
        name: _tint(color, CLASS_FILL_TINT) for name, color in class_colors(classes).items()
    }


def replicate_colors(replicates: list[int]) -> dict[int, tuple]:
    """A dark colour per replicate number, for the outline of a circle.

    Keyed by the replicate number rather than by position, so replicate 2 keeps its colour when
    a class with fewer replicates is added or removed — keyed by position, a user comparing two
    screenshots would read a change that never happened. Cycles beyond the palette, which is
    already more replicates than a plate makes sense for.
    """
    colormap = colormaps[REPLICATE_COLORMAP]
    return {
        number: _shade(colormap(int(number - 1) % colormap.N), REPLICATE_SHADE)
        for number in sorted(set(replicates))
    }


def _tint(color, amount: float) -> tuple:
    """A colour moved toward white by `amount`."""
    return tuple(value + (1.0 - value) * amount for value in to_rgb(color))


def _shade(color, amount: float) -> tuple:
    """A colour moved toward black by `amount`."""
    return tuple(value * (1.0 - amount) for value in to_rgb(color))


def plot_shapes(
    gdf: geopandas.GeoDataFrame,
    labels: pandas.Series | None = None,
    included: list[str] | None = None,
    calibration_array: numpy.ndarray | None = None,
    title: str | None = None,
    figsize: tuple[float, float] = (10.0, 7.5),
) -> Figure:
    """Draw shapes coloured by a label, with everything else grey.

    One function for the class overview, the selection preview and the export QC image
    (`decisions.md` 017): the caller decides what the label means.

    Args:
        gdf: the shapes.
        labels: label per shape index. Defaults to `classification_name`. Shapes whose label
            is NA are always drawn grey — that is how unselected shapes appear.
        included: labels to colour. Anything else is drawn grey, so a user can see what they
            are leaving out rather than only what they are taking.
        calibration_array: 3x2 array; drawn as a dashed triangle if given.
        title: optional heading.
        figsize: inches.
    """
    figure = Figure(figsize=figsize, layout="constrained")
    axes = figure.add_subplot()

    if labels is None:
        labels = gdf[CLASS_NAME]
    labels = labels.reindex(gdf.index)

    classes = sorted(labels.dropna().unique())
    included = classes if included is None else included
    colors = class_colors(classes)
    as_dots = len(gdf) > SHAPE_LIMIT
    logger.info(f"Plotting {len(gdf)} shapes as {'centroids' if as_dots else 'polygons'}")

    unlabelled = gdf[labels.isna()]
    if not unlabelled.empty:
        _draw(axes, unlabelled, MUTED, as_dots, False)

    # Excluded first, so included labels are drawn over them.
    for class_name in sorted(classes, key=lambda name: name in included):
        subset = gdf[labels == class_name]
        if subset.empty:
            continue
        is_in = class_name in included
        _draw(axes, subset, colors[class_name] if is_in else MUTED, as_dots, is_in)

    if calibration_array is not None and len(calibration_array) == 3:
        triangle = numpy.vstack([calibration_array, calibration_array[:1]])
        axes.plot(triangle[:, 0], triangle[:, 1], "--", color="#444444", linewidth=1, zorder=1)
        axes.scatter(
            calibration_array[:, 0], calibration_array[:, 1],
            marker="+", s=90, color="#444444", zorder=4,
        )

    handles = [
        Line2D([], [], marker="o", linestyle="", markersize=7,
               markerfacecolor=colors[name] if name in included else MUTED,
               markeredgecolor="none",
               label=f"{name} ({int((labels == name).sum())})"
                     + ("" if name in included else " — excluded"))
        for name in classes
    ]
    if not unlabelled.empty:
        handles.append(
            Line2D([], [], marker="o", linestyle="", markersize=7, markerfacecolor=MUTED,
                   markeredgecolor="none", label=f"not selected ({len(unlabelled)})")
        )
    if handles:
        # Placed outside the axes: a legend inside covers tissue, and tissue is the point.
        # "outside ..." locations need constrained layout, which the figure above uses.
        figure.legend(handles=handles, fontsize=8, loc="outside right upper", frameon=False)

    # QuPath image coordinates grow downward, so inverting y makes this look like the view
    # the user annotated in.
    axes.invert_yaxis()
    axes.set_aspect("equal")
    axes.axis("off")
    if title:
        axes.set_title(title, fontsize=10)

    return figure


def _draw(axes, subset: geopandas.GeoDataFrame, color: str, as_dots: bool, emphasised: bool) -> None:
    """Draw one class, either as outlines or as centroid dots."""
    if as_dots:
        centroids = subset.geometry.centroid
        axes.scatter(centroids.x, centroids.y, s=2 if emphasised else 1, c=color,
                     linewidths=0, zorder=3 if emphasised else 2)
        return

    polygons = [
        numpy.asarray(geometry.exterior.coords)
        for geometry in subset.geometry
        if geometry.geom_type == "Polygon"
    ]
    if not polygons:
        return
    axes.add_collection(
        PolyCollection(
            polygons,
            facecolors=color,
            edgecolors=color if emphasised else MUTED_EDGE,
            linewidths=0.3,
            zorder=3 if emphasised else 2,
        )
    )
    axes.autoscale_view()



def polygon_paths(subset) -> list:
    """Every polygon in a frame as a matplotlib path that keeps its holes.

    A region can completely surround tissue of another class, and that hole is not its tissue.
    Drawing the exterior ring alone paints straight over the class inside it — on the demo core
    one region did that to 207 000 µm² of another class, which is what made the picture look as
    though the merge had failed when it had not (`decisions.md` 070).

    The rings are **oriented** first: a compound path only reads an interior ring as a hole if
    it winds against the exterior, and GEOS makes no promise about which way a ring came out.
    Measured on the demo core, unoriented rings rendered the holes filled.
    """
    paths = []
    for geometry in subset.geometry:
        if geometry is None or geometry.is_empty:
            continue
        for polygon in getattr(geometry, "geoms", [geometry]):
            if polygon.geom_type != "Polygon":
                continue
            oriented = orient(polygon, sign=1.0)
            rings = [Path(numpy.asarray(oriented.exterior.coords))] + [
                Path(numpy.asarray(ring.coords)) for ring in oriented.interiors
            ]
            paths.append(Path.make_compound_path(*rings))
    return paths


def plot_regions_and_circles(
    regions,
    circles=None,
    replicate_of: pandas.Series | None = None,
    calibration_array: numpy.ndarray | None = None,
    title: str | None = None,
    figsize: tuple[float, float] = (11.0, 8.5),
) -> Figure:
    """The regions as a tissue map, with what will be cut drawn on top of them.

    Two variables in one picture, so they are encoded on two channels that cannot be mistaken
    for one another: **a pale fill is the class** — for the regions and for the circles alike, so
    a circle is visibly part of the tissue it came from — and **a dark outline is the
    replicate**. That way a user can see at once whether a class is being sampled evenly and
    whether the replicates are spread across it rather than clustered in one corner.

    Args:
        regions: the merged regions, carrying `classification_name`.
        circles: what will be cut, carrying `classification_name`. May be None or empty, in
            which case only the tissue map is drawn.
        replicate_of: replicate number per circle. Required to colour the outlines.
        calibration_array: 3x2 array; drawn as a dashed triangle if given.
        title: optional heading.
        figsize: inches.
    """
    figure = Figure(figsize=figsize, layout="constrained")
    axes = figure.add_subplot()

    classes = sorted(regions[CLASS_NAME].dropna().unique())
    fills = class_fill_colors(classes)

    for class_name in classes:
        paths = polygon_paths(regions[regions[CLASS_NAME] == class_name])
        if paths:
            axes.add_collection(
                PathCollection(
                    paths,
                    facecolors=[fills[class_name]],
                    edgecolors=[fills[class_name]],
                    alpha=REGION_FILL_ALPHA,
                    linewidths=0.6,
                    zorder=2,
                )
            )

    replicates: list[int] = []
    if circles is not None and len(circles) and replicate_of is not None:
        labels = replicate_of.reindex(circles.index)
        replicates = sorted({int(value) for value in labels.dropna().unique()})
        edges = replicate_colors(replicates)
        for class_name in classes:
            for number in replicates:
                subset = circles[(circles[CLASS_NAME] == class_name) & (labels == number)]
                paths = polygon_paths(subset)
                if not paths:
                    continue
                axes.add_collection(
                    PathCollection(
                        paths,
                        facecolors=[fills[class_name]],
                        edgecolors=[edges[number]],
                        linewidths=CIRCLE_EDGE_WIDTH,
                        zorder=3,
                    )
                )

    axes.autoscale_view()

    if calibration_array is not None and len(calibration_array) == 3:
        triangle = numpy.vstack([calibration_array, calibration_array[:1]])
        axes.plot(triangle[:, 0], triangle[:, 1], "--", color="#444444", linewidth=1, zorder=1)
        axes.scatter(
            calibration_array[:, 0], calibration_array[:, 1],
            marker="+", s=90, color="#444444", zorder=4,
        )

    _two_legends(figure, classes, fills, replicates)

    # QuPath image coordinates grow downward, so inverting y makes this look like the view
    # the user annotated in.
    axes.invert_yaxis()
    axes.set_aspect("equal")
    axes.axis("off")
    if title:
        axes.set_title(title, fontsize=10)

    logger.info(
        f"Plotting {len(regions)} regions"
        + (f" and {len(circles)} circles" if circles is not None else "")
    )
    return figure


def _two_legends(figure, classes, fills, replicates) -> None:
    """One key for the class fills and one for the replicate outlines.

    Both outside the axes: a legend over the tissue hides the thing being judged. Two separate
    keys rather than one combined, because the reader has to be able to tell which channel
    carries which meaning. The class swatches get a grey edge, since a pale fill on white is
    otherwise hard to see at legend size.
    """
    class_handles = [
        Line2D([], [], marker="s", linestyle="", markersize=10, markerfacecolor=fills[name],
               markeredgecolor="#888888", markeredgewidth=0.6, label=name)
        for name in classes
    ]
    if class_handles:
        first = figure.legend(
            handles=class_handles, title="Class (fill)", fontsize=8, title_fontsize=8,
            loc="outside right upper", frameon=False,
        )
        figure.add_artist(first)

    if not replicates:
        return
    edges = replicate_colors(replicates)
    replicate_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=10, markerfacecolor="none",
               markeredgecolor=edges[number], markeredgewidth=2, label=f"replicate {number}")
        for number in replicates
    ]
    figure.legend(
        handles=replicate_handles, title="Replicate (outline)", fontsize=8, title_fontsize=8,
        loc="outside right lower", frameon=False,
    )
