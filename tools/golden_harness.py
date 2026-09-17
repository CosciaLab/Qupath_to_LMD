#!/usr/bin/env python
"""Golden-file harness: prove that a change did not alter the collection output.

This is a characterization test, not a correctness test. It cannot tell you the laser cut
the right tissue — it tells you the bytes are the same as they were before your change.
That is the check that matters for refactors, because a coordinate shifted by a pixel, an
inverted Y flip or a different shape order are all invisible in the running app.

Usage:

    python tools/golden_harness.py check      # compare current output against tools/golden/
    python tools/golden_harness.py capture    # re-bless: overwrite tools/golden/

`check` exits non-zero on any mismatch, so it can gate a commit.

Run `capture` only when output is *meant* to change, and say so in the commit message —
re-blessing silently is how a real regression gets frozen into the reference. Never
hand-edit the files in tools/golden/.

What it does not cover: the UI, the intentional behaviour changes around QC and warnings,
LineString geometries (no demo file has one), and any input shape outside the cases below.
Add cases as the app grows.

The `regions` and `packing` cases are the ones whose bytes depend on GEOS as well as on this
repo: merging regions of a class is a union, and a GEOS upgrade can legitimately reorder the
vertices it returns. `packing` additionally pins numpy's random stream — if it differs on its
own, a recorded seed no longer reproduces its collection, which is a breaking change for
anybody who wrote one into a methods section. If only those cases differ after a dependency
bump, that is what happened: check the geometry is equivalent before re-blessing, and say so in
the commit.
"""

import os
import sys
from pathlib import Path

# py-lmd's Collection.plot() blocks forever under a GUI matplotlib backend, so a plain
# `python` run on macOS hangs without this. Must precede importing anything that pulls in
# matplotlib.
os.environ.setdefault("MPLBACKEND", "Agg")

from qupath_to_lmd import export, geojson, packing, plate, qc, regions
from qupath_to_lmd.budget import ClassBudget
from qupath_to_lmd.model import CLASS_NAME, REPLICATE, plan_from_class_wells, plan_from_selection

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
DEMO = REPO / "demo_Qupath_project"

# Each case exercises a path where a refactor could silently change coordinates.
CASES = {
    # the ordinary mini-bulk path
    "annotations": {"source": DEMO / "TD_01_verysmall_mIF.geojson"},
    # many small shapes, cell objects carrying measurements
    "cells": {"source": DEMO / "Single_cells.geojson"},
    # the explode path: one well per shape
    "cells_exploded": {"source": DEMO / "Single_cells.geojson", "explode": ["single_cells_demo"]},
    # plate geometry differs, so the CSV and well validation do too
    "annotations_96": {"source": DEMO / "TD_01_verysmall_mIF.geojson", "plate_type": "96", "margin": 0},
    # a realistic QuPath 0.7 cell export: multi-class objects and unclassified objects mixed in
    "multiclass_cells": {"source": DEMO / "multiclass_cells.geojson"},
    # the regions path: Voronoi projection, merge by class, and shapes the app synthesised
    # rather than read, so the coordinates come from geometry code rather than from the file
    "regions": {
        "kind": "regions",
        "source": DEMO / "multiclass_cells.geojson",
        "replicates": 2,
    },
    # the packing path: circles this app invented, placed by a seeded random walk
    "packing": {
        "kind": "packing",
        "source": DEMO / "multiclass_cells.geojson",
        "replicates": 2,
        "pixel_size_um": 0.6535,
    },
}


def run_case(source, explode=None, plate_type="384", margin=1) -> tuple[str, str]:
    """Drive the whole pipeline for one case and return its XML and CSV.

    Deliberately builds the samples-and-wells scheme from sorted class names rather than
    from the plate UI, so the harness depends only on library code and stays stable.
    """
    gdf, calibration_points, _report = geojson.read_and_qc(str(source))
    calibration_names = list(calibration_points)[:3]
    triangle = qc.triangle_qc(gdf, calibration_points, calibration_names)

    if explode:
        gdf = geojson.explode_classes(gdf, explode)

    wells = plate.acceptable_wells(plate=plate_type, margins=margin)
    classes = sorted(set(gdf[CLASS_NAME]))
    samples_and_wells = dict(zip(classes, wells, strict=False))

    plan = plan_from_class_wells(
        gdf=gdf,
        samples_and_wells=samples_and_wells,
        calibration_names=calibration_names,
        calibration_array=triangle.calibration_array,
        source_file=Path(source).name,
        session_id="golden",
    )
    result = export.build_collection(plan, samples_and_wells=samples_and_wells, plate=plate_type)
    return result.xml, result.csv


def run_regions_case(
    source, replicates=1, radius_factor=3.0, plate_type="384", margin=1
) -> tuple[str, str]:
    """Drive the regions pipeline for one case and return its XML and CSV.

    Worth its own reference: every coordinate here is computed rather than read from the file,
    so a change in how regions are tessellated, capped, clipped or merged shows up as different
    bytes instead of as a picture nobody compares.
    """
    gdf, calibration_points, _report = geojson.read_and_qc(str(source))
    calibration_names = list(calibration_points)[:3]
    triangle = qc.triangle_qc(gdf, calibration_points, calibration_names)

    params = regions.RegionParams(radius_factor=radius_factor)
    patches, _region_report = regions.project(gdf, params)
    patches = geojson.synthesize_qupath_columns(patches, "region", source=gdf)

    counts = dict.fromkeys(sorted(set(patches[CLASS_NAME])), replicates)
    replicate_of = regions.deal_patches(patches, counts)

    wells = plate.acceptable_wells(plate=plate_type, margins=margin)
    plan, samples_and_wells = plan_from_selection(
        gdf=patches,
        replicate_of=replicate_of,
        wells=wells,
        calibration_names=calibration_names,
        calibration_array=triangle.calibration_array,
        source_file=Path(source).name,
        session_id="golden",
        workflow="regions",
    )
    result = export.build_collection(plan, samples_and_wells=samples_and_wells, plate=plate_type)
    return result.xml, result.csv


def run_packing_case(
    source, replicates=1, pixel_size_um=0.6535, radius_factor=3.0, plate_type="384", margin=1, seed=0
) -> tuple[str, str]:
    """Drive the circle-packing pipeline for one case and return its XML and CSV.

    Guards the thing that would otherwise be invisible: that a given seed still places the same
    circles in the same places. If this drifts, every collection anyone has recorded a seed for
    stops being reproducible, and nothing in the running app would show it.

    Driven from a demo file rather than a synthetic square, which the plan for this feature
    originally proposed. A synthetic square would keep the bytes free of GEOS, but the `regions`
    case already depends on GEOS so that buys no new robustness, and going through the real file
    covers projection, merging, the synthesised QuPath fields, dealing and export as well.
    `tests/test_packing.py` carries the seed-stability guarantee on its own.
    """
    gdf, calibration_points, _report = geojson.read_and_qc(str(source))
    calibration_names = list(calibration_points)[:3]
    triangle = qc.triangle_qc(gdf, calibration_points, calibration_names)

    patches, _region_report = regions.project(gdf, regions.RegionParams(radius_factor=radius_factor))
    params = packing.PackingParams(seed=seed)
    budgets = [
        ClassBudget(name, replicates, 2_000.0) for name in sorted(set(patches[CLASS_NAME]))
    ]
    result = packing.pack(patches, budgets, params, pixel_size_um)

    circles = geojson.synthesize_qupath_columns(result.circles, "circle", source=gdf)
    wells = plate.acceptable_wells(plate=plate_type, margins=margin)
    plan, samples_and_wells = plan_from_selection(
        gdf=circles,
        replicate_of=circles[REPLICATE],
        wells=wells,
        calibration_names=calibration_names,
        calibration_array=triangle.calibration_array,
        source_file=Path(source).name,
        session_id="golden",
        pixel_size_um=pixel_size_um,
        workflow="regions",
    )
    result = export.build_collection(plan, samples_and_wells=samples_and_wells, plate=plate_type)
    return result.xml, result.csv


def _run(kind: str = "annotations", **kwargs) -> tuple[str, str]:
    """Dispatch a case to the pipeline it exercises."""
    if kind == "regions":
        return run_regions_case(**kwargs)
    if kind == "packing":
        return run_packing_case(**kwargs)
    return run_case(**kwargs)


def capture() -> int:
    """Write current output to tools/golden/, replacing what is there."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, kwargs in CASES.items():
        xml, csv = _run(**kwargs)
        (GOLDEN_DIR / f"{name}.xml").write_text(xml)
        (GOLDEN_DIR / f"{name}.csv").write_text(csv)
        print(f"captured  {name}  xml={len(xml)}B csv={len(csv)}B")
    print(f"\nGolden files written to {GOLDEN_DIR.relative_to(REPO)}. Commit them with your change.")
    return 0


def check() -> int:
    """Compare current output against the golden files."""
    if not GOLDEN_DIR.exists():
        print(f"No golden files at {GOLDEN_DIR}. Run `capture` first.", file=sys.stderr)
        return 2

    mismatches = []
    for name, kwargs in CASES.items():
        produced = dict(zip(("xml", "csv"), _run(**kwargs), strict=True))
        for kind, content in produced.items():
            reference_path = GOLDEN_DIR / f"{name}.{kind}"
            if not reference_path.exists():
                print(f"MISSING  {name}.{kind} has no golden file")
                mismatches.append(f"{name}.{kind}")
                continue
            reference = reference_path.read_text()
            if content == reference:
                print(f"match    {name}.{kind}  ({len(content)}B)")
            else:
                print(f"DIFFER   {name}.{kind}  produced {len(content)}B, golden {len(reference)}B")
                mismatches.append(f"{name}.{kind}")

    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es): {', '.join(mismatches)}")
        print("If this change was meant to alter output, re-run with `capture` and say so in the commit.")
        return 1

    print(f"\nAll {len(CASES) * 2} artefacts identical.")
    return 0


def main() -> int:
    """Dispatch on the subcommand."""
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        return check()
    if command == "capture":
        return capture()
    print(f"Unknown command {command!r}. Use `check` or `capture`.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
