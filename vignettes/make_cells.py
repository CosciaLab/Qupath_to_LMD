"""Cache the Exemplar001 cells that vignettes 2-4 are drawn from, as cells.parquet.

Run from the repo root:  uv run python vignettes/make_cells.py vignettes
"""

import sys
from pathlib import Path

from qupath_to_lmd import geojson

SOURCE = "/Users/jnimoca/Desktop/demo3/Exemplar001.ome.tif - Image0.geojson"

cells, _, _ = geojson.read_and_qc(SOURCE)
cells[["classification_name", "geometry"]].to_parquet(Path(sys.argv[1]) / "cells.parquet")
