#!/usr/bin/env python
"""Generate the outlines demo: two patients' tissue on one image, annotated with three classes.

    uv run python tools/make_outlines_demo.py

Writes `demo_Qupath_project/outlines_two_patients.geojson`. Seeded, so running it again gives
the same file.

What it holds, shaped like a QuPath annotation export:
- two pieces of tissue, one per patient, each with an outline annotation of class `P1` or `P2`;
- inside each, three annotations of each of `Gland`, `Stroma` and `Vessel`, drawn as lumpy loops
  the way a hand-drawn annotation is — the same three classes for both patients, which is the
  point: with `P1` and `P2` ticked as outline classes they become six samples, `P1_Gland` …
  `P2_Vessel`;
- one `Gland` annotation between the two pieces, inside neither outline, so the app's warning
  about shapes it will not collect has something to show;
- three named calibration points, `calib1` … `calib3`.
"""

import json
import math
import random
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "demo_Qupath_project" / "outlines_two_patients.geojson"
SEED = 7
PATIENTS = {"P1": (800, 820), "P2": (2250, 780)}
TISSUE_RADIUS = 560
CLASSES = {"Gland": [230, 159, 0], "Stroma": [86, 180, 233], "Vessel": [204, 121, 167]}
OUTLINE_COLOR = [96, 96, 96]
PER_CLASS = 3
STRUCTURE_RADIUS = (55, 95)
STRAY = ("Gland", (1520, 1380))
CALIBRATION = {"calib1": (120, 120), "calib2": (2900, 120), "calib3": (120, 1560)}


def _loop(rng: random.Random, cx: float, cy: float, radius: float, vertices: int = 48) -> list:
    """A lumpy closed loop, as drawn by hand: a circle with two slow wobbles."""
    a, b = rng.uniform(0.08, 0.18), rng.uniform(0.04, 0.10)
    p, q = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    ring = []
    for k in range(vertices):
        t = 2 * math.pi * k / vertices
        r = radius * (1 + a * math.sin(2 * t + p) + b * math.sin(3 * t + q))
        ring.append([round(cx + r * math.cos(t), 1), round(cy + r * math.sin(t), 1)])
    return ring + [ring[0]]


def _annotation(ring: list, class_name: str, color: list) -> dict:
    return {
        "type": "Feature",
        "id": str(uuid.UUID(int=random.getrandbits(128), version=4)),
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"objectType": "annotation", "classification": {"name": class_name, "color": color}},
    }


def _point(name: str, xy: tuple) -> dict:
    return {
        "type": "Feature",
        "id": str(uuid.UUID(int=random.getrandbits(128), version=4)),
        "geometry": {"type": "Point", "coordinates": list(xy)},
        "properties": {"objectType": "annotation", "name": name},
    }


def _structures(rng: random.Random, centre: tuple) -> list:
    """Three of each class, spread inside the tissue and clear of each other and of its edge."""
    placed: list[tuple[float, float, float]] = []
    features = []
    for class_name, color in CLASSES.items():
        for _ in range(PER_CLASS):
            radius = rng.uniform(*STRUCTURE_RADIUS)
            for _attempt in range(2000):
                angle, reach = rng.uniform(0, 2 * math.pi), (TISSUE_RADIUS - 2 * radius) * math.sqrt(rng.random())
                x, y = centre[0] + reach * math.cos(angle), centre[1] + reach * math.sin(angle)
                if all(math.dist((x, y), (px, py)) > 1.35 * (radius + pr) for px, py, pr in placed):
                    break
            placed.append((x, y, radius))
            features.append(_annotation(_loop(rng, x, y, radius), class_name, color))
    return features


def main() -> None:
    """Write the demo file."""
    rng = random.Random(SEED)
    random.seed(SEED)
    features = [_point(name, xy) for name, xy in CALIBRATION.items()]
    for patient, centre in PATIENTS.items():
        # the outline is drawn a little outside the tissue, as one would by hand
        features.append(_annotation(_loop(rng, *centre, TISSUE_RADIUS * 1.08, vertices=96), patient, OUTLINE_COLOR))
        features += _structures(rng, centre)
    stray_class, (x, y) = STRAY
    features.append(_annotation(_loop(rng, x, y, 70), stray_class, CLASSES[stray_class]))
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    print(f"{OUT.relative_to(OUT.parent.parent)}: {len(features)} features")


if __name__ == "__main__":
    main()
