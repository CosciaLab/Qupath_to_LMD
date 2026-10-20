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
    n = len(plot.REPLICATE_PALETTE)
    colors = plot.replicate_colors(list(range(1, n + 3)))
    assert colors[n + 1] == colors[1], (
        "A replicate past the end of the palette did not wrap back to the first colour, so it "
        "would be drawn with something outside the palette."
    )


def _every_replicate():
    return list(plot.replicate_colors(list(range(1, len(plot.REPLICATE_PALETTE) + 1))).values())


def test_an_outline_is_always_visible_against_every_fill():
    """A white or yellow ring on a pale fill would vanish; the dark edge behind it carries it.

    Every ring is drawn over a dark edge, so what has to hold is that the edge stands out from
    every class fill and that each ring stands out from its edge. Without this the ring — the
    only thing carrying the replicate — disappears into the disc.
    """
    fills = plot.class_fill_colors([f"class {i}" for i in range(len(plot.PALETTE))]).values()
    worst_edge = min(_contrast(plot.REPLICATE_EDGE, fill) for fill in fills)
    assert worst_edge >= 1.7, (
        f"The ring edge reaches only {worst_edge:.2f} contrast on the least favourable class fill. "
        "Below about 1.7 a light ring on that class disappears into the disc."
    )
    rings = [ring for ring in _every_replicate() if _contrast(ring, plot.REPLICATE_EDGE) > 1.0]
    worst_ring = min(_contrast(ring, plot.REPLICATE_EDGE) for ring in rings)
    assert worst_ring >= 3.0, (
        f"A ring reaches only {worst_ring:.2f} contrast on its own dark edge, so it reads as a "
        "thicker edge rather than as its replicate's colour."
    )


def test_a_ring_never_repeats_its_class_colour():
    """Jose: the rings matched the classes "making a mess out of the colors" (`decisions.md` 082)."""
    closest = min(_delta_e(ring, color) for ring in _every_replicate() for color in plot.PALETTE)
    assert closest >= 25, (
        f"A replicate ring is only ΔE {closest:.1f} from a class colour, so it would be hard to "
        "tell from the class fill under it — it would read as part of the class, not as a replicate."
    )


def test_replicates_stay_distinguishable_from_each_other():
    """Rings must be far enough apart that two replicates never read as one."""
    outlines = _every_replicate()
    closest = min(
        _delta_e(first, second) for i, first in enumerate(outlines) for second in outlines[i + 1 :]
    )
    assert closest >= 25, (
        f"The two closest replicate rings are ΔE {closest:.1f} apart. Any closer and two "
        "replicates read as the same colour."
    )


def test_circles_are_filled_close_to_their_class_colour():
    assert plot.CLASS_FILL_TINT <= 0.35, (
        f"Circle fills are tinted {plot.CLASS_FILL_TINT} toward white, so circles go back to "
        "looking washed out, which Jose asked to fix."
    )


def test_every_ring_is_drawn_over_a_dark_edge(two_regions, circles):
    figure = plot.plot_regions_and_circles(two_regions, circles, replicate_of=circles[REPLICATE])
    circle_layers = _collections(figure)[len(two_regions) :]
    assert circle_layers and all(layer.get_path_effects() for layer in circle_layers), (
        "A circle was drawn without its dark edge, so a white or yellow ring on a pale class "
        "would not show."
    )
    handles = [legend for legend in figure.legends if "Replicate" in legend.get_title().get_text()]
    assert handles and all(line.get_path_effects() for line in handles[0].get_lines()), (
        "The replicate legend draws its rings without the dark edge, so replicate 1's white ring "
        "is invisible in the key."
    )


def _lab(color):
    """CIELAB (D65) of an sRGB colour, for perceptual distances."""
    from matplotlib.colors import to_rgb

    r, g, b = (
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in to_rgb(color)
    )
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def _delta_e(first, second):
    """CIE76 ΔE: about 2 is just noticeable, 25 and more reads as a different colour."""
    return sum((a - b) ** 2 for a, b in zip(_lab(first), _lab(second), strict=True)) ** 0.5


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
