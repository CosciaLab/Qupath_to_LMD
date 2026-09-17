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
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from qupath_to_lmd.model import CLASS_NAME

# Okabe-Ito, which stays distinguishable for the common forms of colour blindness.
PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#D55E00", "#CC79A7", "#F0E442"]
MUTED = "#dcdcdc"
MUTED_EDGE = "#b4b4b4"

# Above this many shapes, draw one dot per shape instead of its outline. Drawing outlines is
# ~2s at 50k shapes and ~8s at 200k; centroids are 0.14s at 200k.
SHAPE_LIMIT = 20_000

# Replicates are told apart by the colour of a circle's outline, and tab20 is the standard
# categorical map with enough distinct entries to make more than a handful legible. Deliberately
# a different scale from the class colours above: one picture carries both, so they must not be
# mistakable for each other (`decisions.md` 069).
REPLICATE_COLORMAP = "tab20"

# A circle is drawn as a class-coloured disc with a replicate-coloured ring. The ring has to
# stay readable on a whole-core view, where a circle is a few pixels across, and it has to stay
# readable against a fill of a similar hue.
CIRCLE_FILL_ALPHA = 0.7
CIRCLE_EDGE_WIDTH = 1.5


def class_colors(classes: list[str]) -> dict[str, str]:
    """Stable colour per class: sorted, so a class keeps its colour across redraws."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sorted(classes))}


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


def replicate_colors(replicates: list[int]) -> dict[int, tuple]:
    """A colour per replicate number, from tab20, stable across redraws.

    Keyed by the replicate number rather than by position, so replicate 2 keeps its colour when
    a class with fewer replicates is added or removed. Cycles beyond 20 replicates, which is far
    more than a plate makes sense for.
    """
    colormap = colormaps[REPLICATE_COLORMAP]
    return {number: colormap(int(number - 1) % colormap.N) for number in sorted(set(replicates))}


def plot_regions_and_circles(
    regions,
    circles=None,
    replicate_of: pandas.Series | None = None,
    calibration_array: numpy.ndarray | None = None,
    title: str | None = None,
    figsize: tuple[float, float] = (11.0, 8.5),
) -> Figure:
    """The regions as a tissue map, with what will be cut drawn on top of them.

    Two variables in one picture, so they are encoded on two different channels:
    **fill colour is the class** — for the regions and for the circles inside them, so a circle
    is visibly part of the tissue it came from — and **outline colour is the replicate**, from
    tab20. That way a user can see at once whether a class is being sampled evenly and whether
    the replicates are spread across it rather than clustered in one corner.

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
    colors = class_colors(classes)

    # The tissue, faint: it is the backdrop against which the cuts are judged, not the subject.
    for class_name in classes:
        subset = regions[regions[CLASS_NAME] == class_name]
        polygons = _rings(subset)
        if polygons:
            axes.add_collection(
                PolyCollection(
                    polygons,
                    facecolors=colors[class_name],
                    edgecolors=colors[class_name],
                    alpha=0.25,
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
                polygons = _rings(subset)
                if not polygons:
                    continue
                axes.add_collection(
                    PolyCollection(
                        polygons,
                        facecolors=colors[class_name],
                        edgecolors=[edges[number]],
                        # The fill is held back and the outline pushed forward on purpose. Both
                        # palettes contain an orange, so an orange circle of an orange class
                        # would hide its own replicate ring at full opacity — and the ring is
                        # the only thing carrying the replicate.
                        alpha=CIRCLE_FILL_ALPHA,
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

    _two_legends(figure, classes, colors, replicates)

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


def _rings(subset) -> list:
    """Exterior rings of every Polygon in a frame, for a PolyCollection."""
    return [
        numpy.asarray(geometry.exterior.coords)
        for geometry in subset.geometry
        if geometry is not None and geometry.geom_type == "Polygon"
    ]


def _two_legends(figure, classes, colors, replicates) -> None:
    """One legend for the class fills and one for the replicate outlines.

    Both outside the axes: a legend over the tissue hides the thing being judged. Two separate
    legends rather than one combined, because the reader has to be able to tell which channel
    carries which meaning.
    """
    class_handles = [
        Line2D([], [], marker="s", linestyle="", markersize=9, markerfacecolor=colors[name],
               markeredgecolor="none", label=name)
        for name in classes
    ]
    if class_handles:
        first = figure.legend(
            handles=class_handles, title="Class", fontsize=8, title_fontsize=8,
            loc="outside right upper", frameon=False,
        )
        figure.add_artist(first)

    if not replicates:
        return
    edges = replicate_colors(replicates)
    replicate_handles = [
        Line2D([], [], marker="o", linestyle="", markersize=9, markerfacecolor="none",
               markeredgecolor=edges[number], markeredgewidth=2, label=f"replicate {number}")
        for number in replicates
    ]
    figure.legend(
        handles=replicate_handles, title="Replicate (outline)", fontsize=8, title_fontsize=8,
        loc="outside right lower", frameon=False,
    )
