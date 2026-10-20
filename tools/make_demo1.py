#!/usr/bin/env python
"""Generate demo1: six synthetic QuPath cell exports, two patients × three serial sections.

    uv run python tools/make_demo1.py

Writes `demo_Qupath_project/demo1/P<patient>_S<section>.geojson`. Seeded, so running it again
gives the same files.

What each slide holds, shaped like a QuPath 0.7 cell export:
- about 150 immune, 500 cancer and 1000 stroma cells (±8% per section), classes prefixed with
  the patient (`P1_Immune`, `P1_Cancer`, `P1_Stroma`) so the three sections of one patient pool
  together and the two patients never do;
- cell areas between 100 and 500 µm², most of them round, a fifth elongated, none overlapping;
- cancer in a few nests, stroma around them, immune cells mostly at the nest borders — so the
  regions-and-circles method has contiguous tissue to work with;
- `Cell: Area` and `Cell: Perimeter` measurements, so the app estimates the scale (0.5 µm/px);
- five named calibration points, `calib1` … `calib5`, around the tissue.

Serial sections of one patient share a tissue layout, shifted and turned slightly as a real
section would be on its slide, with different cells in each.
"""

import json
import math
import random
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "demo_Qupath_project" / "demo1"
PIXEL_SIZE_UM = 0.5
IMAGE_SIZE_PX = 3400
COUNTS = {"Immune": 150, "Cancer": 500, "Stroma": 1000}
COLORS = {"Immune": [220, 50, 47], "Cancer": [230, 159, 0], "Stroma": [86, 180, 233]}
AREA_UM2 = (100.0, 500.0)
ELONGATED_SHARE = 0.2
MIN_GAP_PX = 2.0
VERTICES = 20


def _layout(rng: random.Random) -> dict:
    """One patient's tissue: an ellipse with cancer nests inside it, in tissue coordinates."""
    radius = 1400.0
    nests = []
    while len(nests) < 3:
        angle, distance = rng.uniform(0, 2 * math.pi), rng.uniform(0, 0.5 * radius)
        centre = (distance * math.cos(angle), distance * math.sin(angle))
        nest_radius = rng.uniform(380, 450)
        if all(math.dist(centre, other) > nest_radius + r + 60 for other, r in nests):
            nests.append((centre, nest_radius))
    return {"radius": radius, "aspect": rng.uniform(0.8, 0.95), "nests": nests}


def _inside_tissue(point, layout) -> bool:
    x, y = point
    return (x / layout["radius"]) ** 2 + (y / (layout["radius"] * layout["aspect"])) ** 2 <= 1


def _nest_distance(point, layout) -> float:
    """Distance to the nearest nest border: negative inside a nest."""
    return min(math.dist(point, centre) - radius for centre, radius in layout["nests"])


def _where(kind: str, point, layout) -> bool:
    if not _inside_tissue(point, layout):
        return False
    border = _nest_distance(point, layout)
    if kind == "Cancer":
        return border < 0
    if kind == "Stroma":
        return border > 0
    return 0 < border < 160  # immune cells gather at the nest borders


def _cell(rng: random.Random, centre) -> tuple[list[list[float]], float]:
    """A cell outline around `centre`, and its bounding radius. Round, or a fifth of the time oval."""
    area_px = rng.uniform(*AREA_UM2) / PIXEL_SIZE_UM**2
    aspect = rng.uniform(1.2, 1.6) if rng.random() < ELONGATED_SHARE else 1.0
    a = math.sqrt(area_px * aspect / math.pi)
    b = a / aspect
    turn = rng.uniform(0, math.pi)
    ring = []
    for k in range(VERTICES):
        t = 2 * math.pi * k / VERTICES
        wobble = 1 + rng.uniform(-0.03, 0.03)
        x, y = a * math.cos(t) * wobble, b * math.sin(t) * wobble
        ring.append([
            round(centre[0] + x * math.cos(turn) - y * math.sin(turn), 2),
            round(centre[1] + x * math.sin(turn) + y * math.cos(turn), 2),
        ])
    ring.append(ring[0])
    return ring, a * 1.04


def _polygon_area(ring) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False))) / 2


def _perimeter(ring) -> float:
    return sum(math.dist(p, q) for p, q in zip(ring, ring[1:], strict=False))


def _section(patient: int, section: int, layout: dict) -> dict:
    rng = random.Random(1000 * patient + section)
    # A serial section lands on its slide shifted and turned a little.
    shift = (IMAGE_SIZE_PX / 2 + rng.uniform(-60, 60), IMAGE_SIZE_PX / 2 + rng.uniform(-60, 60))
    turn = math.radians(rng.uniform(-4, 4))

    def to_image(point):
        x, y = point
        return (shift[0] + x * math.cos(turn) - y * math.sin(turn), shift[1] + x * math.sin(turn) + y * math.cos(turn))

    def new_id() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128)))

    features = []
    for n in range(5):
        angle = 2 * math.pi * n / 5 + math.radians(18)
        x, y = to_image((1.12 * layout["radius"] * math.cos(angle), 1.12 * layout["radius"] * layout["aspect"] * math.sin(angle)))
        features.append({
            "type": "Feature", "id": new_id(),
            "geometry": {"type": "Point", "coordinates": [round(x, 2), round(y, 2)]},
            "properties": {"objectType": "annotation", "name": f"calib{n + 1}"},
        })

    placed: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    cell = 60.0

    def free(x, y, r) -> bool:
        gx, gy = int(x // cell), int(y // cell)
        return all(
            math.dist((x, y), (ox, oy)) >= r + orad + MIN_GAP_PX
            for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            for ox, oy, orad in placed.get((gx + dx, gy + dy), ())
        )

    span = layout["radius"]
    for kind, count in COUNTS.items():
        wanted = round(count * rng.uniform(0.92, 1.08))
        made, attempts = 0, 0
        while made < wanted and attempts < 200_000:
            attempts += 1
            local = (rng.uniform(-span, span), rng.uniform(-span, span))
            if not _where(kind, local, layout):
                continue
            centre = to_image(local)
            ring, radius = _cell(rng, centre)
            if not free(centre[0], centre[1], radius):
                continue
            placed.setdefault((int(centre[0] // cell), int(centre[1] // cell)), []).append((*centre, radius))
            area_um2 = _polygon_area(ring) * PIXEL_SIZE_UM**2
            features.append({
                "type": "Feature", "id": new_id(),
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {
                    "objectType": "cell",
                    "classification": {"name": f"P{patient}_{kind}", "color": COLORS[kind]},
                    "measurements": {
                        "Cell: Area": round(area_um2, 2),
                        "Cell: Perimeter": round(_perimeter(ring) * PIXEL_SIZE_UM, 2),
                    },
                },
            })
            made += 1
        print(f"  P{patient}_S{section} {kind}: {made} of {wanted}")
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    """Write the six slides."""
    OUT.mkdir(parents=True, exist_ok=True)
    for patient in (1, 2):
        layout = _layout(random.Random(patient))
        for section in (1, 2, 3):
            path = OUT / f"P{patient}_S{section}.geojson"
            path.write_text(json.dumps(_section(patient, section, layout)))
            print(f"wrote {path.relative_to(OUT.parent.parent)} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
