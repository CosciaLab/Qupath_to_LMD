"""The picture the user tunes the collection against.

It carries two variables at once — which class a shape belongs to and which replicate it goes
into — so the thing worth testing is that the two stay on separate channels and that a colour
means the same thing every time it is drawn.
"""

import geopandas
import numpy
import pandas
import pytest
import shapely
from matplotlib import colormaps
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.collections import PathCollection
from shapely.geometry import Point, box

from qupath_to_lmd import plot
from qupath_to_lmd.model import CLASS_NAME, REPLICATE


@pytest.fixture
def two_regions():
    """One region of each of two classes."""
    return geopandas.GeoDataFrame(
        {CLASS_NAME: ["Tumor", "Immune cells"]},
        geometry=[box(0, 0, 100, 100), box(100, 0, 200, 100)],
        crs=None,
    )


@pytest.fixture
def circles():
    """Two circles per class, one in each of two replicates."""
    rows = []
    for index, (class_name, replicate) in enumerate(
        [("Tumor", 1), ("Tumor", 2), ("Immune cells", 1), ("Immune cells", 2)]
    ):
        rows.append(
            {
                CLASS_NAME: class_name,
                REPLICATE: replicate,
                "geometry": Point(20 + 40 * index, 50).buffer(8),
            }
        )
    return geopandas.GeoDataFrame(rows, geometry="geometry", crs=None)


def _collections(figure):
    """Every path collection the figure drew, in the order it was added."""
    return [c for c in figure.axes[0].collections if isinstance(c, PathCollection)]


def test_a_replicate_keeps_its_colour_when_another_class_changes():
    """Colours are keyed by replicate number, not by position in the list.

    Keyed by position, adding a class with fewer replicates would shift every colour and the
    user would think the assignment had changed when only the legend had.
    """
    assert plot.replicate_colors([1, 2, 3])[2] == plot.replicate_colors([1, 2])[2], (
        "Replicate 2 changed colour when replicate 3 disappeared, so a user comparing two "
        "screenshots would read a change that did not happen."
    )


def test_replicate_colours_cycle_rather_than_run_out():
    """More replicates than the palette has must still each get a colour."""
    colormap = colormaps[plot.REPLICATE_COLORMAP]
    colors = plot.replicate_colors(list(range(1, colormap.N + 3)))
    assert colors[colormap.N + 1] == colors[1], (
        "A replicate past the end of the palette did not wrap back to the first colour, so it "
        "would be drawn with something outside the map."
    )


def test_an_outline_is_always_visible_against_every_fill():
    """Hue alone cannot separate the two channels: two full palettes collide exactly somewhere.

    Fills are tinted toward white and outlines shaded toward black so the separation is in
    lightness, which no pairing can defeat. Without this an orange circle of an orange class
    hid its own replicate ring — and the ring is the only thing carrying the replicate.
    """
    fills = plot.class_fill_colors([f"class {i}" for i in range(len(plot.PALETTE))]).values()
    outlines = plot.replicate_colors(list(range(1, 11))).values()

    worst = min(_contrast(outline, fill) for outline in outlines for fill in fills)
    assert worst >= 1.7, (
        f"The least contrasting outline-on-fill pair is at {worst:.2f}. Below about 1.7 the ring "
        "disappears into the disc and the replicate becomes unreadable."
    )


def test_replicates_stay_distinguishable_from_each_other():
    """Shading a palette compresses it, so the outlines must still be far enough apart."""
    outlines = list(plot.replicate_colors(list(range(1, 11))).values())
    closest = min(
        sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5
        for i, first in enumerate(outlines)
        for second in outlines[i + 1 :]
    )
    assert closest >= 0.18, (
        f"The two closest replicate outlines are {closest:.3f} apart in RGB. Any closer and two "
        "replicates read as the same colour."
    )


def _contrast(first, second):
    """WCAG relative-luminance contrast ratio between two colours."""
    from matplotlib.colors import to_rgb

    def luminance(color):
        channels = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in to_rgb(color)
        ]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def test_fill_is_the_class_and_outline_is_the_replicate(two_regions, circles):
    """The whole point of the picture: two variables, two channels.

    If both were on the fill, a user could not see which replicate a circle belongs to; if both
    were on the outline, they could not see which tissue it came from.
    """
    replicate_of = circles[REPLICATE]
    figure = plot.plot_regions_and_circles(two_regions, circles, replicate_of=replicate_of)

    class_colors = plot.class_fill_colors(sorted(two_regions[CLASS_NAME]))
    edges = plot.replicate_colors([1, 2])

    # The circle layers are those drawn above the two region layers.
    circle_layers = _collections(figure)[len(two_regions) :]
    assert circle_layers, "No circles were drawn on top of the regions."

    seen_fills, seen_edges = set(), set()
    for layer in circle_layers:
        seen_fills.add(tuple(round(v, 6) for v in layer.get_facecolor()[0][:3]))
        seen_edges.add(tuple(round(v, 6) for v in layer.get_edgecolor()[0][:3]))

    expected_fills = {tuple(round(v, 6) for v in color) for color in class_colors.values()}
    assert seen_fills <= expected_fills, (
        f"Circle fills {seen_fills} are not the class colours {expected_fills}, so a circle no "
        "longer shows which tissue it came from."
    )
    assert seen_edges == {tuple(round(v, 6) for v in color) for color in edges.values()}, (
        f"Circle outlines {seen_edges} are not the replicate colours, so the replicates cannot "
        "be told apart."
    )


def test_the_figure_carries_a_legend_for_each_channel(two_regions, circles):
    """Two meanings on one picture need two keys, or the reader has to guess which is which."""
    figure = plot.plot_regions_and_circles(
        two_regions, circles, replicate_of=circles[REPLICATE]
    )
    titles = {legend.get_title().get_text() for legend in figure.legends}
    assert any("Class" in title for title in titles), (
        "No class legend, so the fill colours mean nothing to the reader."
    )
    assert any("Replicate" in title for title in titles), (
        f"No replicate legend; legends present were {titles}. The outline colours would be "
        "undecodable."
    )


def test_the_regions_can_be_drawn_before_anything_is_packed(two_regions):
    """The tissue map is shown while the user is still choosing settings, with no circles yet."""
    figure = plot.plot_regions_and_circles(two_regions, None, replicate_of=None)
    assert _collections(figure), "The regions themselves were not drawn."
    assert not any("Replicate" in leg.get_title().get_text() for leg in figure.legends), (
        "A replicate legend was drawn with no replicates in the picture."
    )


def test_a_circle_with_no_replicate_is_not_drawn(two_regions, circles):
    """A circle with no replicate is not being collected, so it must not appear as if it were."""
    labels = pandas.Series([1, None, 2, None], index=circles.index, dtype="Int64")
    figure = plot.plot_regions_and_circles(two_regions, circles, replicate_of=labels)
    drawn = sum(len(layer.get_paths()) for layer in _collections(figure)[len(two_regions) :])
    assert drawn == 2, (
        f"{drawn} circles were drawn but only 2 have a replicate. Drawing the rest would show "
        "the user tissue that is not being collected."
    )


def test_the_y_axis_is_inverted_like_the_image(two_regions):
    """QuPath image coordinates grow downward, so an upright plot is upside down."""
    figure = plot.plot_regions_and_circles(two_regions)
    bottom, top = figure.axes[0].get_ylim()
    assert bottom > top, (
        "The y axis is not inverted, so the picture is a vertical mirror of what the user "
        "annotated in QuPath and they cannot match one to the other."
    )


def test_a_hole_in_a_region_is_not_painted_over():
    """A region can completely surround tissue of another class, and that hole is not its tissue.

    Drawing the exterior ring alone painted straight over the class inside it — on the demo core
    one region did that to 207,000 µm² of another class, which made the picture look as though
    the merge had failed when it had not. This renders the figure and checks the pixel in the
    hole, because whether a compound path reads as a hole depends on ring orientation and
    nothing short of drawing it proves the orientation is right.
    """
    holed = geopandas.GeoDataFrame(
        {CLASS_NAME: ["Tumor"]},
        geometry=[shapely.Polygon(box(0, 0, 100, 100).exterior, [box(40, 40, 60, 60).exterior])],
        crs=None,
    )
    figure = plot.plot_regions_and_circles(holed, figsize=(2, 2))
    FigureCanvasAgg(figure)
    axes = figure.axes[0]
    axes.set_xlim(0, 100)
    axes.set_ylim(0, 100)
    figure.canvas.draw()

    pixels = numpy.asarray(figure.canvas.buffer_rgba())
    height, width, _ = pixels.shape
    in_hole = tuple(int(v) for v in pixels[height // 2, width // 2][:3])
    in_body = tuple(int(v) for v in pixels[int(height * 0.85), int(width * 0.15)][:3])

    assert in_hole != in_body, (
        f"The hole and the region body are both {in_hole}, so the hole was filled in. A region "
        "would be drawn over the class it surrounds and the user would read that as a broken "
        "merge."
    )
