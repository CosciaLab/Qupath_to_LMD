"""Vignette 6 (parent annotations): two patients on one image -> parent annotations name the samples -> one well each.

The annotator uses three classes for both patients and draws one parent annotation per patient. The app
names every shape after the parent annotation it sits in, so `Gland` becomes `P1_Gland` or `P2_Gland` and
the two patients never share a well. The shape between the two pieces belongs to neither and is
left out, drawn grey, as in the app.

Run from the repo root:  uv run python vignettes/vignette6.py vignettes
"""

import sys
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).parent))
import common

from qupath_to_lmd import geojson, parent_annotations, plot

OUT = Path(sys.argv[1])
shapes, calibration, report = geojson.read_and_qc("demo_Qupath_project/parent_annotations_two_patients.geojson")
# the demo is in image pixels, about six times the units the shared cards and crosses are sized for
SCALE = 0.18
shapes = shapes.assign(geometry=shapes.geometry.scale(SCALE, SCALE, origin=(0, 0)))
calibration = {name: (x * SCALE, y * SCALE) for name, (x, y) in calibration.items()}
PATIENTS = ["P1", "P2"]
grouped, parent_report = parent_annotations.group_by_parents(shapes, PATIENTS, report.multipolygons)
CLASSES = sorted(set(shapes.classification_name) - set(PATIENTS))
colors = plot.class_colors(CLASSES)
# a patient is told apart by its parent annotation, so its samples keep the class hue: P1 full, P2 tinted
tinted = plot.class_fill_colors(CLASSES)
sample_colors = {
    parent_annotations.grouped_name(patient, name): (colors[name] if patient == "P1" else tinted[name])
    for patient in PATIENTS
    for name in CLASSES
}
drawn = parent_report.parents
points = numpy.array(list(calibration.values()))
x0, y0 = numpy.minimum(shapes.total_bounds[:2], points.min(axis=0))
x1, y1 = numpy.maximum(shapes.total_bounds[2:], points.max(axis=0))


def slide(ax):
    """The image's extent, its tissue and its calibration points."""
    common.card(ax, x0, y0, x1, y1)
    # the tissue sits just inside the parent annotation the annotator drew around it
    common.draw(ax, drawn.geometry.buffer(-7), common.TISSUE, common.TISSUE_EDGE, lw=0.8, z=1)
    for x, y in calibration.values():
        common.cross(ax, x, y, arm=9)


def labels(ax, solid):
    """Each parent annotation named by its class, dashed as drawn in QuPath or solid as the app shows it."""
    if solid:
        common.draw(ax, drawn.geometry, "none", plot.PARENT_EDGE, lw=1.4, z=4)
    else:
        common.draw(ax, drawn.geometry, "none", "white", lw=3.2, z=4)
        common.draw(ax, drawn.geometry, "none", "#1f2328", lw=1.4, z=5, ls=(0, (4, 2)))
    for label, geometry in zip(drawn["label"], drawn.geometry, strict=True):
        top = geometry.bounds[1]
        ax.text(geometry.centroid.x, top - 6, label, ha="center", va="bottom", fontsize=11,
                color=common.INK, zorder=6, fontweight="bold")


fig, (one, two, three) = common.figure()

slide(one)
labels(one, solid=False)
for name in CLASSES:
    common.draw(one, shapes.geometry[shapes.classification_name == name], colors[name], "black", lw=0.6, z=3)

slide(two)
labels(two, solid=True)
common.draw(two, parent_report.left_out.geometry, plot.MUTED, plot.MUTED_EDGE, lw=0.6, z=3)
for name, color in sample_colors.items():
    common.draw(two, grouped.geometry[grouped.classification_name == name], color, "black", lw=0.6, z=3)

wells = {
    (row, 1 + k): (grouped.geometry[grouped.classification_name == parent_annotations.grouped_name(patient, name)],
                   sample_colors[parent_annotations.grouped_name(patient, name)])
    for row, patient in enumerate(PATIENTS, start=1)
    for k, name in enumerate(CLASSES)
}
common.plate(three, wells)
common.save(fig, OUT, "vignette6")
print(grouped.classification_name.value_counts().sort_index().to_dict(), len(parent_report.left_out))
