"""The picture the user tunes the collection against.

It carries two variables at once — which class a shape belongs to and which replicate it goes
into — so the thing worth testing is that the two stay on separate channels and that a colour
means the same thing every time it is drawn.
"""

import geopandas
import pandas
import pytest
from matplotlib import colormaps
from matplotlib.collections import PolyCollection
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
    """Every PolyCollection the figure drew, in the order it was added."""
    return [c for c in figure.axes[0].collections if isinstance(c, PolyCollection)]


def test_a_replicate_keeps_its_colour_when_another_class_changes():
    """Colours are keyed by replicate number, not by position in the list.

    Keyed by position, adding a class with fewer replicates would shift every colour and the
    user would think the assignment had changed when only the legend had.
    """
    assert plot.replicate_colors([1, 2, 3])[2] == plot.replicate_colors([1, 2])[2], (
        "Replicate 2 changed colour when replicate 3 disappeared, so a user comparing two "
        "screenshots would read a change that did not happen."
    )


def test_replicate_colours_come_from_tab20_and_cycle():
    """tab20 is the one categorical map with enough entries to tell many replicates apart."""
    colormap = colormaps[plot.REPLICATE_COLORMAP]
    colors = plot.replicate_colors(list(range(1, 23)))
    assert colors[1] == colormap(0), "Replicate 1 is not the first tab20 colour."
    assert colors[21] == colors[1], (
        "A 21st replicate did not wrap back to the first colour, so it would be drawn with "
        "something outside the map."
    )


def test_fill_is_the_class_and_outline_is_the_replicate(two_regions, circles):
    """The whole point of the picture: two variables, two channels.

    If both were on the fill, a user could not see which replicate a circle belongs to; if both
    were on the outline, they could not see which tissue it came from.
    """
    replicate_of = circles[REPLICATE]
    figure = plot.plot_regions_and_circles(two_regions, circles, replicate_of=replicate_of)

    class_colors = plot.class_colors(sorted(two_regions[CLASS_NAME]))
    edges = plot.replicate_colors([1, 2])

    # The circle layers are those drawn above the two region layers.
    circle_layers = _collections(figure)[len(two_regions) :]
    assert circle_layers, "No circles were drawn on top of the regions."

    seen_fills, seen_edges, alphas = set(), set(), set()
    for layer in circle_layers:
        face = layer.get_facecolor()[0]
        # Compare hue only: the fill is drawn at less than full opacity on purpose, so its
        # alpha will not match the palette entry.
        seen_fills.add(tuple(face[:3]))
        alphas.add(round(float(face[3]), 3))
        seen_edges.add(tuple(layer.get_edgecolor()[0][:3]))

    expected_fills = {tuple(_rgba(color)[:3]) for color in class_colors.values()}
    assert seen_fills <= expected_fills, (
        f"Circle fills {seen_fills} are not the class colours {expected_fills}, so a circle no "
        "longer shows which tissue it came from."
    )
    assert seen_edges == {tuple(color[:3]) for color in edges.values()}, (
        f"Circle outlines {seen_edges} are not the tab20 replicate colours, so the replicates "
        "cannot be told apart."
    )
    assert alphas == {plot.CIRCLE_FILL_ALPHA}, (
        f"Circle fills were drawn at opacity {alphas}, not {plot.CIRCLE_FILL_ALPHA}. Both "
        "palettes contain an orange, so a full-opacity fill hides the replicate ring on top "
        "of it — which is the only thing carrying the replicate."
    )


def _rgba(color):
    """A matplotlib colour spec as an RGBA tuple, for comparing against a collection."""
    from matplotlib.colors import to_rgba

    return to_rgba(color)


def test_the_figure_carries_a_legend_for_each_channel(two_regions, circles):
    """Two meanings on one picture need two keys, or the reader has to guess which is which."""
    figure = plot.plot_regions_and_circles(
        two_regions, circles, replicate_of=circles[REPLICATE]
    )
    titles = {legend.get_title().get_text() for legend in figure.legends}
    assert "Class" in titles, "No class legend, so the fill colours mean nothing to the reader."
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
