"""Vignette 1 (whole shapes): annotate on tissue -> the app sees classes -> one well per class.

Run from the repo root:  uv run python <scratchpad>/vignette1.py <scratchpad>
"""

import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).parent))
import common

from qupath_to_lmd import geojson, plot

OUT = Path(sys.argv[1])
shapes, calibration, _ = geojson.read_and_qc("demo_Qupath_project/TD_01_verysmall_mIF.geojson")
he = numpy.load(OUT / "he.npy")
classes = sorted(shapes.classification_name.unique())
colors = plot.class_colors(classes)
EXTENT = (0, he.shape[1], he.shape[0], 0)


def tissue(ax, faded):
    """The virtual H&E with its calibration points, faded when shapes go on top."""
    common.card(ax, 0, 0, he.shape[1], he.shape[0])
    image = 0.45 * he + 0.55 if faded else he
    ax.imshow(image, extent=EXTENT, zorder=1, interpolation="lanczos")
    for x, y in calibration.values():
        common.cross(ax, x, y, arm=16)


fig, (one, two, three) = common.figure()

tissue(one, faded=False)
# drawn by hand in QuPath: the outline is all there is until a class is given
common.draw(one, shapes.geometry, "none", "white", lw=3.2, z=3)
common.draw(one, shapes.geometry, "none", "#1f2328", lw=1.4, z=4, ls=(0, (4, 2)))

tissue(two, faded=True)
for name in classes:
    common.draw(two, shapes.geometry[shapes.classification_name == name], colors[name], "black", lw=0.8, z=3,
                alpha=0.85)

wells = {(1, 1 + k): (shapes.geometry[shapes.classification_name == name], colors[name])
         for k, name in enumerate(classes)}
common.plate(three, wells)
common.save(fig, OUT, "vignette1")
print(shapes.classification_name.value_counts().to_dict(), calibration)
