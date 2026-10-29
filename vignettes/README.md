# Vignettes

The figures in the top-level README. Run everything from the repo root.

1. `uv run python vignettes/make_cells.py vignettes` — cells.parquet from the Exemplar001 export
2. `uv run --with tifffile python vignettes/virtual_he.py demo_Qupath_project/TD_01_verysmall_mIF.ome.tif vignettes/he.npy`
3. `uv run python vignettes/vignetteN.py vignettes` — writes vignetteN.png and .svg

Vignette 6 reads `demo_Qupath_project/parent_annotations_two_patients.geojson`, which
`uv run python tools/make_parent_annotations_demo.py` regenerates; it needs neither step 1 nor 2.

cells.parquet is committed because the Exemplar001 export it comes from is not in the repo, so
step 1 is only needed to rebuild it. he.npy (vignette 1's background) is not committed; step 2
regenerates it from the demo image. Only the SVGs are committed — the PNGs are for a quick look.
