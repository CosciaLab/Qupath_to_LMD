"""Shared fixtures.

Everything here comes from files committed to the repo or is generated on the fly, so the
suite runs in CI with no external data. Jose's real 83.7 MB export is deliberately not a
dependency.
"""

import json
import os

# py-lmd's Collection.plot calls plt.show(), which blocks forever under a GUI matplotlib
# backend — a plain `pytest` on macOS hangs without this. Set before anything imports pyplot,
# so the suite works with no environment variable to remember.
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib

matplotlib.use("Agg", force=True)

import geopandas
import numpy
import pytest
from loguru import logger
from shapely.geometry import box

from qupath_to_lmd import budget, geojson, plate, qc, selection
from qupath_to_lmd.model import CLASS_NAME, plan_from_selection

# The app logs every step through loguru, which would bury test output. Silence it for the
# suite; the assertions carry the diagnostics instead.
logger.remove()

DEMO = "demo_Qupath_project"

# Files committed to the repo, with what each one is good for.
ANNOTATIONS_FILE = f"{DEMO}/TD_01_verysmall_mIF.geojson"       # 6 annotations, no measurements
CELLS_FILE = f"{DEMO}/Single_cells.geojson"                    # 121 cells, 126 measurement fields
MULTICLASS_FILE = f"{DEMO}/multiclass_cells.geojson"            # real QuPath 0.7 shapes, no measurements
SAW_FILE = f"{DEMO}/demo_samples_and_wells.txt"
PARENTS_FILE = f"{DEMO}/parent_annotations_two_patients.geojson"     # two patients, a parent annotation each, 3 classes each

# The scale Single_cells.geojson implies, from its own QuPath measurements.
CELLS_PIXEL_SIZE = 0.3467


@pytest.fixture(scope="session")
def annotations():
    """The small annotation file: no measurements, so no implied scale."""
    return geojson.read_and_qc(ANNOTATIONS_FILE)


@pytest.fixture(scope="session")
def cells():
    """The single-cell file: carries QuPath measurements, so the scale is recoverable."""
    return geojson.read_and_qc(CELLS_FILE)


@pytest.fixture(scope="session")
def multiclass():
    """72 shapes from a real QuPath 0.7.0 export: single-class, multi-class and unclassified."""
    return geojson.read_and_qc(MULTICLASS_FILE)


@pytest.fixture
def cells_gdf(cells):
    """Just the shapes from the single-cell file."""
    return cells[0]


@pytest.fixture
def calibration(cells):
    """A valid calibration array for the single-cell file."""
    gdf, points, _report = cells
    return qc.triangle_qc(gdf, points, list(points)[:3]).calibration_array


@pytest.fixture
def touching_chain():
    """Twenty squares in a row, each touching the next.

    The largest set with no two touching is exactly ten, which makes it the one fixture where
    the adjacency preference can be checked against a known optimum.
    """
    n = 20
    return geopandas.GeoDataFrame(
        {CLASS_NAME: ["chain"] * n, "id": [f"s{i}" for i in range(n)], "objectType": ["cell"] * n},
        geometry=[box(i * 10, 0, i * 10 + 10, 10) for i in range(n)],
        crs=None,
    )


@pytest.fixture
def near_touching_chain():
    """Squares separated by a 0.5 px gap: visually adjacent, geometrically disjoint.

    This is what real QuPath cell segmentation produces — on an 8537-cell export the median
    boundary-to-boundary gap to the nearest neighbour was 0.57 px and only 4% of cells actually
    intersected. It is the fixture that shows why adjacency is judged by distance.
    """
    n, size, gap = 12, 10.0, 0.5
    return geopandas.GeoDataFrame(
        {CLASS_NAME: ["chain"] * n, "id": [f"n{i}" for i in range(n)], "objectType": ["cell"] * n},
        geometry=[box(i * (size + gap), 0, i * (size + gap) + size, size) for i in range(n)],
        crs=None,
    )


@pytest.fixture
def synthetic_cells(tmp_path):
    """Build a QuPath-shaped export of N cells on demand, for scale-dependent tests.

    Returns a callable so a test can ask for the size it needs. Written to `tmp_path` rather
    than committed, because the interesting sizes are far too large for a repository.
    """

    def build(n: int, classes=("Tumor", "Immune cells", "Stroma"), with_measurements=True):
        rng = numpy.random.default_rng(0)
        side = int(numpy.ceil(numpy.sqrt(n)))
        step, theta = 14.0, numpy.linspace(0, 2 * numpy.pi, 13)[:-1]
        features = [
            {
                "type": "Feature",
                "id": f"calib{i}",
                "geometry": {"type": "Point", "coordinates": [x, y]},
                "properties": {"objectType": "annotation", "name": f"calib{i}"},
            }
            for i, (x, y) in enumerate(
                [(-500.0, -500.0), (side * step + 500, -500.0), (-500.0, side * step + 500)], start=1
            )
        ]
        for k in range(n):
            cx = (k % side) * step + rng.uniform(-2, 2)
            cy = (k // side) * step + rng.uniform(-2, 2)
            radius = rng.uniform(4.0, 6.5)
            ring = [
                [round(float(cx + radius * numpy.cos(a)), 2), round(float(cy + radius * numpy.sin(a)), 2)]
                for a in theta
            ]
            ring.append(ring[0])
            properties = {
                "objectType": "cell",
                "classification": {"name": classes[k % len(classes)], "color": [1, 2, 3]},
            }
            if with_measurements:
                properties["measurements"] = {"Cell: Area": round(float(numpy.pi * radius**2 * 0.6535**2), 3)}
            features.append(
                {
                    "type": "Feature",
                    "id": f"c{k}",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": properties,
                }
            )

        path = tmp_path / f"synthetic_{n}.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        return str(path)

    return build


@pytest.fixture
def make_plan(calibration, cells):
    """Build a CollectionPlan from the single-cell file, for export and reporting tests."""
    gdf, points, _report = cells

    def build(replicates=2, per_replicate=10, wells=None, seed=1):
        classes = sorted(set(gdf[CLASS_NAME]))
        budgets = [budget.ClassBudget(name, replicates, per_replicate) for name in classes]
        result = selection.select(
            gdf, budgets, budget.BudgetMode.CELLS, selection.SelectionParams(seed=seed), CELLS_PIXEL_SIZE
        )
        usable = wells if wells is not None else plate.acceptable_wells("384", margins=1)
        assignment = plate.assign_wells(budget.group_keys(budgets), usable)
        plan, samples_and_wells = plan_from_selection(
            gdf=gdf,
            replicate_of=result.replicate_of,
            wells=usable,
            samples_and_wells=assignment,
            calibration_names=list(points)[:3],
            calibration_array=calibration,
            pixel_size_um=CELLS_PIXEL_SIZE,
            source_file="Single_cells.geojson",
            session_id="test",
        )
        return plan, samples_and_wells, result, budgets

    return build
