# facts.md

Living record of what is true about this app. Corrected in place when it goes stale — not
append-only (that is `decisions.md`). Last verified: 2026-08-27.

Restructuring into two workflows is done: `ROADMAP.md` Phases 0-6 are complete. Both
workflows reach a downloadable collection.

---

## What the app is for

Web app that turns QuPath annotations into a Leica LMD7 cutting file, for Deep Visual
Proteomics (DVP) experiments. Scientists annotate tissue in QuPath, export a GeoJSON,
upload it here, choose which class goes into which well of a 96- or 384-well plate, and
download an `.xml` the LMD software can execute.

Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework. >60 unique users.
Cites Makhmut et al., *Cell Systems* 14, 1002-1014.e5 (2023).

## Deployment

- Live at `https://qupath-to-lmd-mdcberlin.streamlit.app/` on Streamlit Community Cloud.
- Cloud installs from **`requirements.txt`**, which is generated:
  `uv pip compile pyproject.toml -o requirements.txt`. Local dev uses `uv sync` off
  `pyproject.toml` + `uv.lock`. All three must agree or local and deployed diverge.
- `git remote origin` = `https://github.com/josenimo/Qupath_to_LMD.git`; the README and
  `pyproject.toml` URLs point at `CosciaLab/Qupath_to_LMD`. Branches: `master` (default),
  `dev`.
- Python `>= 3.11, < 3.14`. Version in `pyproject.toml`: `0.2.0`.

## Layout

Since Phase 0 (`decisions.md` 021), `core.py` and `utils.py` are gone and the library is
split by responsibility. Library functions take explicit arguments and return values —
none of them read `st.session_state`.

```
streamlit_app.py                  session init, logging, and the workflow router. Thin.
src/qupath_to_lmd/
  === library layer: pure, no Streamlit ===
  model.py                        CollectionPlan, canonical column names, provenance
  geojson.py                      read_and_qc, explode_classes, extract_coordinates,
                                  rewrite_classification, sanitize_for_qupath,
                                  synthesize_qupath_columns, classification_values,
                                  implied_pixel_size, drop_unused_columns
  plate.py                        plate shapes, acceptable_wells, layouts, saw parse/convert
  qc.py                           triangle_qc, validate_saw, compare_pixel_size (reports)
  stats.py                        class_statistics, for_display, reference_pixel_sizes
  budget.py                       BudgetMode, ClassBudget, feasibility, total_groups
  selection.py                    SelectionMode, SelectionParams, select, grid_bins
  regions.py                      RegionParams, project, voronoi_regions, merge_by_class,
                                  close_slivers, deal_patches, tissue_hull,
                                  median_cell_spacing
  packing.py                      ClassPacking, PackingParams, pack, capacity,
                                  packable_area, class_generator, smoothing_loss
  plot.py                         plot_shapes — class overview, selection preview, QC image;
                                  plot_regions_and_circles — the regions feedback picture
  export.py                       build_collection, build_bundle, PathOrder,
                                  order_for_cutting, path_stats, ORIENTATION_TRANSFORM
  extras.py                       QuPath classes.json generation
  === UI layer: Streamlit, owns session_state ===
  ui_shared.py                    steps every workflow uses, incl. class_selection_step
                                  (which draws the input beside its own table)
  ui_legacy.py                    annotations workflow (frozen as of Phase 1)
  ui_cells.py                     cell-segmentation workflow (step 8 is an st.fragment)
  ui_packing.py                   cellular-neighbourhood workflow
  === other ===
  mock_streamlit.py               patch_streamlit() — stubs st.* for notebook use
  __init__.py                     empty
tools/
  golden_harness.py               byte-equality regression gate
  golden/                         14 reference artefacts, 7 cases
demo_Qupath_project/              real QuPath project used as test fixture
  TD_01_verysmall_mIF.geojson     9 features: 6 annotation Polygons + 3 calibration Points
  Single_cells.geojson            131 features: 121 cells + 7 annotations + 3 Points
  multiclass_cells.geojson        72 cells from a real QuPath 0.7.0 export: single-class,
                                  multi-class and unclassified, plus 3 added calib points
  demo_samples_and_wells.txt      python-dict-literal saw file for the upload path
  QuPath_scripts/*.groovy         detections_to_annotations, select_random_detections
assets/                           screenshots, example classes.json
```

Key dependency: **`py-lmd`** (`lmd.lib.Collection`) does the actual coordinate transform
and XML writing. Also geopandas/shapely for geometry, loguru for logging.

## What QuPath actually puts in the GeoJSON

Verified against both demo files.

- `id` — uuid string, stable per object. `objectType` — `"annotation"`, `"cell"`, or
  `"detection"`. **`objectType` is the natural discriminator between the two planned
  workflows**, and a single file can legitimately mix them (`Single_cells.geojson` has both).
- `classification` — arrives as a **JSON string** (`'{ "name": ..., "color": [r,g,b] }'`),
  not a dict, hence the `ast.literal_eval` on it. Absent for unclassified objects. Comes in
  three shapes, all seen in one real QuPath 0.7.0 export:
  - `{"name": "Tumor", "color": [...]}` — a single class
  - `{"names": ["Tumor", "Immune cells"], "color": [...]}` — **multi-class**, note the
    plural. Sorted and joined with `--` (`geojson.MULTICLASS_SEPARATOR`) into one flat
    combined class the user can assign a well to, e.g. `Immune cells--Tumor`. Sorted so one
    set of classes always yields one class name regardless of the order QuPath wrote them.
  - missing entirely — unclassified, dropped with a count
- `name` — set on calibration Points, `None` on annotations and cells. **QuPath omits a
  property entirely when no object in the export has it**, so a file with no calibration
  points has no `name` column at all. That is the single most common shape of a "broken"
  export and it is not broken — the user just has not added the points, or exported a
  selection that left them out.
- `isLocked` — present on annotations, NaN elsewhere.
- `measurements` — **only on cells/detections**, a JSON string with QuPath's per-object
  measurements. `Single_cells.geojson` carries **126 fields** per cell:
  `Cell: Area`, `Perimeter`, `Circularity`, `Max/Min caliper`, `Eccentricity`, and
  mean/std/max/min per marker channel (DAPI, Vimentin, CD3e, panCK, CD8, Ki67, COL1A1,
  CD20, CD68, and the `_bg` channels). This is the raw material for the planned per-class
  statistics and for measurement-ranked selection.
- **Coordinates are in image pixels**, but `Cell: Area` is in **µm²**. So the scale is
  recoverable: `sqrt(Cell:Area / polygon_area_px)` gives 0.3467 µm/px across all 121 cells
  of `Single_cells.geojson` with 0.2% spread. Used as a cross-check on the user's µm/px
  input, not to auto-fill it (`decisions.md` 011).

## py-lmd surface we use

- `Collection(calibration_points)` → `new_shape(points, well, name)` → `save(path)`;
  also `plot(save_name=...)` and `stats()`.
- `Collection` does **no** cut-path optimization. `lmd.lib` does export
  `tsp_greedy_solve(node_list, k=100, return_sorted=False)` (returns indices) and
  `tsp_hilbert_solve(data, p=3)`, but only the mask-based `SegmentationLoader` calls them.
  To order shapes we must call the solver ourselves before `new_shape`. Only the Hilbert
  solver is used; greedy needs `umap-learn` and is not offered (`decisions.md` 053).
- `SegmentationLoader`'s config is the field's vocabulary for shape processing —
  `shape_dilation`, `shape_erosion`, `binary_smoothing`, `convolution_smoothing`,
  `poly_compression_factor`, `path_optimization`, `hilbert_p`. It operates on label masks,
  so none of it applies to this app's vector path; shapely equivalents are ours to write.

## The pipeline, step by step

One page, top to bottom; each step gates on session state from the previous one. Steps 1–3
are shared, then the router dispatches to one of two workflows.

**Shared:** 1 upload + QC · 2 workflow choice · 3 calibration points.
**Legacy then continues:** 4 optional class split · 5 plate layout · 6 process and download.
**Cells then continues:** 4 class statistics and selection · 5 replicates, budgets and image
scale · 6 plate and capacity · 7 selection with preview · 8 export.
Both workflows reach a downloadable collection, and both share the same export parameters.

Step numbers are passed into the `ui_shared` step functions rather than hard-coded, because
the two workflows reach the shared steps at different points.

1. **Upload + QC** — `geojson.read_and_qc`, cached in the app by a thin wrapper.
   `geopandas.read_file`, then `set_crs(None, allow_override=True)`. Raises `GeojsonError`
   if the file is empty or has no `name`/`classification` column. Then, in order:
   - `Point` geometries with a non-empty `name` become the **calibration point pool**
     (`{name: [x, y]}`); all `Point`s are then dropped from the GeoDataFrame.
   - Rows with NaN `classification` (unclassified QuPath objects) are dropped, count
     recorded in the report. Note the demo files' 3 NaN-classification rows *are* the
     calibration Points, which have already left the frame — so both report 0 here.
   - `classification_name` column derived from `classification` (dict or its `str` repr).
   - `MultiPolygon`s are recorded in the report and dropped — py-lmd cuts one closed path
     per shape, so they have no meaning.
   Returns `(gdf, calibration_points, GeojsonReport)`. The report is rendered by the app,
   not by the library.
   **The app shows it as one table**, not a stack of warnings: `GeojsonReport.summary()`
   returns a frame — *In the file*, then one row per finding that actually applies — plus a
   "What happens" note per row (`decisions.md` 064). Rows with a zero count are omitted, so a
   clean file shows one line. The surviving count is **not** in the table; the `st.success`
   line under it carries that. The only warning box left at this step is unnamed points, which
   is about calibration rather than shapes.
   Invariant, asserted in `tests/test_geojson.py`: the rows whose note says "ignored" sum to
   `n_shapes_in_file - n_shapes_kept`, so every dropped shape is accounted for on screen.
1.1 **Calibration selection** — three `st.selectbox`es pick 3 names from the pool, order
   matters. `qc.triangle_qc` returns a `TriangleReport` with the calibration array, the
   triangle area, and the fraction of Polygons/LineStrings intersecting it;
   `is_concerning` below 25%, `is_degenerate` when the area is 0.
   **Two hard stops here** (`decisions.md` 031), the only blocking checks in the app besides
   an unreadable file: fewer than three calibration points in the file, and three points that
   do not form a triangle (a repeat, or all three collinear). Both make every downstream
   output meaningless, and **py-lmd writes a well-formed XML for a degenerate triangle
   without complaining** — verified — so nothing further would catch it.
1.2 **Optional class split** — `geojson.explode_classes` turns e.g. `T-Cell` into
   `T-Cell_001…`, one name per shape, for single-cell collection. Stores
   `original_classification_name` so repeated runs stay idempotent, and rewrites the
   nested `classification` dict via `geojson.rewrite_classification`.
2. **Plate layout** — plate type (384/96), margin, row step, column step feed
   `plate.acceptable_wells`. Two views: `plate.default_layout` (well names, allowed ones
   green) and `plate.sample_layout` (classes placed into allowed wells in **sorted** order,
   optionally randomized; returns the classes that did not fit).
   "Confirm and use this plate layout" → `plate.layout_to_saw` → `qc.validate_saw`.
2.2 **Plate rendering is shared.** `ui_shared.plate_settings_step` is the only place plate
   options live (type, margin, row/column spacing, randomize) and
   `ui_shared.plate_preview` is the only plate renderer, so both workflows show the same
   menu and the same table (`decisions.md` 045). The one remaining difference is
   deliberate: the annotations workflow keeps its **Confirm** button and custom
   samples-and-wells upload, because there the user maps classes to wells themselves; the
   cell workflow derives `class_r<replicate>` groups from the budgets and assigns them
   automatically, so there is nothing to confirm.
2.3 **Custom samples-and-wells upload** — overrides the generated layout.
   `plate.parse_saw_file` reads a `.txt`/`.json` containing a **Python dict literal** and
   `ast.literal_eval`s it (trailing commas fine, `//` comments not). Raises
   `SawParseError` with a specific reason. Sets `use_plate_wells = False`.
3. **Process** — `model.plan_from_class_wells` builds a `CollectionPlan`
   (`group_key = classification_name`, `well` mapped from the saw dict, unmatched shapes
   left with no well and reported), then `export.build_collection`:
   - `geometry.simplify(1)` then `geojson.extract_coordinates` (Polygon exterior, or
     LineString coords; anything else raises).
   - `Collection(calibration_points=plan.calibration_array)` with
     `ORIENTATION_TRANSFORM = [[1, 0], [0, -1]]` — **flips Y**, because QuPath image
     coordinates grow downward and the LMD stage does not.
   - One `new_shape` per selected row, in load order, into `plan.shapes["well"]`.
   - QC image is written to a fresh temp directory, not the working directory.
   Then `export.build_bundle` zips: `<stem>.xml`, `<stem>_<plate>_wellplate.csv`,
   `samples_and_wells.json`, `provenance.json`, `<stem>_processed.geojson` (sanitised for
   QuPath re-import), `collection.png`, and the session log.

**Extra #1** (below the main flow): generates a QuPath `classes.json` from two lists of
categoricals × replicate count, cycling 6 hard-coded colours as Java signed ints.

## Workflow routing and image scale (Phase 1)

- The router suggests a workflow from `objectType` counts: more cells/detections than
  annotations suggests the cell workflow, otherwise annotations. It is a **suggestion** —
  the radio is always user-changeable, and legacy is the default before any file is loaded.
  Verified on both demo files (`Single_cells.geojson` → cells, `TD_01…` → legacy).
- **The scale is estimated, not asked for, wherever the file allows it** (`decisions.md` 056).
  `ui_shared.resolve_pixel_size()` returns the value and its source: a typed override wins,
  otherwise the value derived from QuPath's own measurements when the file was read, otherwise
  nothing. The class table just shows areas without the user entering anything.
- `ui_shared.pixel_size_control()` is a compact input that sits **beside the area budget
  control**, not in a step of its own — that is the only thing the scale feeds, and users were
  confused about why it was being asked for (`decisions.md` 057). It reports where the value came
  from, warns when a typed value disagrees with the file by more than 5%, and warns when the
  implied scale varies between objects by more than `WIDE_SPREAD` (2%), which suggests a mixed or
  rescaled export.
- Beside the µm/px input sits a reference table from `stats.reference_pixel_sizes()`:
  objectives 4×–63× against two sensor pitches (3.45 and 6.5 µm), each cell being
  `pitch / magnification`. Its purpose is to make the **spread visible** — the 20× row alone
  varies 1.9× — because pixel size is a property of the whole optical path, not of the
  objective. The accompanying warning says so explicitly and points at QuPath's
  *Image → Image properties → Pixel width* as the real source.
- `geojson.area_measurements` pulls out only QuPath's area field. Building the whole
  `measurements_frame` to read one of ~100 columns cost 0.28 s and a 170 MB transient on every
  rerun; the narrow version is 0.16 s with no measurable allocation (`decisions.md` 050).
- `qc.pixel_size_qc` cross-checks the user's µm/px against `sqrt(Cell: Area / polygon area)`
  and warns above 5% disagreement. On `Single_cells.geojson` the implied value is
  0.3467 µm/px with 0.23% spread, so entering 3.467 is flagged as 10.00×. Files without area
  measurements are the normal case, so the check reports quietly that it could not run. The
  entered value is never overwritten (`decisions.md` 011, 029).
- `geojson.measurements_frame` explodes the `measurements` JSON into a DataFrame indexed
  like the input frame. Phase 2's per-class statistics will build on it.


## Statistics and plotting (Phase 2)

- `stats.class_statistics(gdf, pixel_size_um)` returns a numeric frame indexed by class:
  shape count, total area, median, standard deviation, Q1, Q3, min and max — all in µm².
  `stats.for_display` renames, orders and rounds the columns for the UI — areas to
  `stats.DECIMALS` (2) decimal places, shape counts left exact, because a count is not a
  measurement. Columns stay numeric so the table sorts correctly; the `%.2f` column format
  only trims what is shown. Standard deviation is `NaN` for a single-shape class, which is
  honest: one shape has no spread.
- **Areas come from `geometry.area × (µm/px)²`, never from QuPath `measurements`**
  (`decisions.md` 029). Cross-checked against `Cell: Area` on `Single_cells.geojson`:
  median ratio **0.9998**, 5–95% 0.9916–1.0079. So the geometry route is accurate and works
  on exports that carry no measurements at all.
- `plot.plot_shapes(gdf, included=..., calibration_array=...)` returns a matplotlib
  `Figure`. Classes not in `included` are drawn grey, so a user sees what they are leaving
  out. Above `plot.POLYGON_LIMIT` (20 000) shapes it draws one dot per shape instead of an
  outline. Colours are Okabe-Ito, assigned by sorted class name so a class keeps its colour
  across redraws. The y axis is inverted so the view matches QuPath's. The legend sits
  **outside** the axes (`figure.legend(loc="outside right upper")`, which needs the
  constrained layout the figure is built with) — a legend inside covers tissue.
- Built on `matplotlib.figure.Figure`, **not `pyplot`** — pyplot keeps every figure in a
  global registry and Streamlit reruns would leak them.
- Timings on the 14145-shape export: statistics 0.038 s, full polygon render 0.16 s.

## Budgets and feasibility (Phase 3)

- **Pixel size is optional** (`decisions.md` 038). Without it, `class_statistics` returns
  shape counts alone and only the *cells* budget mode is offered, with a caption saying why.
  Nothing expressible in cell counts is ever blocked.
- `budget.BudgetMode` is `CELLS` or `AREA`; `mode.stats_column` maps to `shapes` or
  `area_total_um2`, and `budget.feasibility` raises `KeyError` if that column is absent from a
  non-empty frame — which is how an area budget without a scale fails, loudly, in the library
  rather than silently in the UI. A class merely *missing* from the frame is not an error: it
  reads as zero available, which is what a class filtered away entirely looks like.
- `budget.feasibility` returns per class: replicates, per-replicate amount, total requested,
  available, shortfall, and **how many whole replicates the class can actually fill**. That
  last number is the actionable one. Pass `excluded=` and it also reports what the size filter
  took (see *Minimum collectable area*).
- Shortfalls **warn and continue** (003) — a user may knowingly accept a partly-filled
  replicate.
- The per-class editor defaults to **the whole class in a single replicate**, which is what
  the annotations workflow would do. Neutral, and not an invented number.
- `budget.total_groups` is the well count the plan needs, one well per replicate per class;
  step 7 compares it against the usable wells of the chosen plate and warns if it exceeds them.
- The `st.data_editor` key is an md5 of the sorted class selection plus the mode, so changing
  either gives a fresh editor instead of leaving stale rows behind.

## Minimum collectable area

- `stats.filter_by_minimum_area(gdf, floors, pixel_size_um)` drops shapes below their class's
  floor and returns the survivors plus the count excluded per class. Default
  `stats.DEFAULT_MINIMUM_AREA_UM2` is **100 µm²**, per class because different biologies differ
  in size (`decisions.md` 060).
- **Applied before anything is measured.** The order is: filter, then per-class statistics, then
  feasibility, then selection. So "available" in the feasibility table means *collectable*.
  Filtering afterwards would show the user an amount they cannot have.
- The selection draws from the filtered pool, not the whole frame. The plan is still built from
  the whole frame so filtered and unselected shapes stay reportable, with `replicate_of` reindexed
  onto it.
- Without a scale nothing is filtered, because a µm² floor cannot be evaluated — and the editor
  hides the column and says why.
- **What it removed is reported as two columns of the feasibility table**, not as its own message:
  `budget.feasibility(..., excluded=...)` adds `filtered_by_area` (count) and `filtered_share`
  (percentage of *that class's* shapes), shown as "Too small to collect" and "% too small"
  (`decisions.md` 065). A caption under the table gives the total.
- A class whose floor excludes every shape in it is absent from the statistics frame; `feasibility`
  treats that as zero available and 100% too small, rather than raising `KeyError`.
- On `Single_cells.geojson` a 100 µm² floor excludes 21 of 121 shapes and lifts the surviving
  median from 157 to 170 µm².
- Recorded per class in `provenance.json`.

## Selection engine (Phase 4)

- `selection.select(gdf, budgets, budget_mode, params, pixel_size_um)` returns a
  `SelectionResult`: `replicate_of` (replicate number per shape index, NA for unselected),
  an `achieved` frame per class and replicate, and `n_blocked_by_adjacency`.
- **Spread is implemented with a regular grid, not k-means** (`decisions.md` 040). Measured on
  4214 centroids: k-means costs 0.8 s at k=500 and **14 s at k=2000, 62 s at k=4000**, which
  is unusable inside a Streamlit rerun; the grid is ~0.03 s at any k and separates better
  (min pairwise gap 118 px vs 82 at k=100). `grid_bins` binary-searches the cell size until
  the occupied-cell count lands near the target.
- **One bin per shape in the whole class budget**, not per replicate (`decisions.md` 042).
  Every collected shape is then roughly a bin apart, and the shapes are dealt across
  replicates in a spatially shuffled order. The earlier design binned per replicate and took
  the *i*-th nearest to each bin centre, which put replicates 1–3 on top of each other:
  measured, 100% of collected shapes had their nearest collected neighbour in a *different*
  replicate, with a 12 px minimum gap. Now the median nearest-neighbour distance is 104 px,
  2.2× better than random, and replicate co-location is gone.
- **Replicates stay interleaved**: over 6 seeds the spread of replicate centroids is 5.6% of
  the class extent against a shuffled-label null of 5.8% — statistically indistinguishable
  from random assignment, which is what "interleaved rather than partitioned" means
  (`decisions.md` 015).
- **Filling is round-robin across replicates** as the stream is consumed, which serves both
  budget modes with one loop. Area budgets land just above target, overshooting under 5%.
- **Neighbours are judged by distance, not strict intersection** (`decisions.md` 044).
  QuPath's cell segmentation leaves a **sub-pixel gap** between adjacent cells: on the real
  8537-cell export the median boundary-to-boundary gap to the nearest neighbour is 0.57 px
  and only 4% of cells actually touch. So `predicate="intersects"` found 350 pairs where
  `dwithin` at 1 px finds 26 336, involving 8213 of 8537 shapes. The default
  `DEFAULT_NEIGHBOUR_DISTANCE_PX` is 1.0 and is user-adjustable; zero reverts to strict
  intersection and will report almost nothing. Cost 0.09 s at 1 px over 8537 shapes.
- **Adjacency is a strong preference, not a rule** (`decisions.md` 042). Conflicting
  candidates are deferred and used only once the non-conflicting ones run out, because a dense
  class cannot always fill a large budget without touching and under-delivering silently
  would be worse. The count of collected shapes touching another collected shape is **always
  reported**, per replicate, whether or not the preference is on — so the adjacency graph is
  built every time (0.04 s over 8537 shapes; that file has 350 touching pairs among 287 shapes).
- Adjacency spans replicates: the laser cuts a shared boundary regardless of which well
  either cell goes to. Verified on a 20-square touching chain whose largest non-touching set
  is 10: asking for 10 gives 0 conflicts, asking for 12 still delivers 12 and reports 8.
  On the real export, 900 of 3193 Tumor cells selects with 0 conflicts; 2700 of 3193 reports
  2206, which is genuinely unavoidable at 85% of a dense class.
- **Seed is exposed and recorded** in `provenance.json`, so a selection can be reported in a
  methods section and reproduced.
- **Start well** (`plate.wells_from`): filling begins at a chosen well instead of the first
  usable one, which is how several slides reach one plate (`decisions.md` 061). The plate caption
  reports the range used and names the well to start the next slide from. An unknown or unusable
  start well degrades to the beginning with a warning, so a typo cannot silently collect nothing.
- **The plate can be edited by hand**, opt-in behind a checkbox: `ui_shared.editable_plate` shows
  the plate as an `st.data_editor` with a dropdown of samples per well. Not drag-and-drop —
  Streamlit has none for grids and a real one would mean a custom frontend (`decisions.md` 055) —
  and a dropdown cannot produce a typo or name a sample that does not exist. It errors if a sample
  is dropped off the plate entirely and warns if two share a well.
- The cell workflow has **no confirm step**: the assignment is recomputed from the current
  plate settings on every rerun, so changing the plate, margin, spacing or randomize toggle
  takes effect immediately and the plate table under the options always shows what will be
  used. Verified: the same settings re-derive the same assignment, and a change to plate type,
  margin or randomize propagates into the plan's wells.
- **The well assignment is computed at the plate step, before the selection runs**
  (`decisions.md` 045). `budget.group_keys` derives the `class_r<replicate>` groups from the
  budgets alone, and `plate.assign_wells` maps them to wells — sorted, so the same plan always
  lands the same way, and seeded when randomized so a shuffled layout is still reproducible.
  `model.plan_from_selection` then takes that approved mapping rather than recomputing it, so
  a replicate that ends up with no shapes keeps its well instead of quietly vanishing.
  Groups beyond the available wells are reported, never silently dropped.

## Export parameters (Phase 5)

Both workflows expose the same two, in the shared export step.

- **Smoothing tolerance**, in pixels, default 1.0 — unchanged from what the app has always
  used (`decisions.md` 019). Measured on 900 real shapes: tolerance 0 gives 12 432 vertices,
  1.0 gives 7 938, 5.0 gives 4 586.
- **Cutting order** (`export.PathOrder`), default **`HILBERT`** — the option that minimises
  stage movement, not the one that preserves historical output (`decisions.md` 047), because
  stage movement between shapes is a leading cause of cutting misalignment.
  - `HILBERT` — grouped by well, path within each well shortened with py-lmd's
    `tsp_hilbert_solve` at order 7. **Default.**
  - `GROUPED` — all of a well's shapes together, wells visited in plate order, no shortening.
  - `NONE` — the order shapes were loaded in; what the app did before Phase 5.
- Measured on 900 shapes across 9 wells: load order needs **759 collector movements** and
  346 038 px of stage travel. Grouping gives 8 movements but *lengthens* travel to 392 877 px
  because it ignores position within a well. **Hilbert gives 213 295 px (62%)** with 8
  movements, and is the default.
- **What will not be cut is reported by cause, not by count** (`decisions.md` 048).
  `CollectionPlan.not_selected` is shapes with no group — deliberate in the cell workflow, a
  likely mistake in the annotations workflow, so the cell workflow states it in a caption and
  the annotations workflow warns. `CollectionPlan.unplaced` is shapes that *do* belong to a
  group whose group got no well, which always warrants a warning in either workflow.
  `plan_from_class_wells` assigns a `group_key` only to classes present in the
  samples-and-wells scheme, so both workflows classify exclusions the same way.
- Reordering is a pure permutation: **coordinates and well assignments are untouched**,
  asserted directly on both the order array and the XML.
- Effect on the golden cases when the default changed — cap runs collapse to the number of
  distinct wells in every case: `annotations` 2 002→1 215 px and 4→1 moves; `cells`
  30 755→7 815 px and 6→3; `multiclass_cells` 8 984→2 406 px and 11→2. `cells_exploded` is the
  exception at 30 755→31 066 px: with 124 wells for 128 shapes almost every shape has its own
  well, so travel is dominated by well order and there is nothing within a well to shorten.
  Its collector movements still drop 126→123.
- **py-lmd's `tsp_greedy_solve` is deliberately not offered** (`decisions.md` 053). It gave
  ~8% shorter travel than hilbert (197 563 px vs 213 295 px) but needs `umap-learn`, which it
  imports lazily at `lmd/segmentation.py:120`. That cost ~354 MB of numba JIT on first use
  against hilbert's 33 MB, ~15 s of cold start, and five extra packages reinstalled on every
  Community Cloud reboot. Removed entirely rather than hidden, so there is no code path whose
  dependency is absent — the Phase 5 harness asserts `PathOrder` has no `GREEDY` and that
  `umap` will not import.
- Hilbert cost by shape count at order 7: 0.06 s at 100, 0.33 s at 900, 0.93 s at 2 700,
  2.70 s at 8 000. Lower orders are faster but markedly worse above ~1 000 shapes.
- The solver prints progress to stdout, which is silenced in `_solve_within_well`, and a
  solver returning anything other than a permutation is logged and ignored rather than allowed
  to drop or duplicate shapes.

## Regions: the cellular-neighbourhood workflow

`regions.py` turns classified cells into **regions** — contiguous areas of tissue belonging to
one class — and `packing.py` fills those regions with circles. `ui_packing.py` is the workflow.

**Step order: 4 classes, 5 regions, 6 what-to-collect, 7 plate, 8 export.** The collection step
comes *before* the plate on purpose — how much tissue per replicate and how many replicates are
exactly what the plate has to accommodate, so deciding them first means the plate is shown once,
already correct, instead of being redrawn under the user while they tune circle settings
(`decisions.md` 068). Step 6 is where the user loops.

Step 6 offers a choice of what to cut out of each region: **circles packed inside them**, which
is the default and the point of the workflow, or **the whole regions**. Whole regions is the
only option when the file gives no image scale, because every packing amount is an area in µm².

**Steps 4, 5 and 6 are all `st.columns([1, 2])`** — numbers on the left, a picture of what they
produce on the right. Step 4 draws every shape in the file, coloured where the class is kept and
grey where it is not, which is the only place the app shows a user what they actually uploaded.
Step 5 draws the regions. Step 6's table is too wide for a third of the page, so its picture goes
directly below instead (`decisions.md` 071).

`class_selection_step` owns step 4's picture, so the cell workflow gets it too; `ui_cells`
no longer has its own `overview_step`.

**Step 6 is one table, then the picture.** Every number the user sets is a property of one
class, so they live in one `st.data_editor` row per class — replicates, µm² per replicate,
smallest and largest circle, and the gap — with only the image scale and the seed outside it.
The picture sits directly below, so the loop is change-a-number, look down, change again
(`decisions.md` 070). Those rows become `packing.ClassPacking`; `.as_budget()` converts to
`budget.ClassBudget` so `budget.group_keys` keeps owning the `class_rN` naming rule that decides
which well a group lands in.

No metrics row: "tissue in the regions / being collected / circles to cut / mean circle across"
sat between the settings and the picture and pushed the two apart, which is the one thing step 6
is arranged to avoid. Every figure it carried is in the per-replicate table below
(`decisions.md` 071).

**No `st.fragment` here**, unlike the cell workflow. A fragment only reruns itself, so nothing
below it re-executes — which would leave the plate and the export showing a stale collection,
the exact trap `decisions.md` 051 describes. With the collection step above the plate the
fragment has to go, and the caches on the projection and the packing are what keep a full rerun
affordable instead.

Defaults: **3 replicates** of **25 000 µm²** per class. Three is the smallest number that
supports a variance estimate, so it is what a DVP experiment is normally designed around, and
25 000 µm² is what Jose collects per well. On his core those defaults sit at the edge for the
smallest class — `Immune cells--Tumor` holds about 76 000 µm² of packable area against 75 000
asked for — so the shortfall warning fires on its third replicate out of the box. That is the
capacity estimate doing its job, not a failure.

### The pipeline, and why each part is the way it is

1. **Tessellate over every classified cell.** `shapely.voronoi_polygons(MultiPoint(centroids),
   ordered=True, extend_to=hull.envelope)`. `ordered=True` (shapely >= 2.1) guarantees the i-th
   polygon belongs to the i-th point, so class labels map by position — no spatial join and no
   `-1` unbounded-region bookkeeping. Verified 121/121 on `Single_cells.geojson`.
   `tests/test_regions.py::test_each_region_belongs_to_its_own_cell` is the guard: if that
   contract breaks, every region carries a neighbour's class and every well holds the wrong
   tissue.
2. **Classes are filtered *after* tessellating**, never before. `project(..., include=[...])`
   exists for exactly this. Dropping a class first would let its neighbours expand into the
   space it occupied, putting one class's tissue in another class's well.
3. **Cap each region** at a disc around its own cell, and **clip to the convex hull of the cell
   outlines**. The interface asks for the cap as a **distance in µm**, defaulting to three times
   `median_cell_spacing` — so "what bounds the projection?" is answered by the label rather than
   by a tooltip (`decisions.md` 071). An uncapped Voronoi cell on the rim of the tissue is
   unbounded, so a cut placed in it takes blank glass. The hull is of the cell **bodies**, not
   their centroids: a rim cell's centroid sits on the centroid hull, which would clip away half
   its own tissue. `RegionParams` still accepts `radius_factor` for callers that prefer to scale
   it, which the harness and the tests use.
4. **Merge by class**, `dissolve` then `explode`, one row per contiguous patch. Patches are then
   sorted by `(class, minx, miny)` — GEOS decides what order a union returns and that order
   picks the wells, so it is pinned rather than inherited.
5. **Deal patches across replicates**, largest first into whichever replicate holds the least
   area. No randomness; the same file reaches the same wells in any session.

Voronoi cells, discs and the hull are all convex, so a **region is always a single Polygon** —
MultiPolygons appear only at the merge, and the explode turns those into separate patches.

### The feedback picture

`plot.plot_regions_and_circles`. Two variables, so two channels that cannot be confused:
**a pale fill is the class**, for regions and circles alike, so a circle is visibly part of the
tissue it came from; **a dark outline is the replicate**.

Hue alone is not enough, and this was measured rather than guessed. With a full palette for each
channel, the tightest fill-outline pair across every combination has a WCAG contrast of **1.00** —
literally the same colour, which is why an orange circle of an orange class hid its own ring in
the first version. Scanning tint and shade factors against the contrast of every pair *and* the
RGB separation of every pair of outlines:

| fills | outlines | min contrast | min outline separation |
| --- | --- | --- | --- |
| Okabe-Ito as-is | tab20 | 1.00 | 0.098 |
| Okabe-Ito as-is | tab10 | 1.00 | 0.265 |
| tinted 0.55 | tab20 | 1.00 | 0.098 |
| **tinted 0.55** | **tab10 shaded 0.25** | **1.78** | **0.198** |
| tinted 0.6 | tab10 shaded 0.4 | 2.83 | 0.159 |

So `CLASS_FILL_TINT = 0.55`, `REPLICATE_SHADE = 0.25`, `REPLICATE_COLORMAP = "tab10"`. tab10
rather than tab20 because shading compresses a palette and tab20's twenty entries end up too
close to tell apart once darkened; ten replicates is already more than a plate makes sense for,
and it cycles beyond that. `class_colors` (the app-wide Okabe-Ito) stays the single source of
truth for a class's hue and `class_fill_colors` tints it, so a class looks like itself in every
picture. `replicate_colors` keys the palette by replicate *number*, not position, so replicate 2
keeps its colour when a class with fewer replicates appears.
`tests/test_plot.py` asserts both the contrast floor and the separation floor.

**Holes are drawn as holes**, via `polygon_paths`: `Path.make_compound_path` over the exterior
and every interior ring. Two things had to be right. Exterior-only drawing painted one region
over 207 000 µm² of the class it surrounds, which is what looked like a broken merge. And a
compound path only reads an interior as a hole if it **winds against** the exterior — GEOS makes
no promise which way a ring came out, and on this data the unoriented rings rendered *filled*.
`shapely.geometry.polygon.orient(polygon, sign=1.0)` fixes the winding.
`tests/test_plot.py::test_a_hole_in_a_region_is_not_painted_over` renders the figure and reads
the pixel in the hole, because nothing short of drawing it proves the orientation is right.

Regions draw at alpha 0.45 as a backdrop, circles at full opacity with a 1.5 pt ring; the two
layers separate by weight rather than by needing a third palette. Measured at 0.03 s for 684
regions plus 422 circles, so it redraws on every keystroke for free.

### Pinhole slivers

`CAP_QUAD_SEGS = 16` makes the radius cap a 64-sided polygon, while Voronoi edges are exact. So
where three capped regions meet they leave a gap of a pixel or two owned by no region, and
merging turns those into interior rings. `close_slivers` fills any interior ring smaller than
**the smallest region of the whole tessellation** — a real hole is another cell's territory, so
it cannot be smaller than that, which makes the threshold a property of the data rather than a
tuned constant. Measured:

| file | radius factor | smallest region | hole areas |
| --- | --- | --- | --- |
| `multiclass_cells` | 1.0 | 298 px² | 0.0, 1.0 — slivers |
| `multiclass_cells` | 0.5 / 2 / 3 | 149–361 px² | none |
| `Single_cells` | 1.0 | 702 px² | 0.0, 0.0, 0.0, 8.4 — slivers |
| `Single_cells` | 3.0 | 702 px² | 2635.5 — real |
| synthetic grid, one class enclosed | 3.0 | 48 px² | 100.0 — real |

The two groups do not overlap. The threshold is taken over **all** regions, not only the kept
ones, because a hole may be the region of a class the user left out and that class can hold
smaller regions than any kept one.

### The merge is verified, not assumed

Jose read the first version of the feedback picture as a broken merge — two classes appeared to
overlay each other in places. The merge was correct; the **plot** was wrong. Measured over the
684 regions of the real core:

| check | result |
| --- | --- |
| largest **area** shared by any two regions | **0.000000 µm²** |
| same-class region pairs that intersect at all | 18, all of them `Point` or `MultiPoint` |
| different-class pairs that intersect | 1 177, all of them `LineString`/`MultiLineString` |

So no two regions share any tissue, different-class regions meet along shared boundaries as they
must, and same-class regions only ever touch at isolated corners — which is what separate
components of a union legitimately do, not evidence of an unmerged pair.
`tests/test_regions.py::test_no_two_regions_share_any_tissue` holds that invariant.

### Two things Voronoi does that the user has to know about

Both are Jose's, and both are real:

- **Empty space inside the tissue** — a lumen, a vessel, a tear — is nearest to *some* cell, so a
  plain Voronoi hands it to that cell. The **radius cap is the mechanism that prevents it**: a gap
  wider than twice the cap is left unassigned. At the default factor of 3 on the real core the cap
  is 29 µm, so gaps wider than about 58 µm are correctly left blank, and lowering the factor
  leaves more of them blank. There is no way to distinguish "empty" from "sparse" from the
  centroids alone, so this is a dial rather than a decision the app can make.
- **The rim extends to infinity** — an outer Voronoi cell is unbounded. Handled by the same cap
  plus the clip to the convex hull of the cell outlines, so nothing reaches past the tissue.

### What the export path cannot represent

`geojson.extract_coordinates` returns `geometry.exterior.coords`, so **a hole is cut through**.
Where a region completely surrounds another class, that enclosed tissue lands in the same well if
whole regions are being collected. Circles are never packed into a hole — `patch.contains`
respects interior rings.

**This is not warned about**, at Jose's direction: on his real core 21 of 684 regions enclose
another class and in practice it does not affect a collection (`decisions.md` 068). The geometry
still does it, and `RegionReport` still counts `n_patches_with_holes` and `hole_area_px2` into the
log, so the figures are there if the judgement ever changes.

A region also **reaches past the cell outlines QuPath drew**. This is the dilation case
`decisions.md` 013 and ROADMAP round-one open question 3 both left open, with the commitment
that the app would say so on screen if it ever arrived. Step 5 explains it in prose rather than
warning, because it is how the workflow works rather than an anomaly.

### Synthesised shapes

Regions have no QuPath object behind them, but `sanitize_for_qupath` and the plan frame both
need `id`, `objectType` and `classification`. `geojson.synthesize_qupath_columns` adds them:
positional ids (`region-000001`), `objectType="annotation"`, and the `classification` value
QuPath wrote for that class, so colours survive the round trip. Called inside
`ui_packing._cached_projection`, so every later step already holds an exportable frame.

### Measured cost

| cells | voronoi + cap + clip | dissolve + explode |
| --- | --- | --- |
| 20 000 | 0.79 s | 0.65 s |
| 100 000 | hull alone 0.09 s | — |

`tissue_hull` uses `shapely.convex_hull` on a geometry collection rather than
`union_all().convex_hull`: identical answer, **0.09 s against 9.1 s** at 100 000 cells.

`scipy` was declared in `pyproject.toml` and imported nowhere until now; `regions.py` uses
`scipy.spatial.cKDTree` for nearest-neighbour spacing. No new dependency was added.

### Packing circles

Rejection sampling: throw a circle of random size at a random spot in a region, keep it if it
fits and is far enough from everything already placed, stop at the target area or after
`max_attempts` consecutive failures. Ported from `PY38_CirclePackingStreamlit/functions.py`
with the changes in `decisions.md` 067. Measured on the real 8 411-cell export at 0.6535 µm/px,
684 regions, 3 replicates of 10 000 µm² for each of 3 classes:

| | before | after |
| --- | --- | --- |
| wall time | 8.6 s | **0.7 s** |
| circles kept / placed | 479 / 718 | **422 / 431** |
| replicates reaching target | 6 of 9 | **9 of 9** |

The three fixes behind that, all of them found by running it rather than reading it:

1. **Regions that cannot hold one smallest circle are skipped.** `buffer(-min_radius).is_empty`
   is the exact test and costs 0.01 s for 141 regions. On real tissue 119 of 684 regions are in
   that state; each one otherwise burned the whole 2 000-attempt budget discovering it.
2. **Each region is asked for its share of what is still outstanding**, not for a fixed share
   plus a one-sided deficit. Every region overshoots by up to one circle, and with 141 regions
   in a class the one-sided version over-packed it by more than twice its target — which is
   where the 239 discarded circles came from.
3. **One circle of slack per replicate.** A replicate fills until it *reaches* the target, so
   packing exactly the total left the last replicate with only the remainder — reliably ~5%
   short while its siblings overshot. A replicate systematically smaller than its siblings is
   not a comparable measurement.

Spacing is enforced **across the whole collection**, not per class: regions of different classes
touch, so two circles either side of a boundary can otherwise end up a fraction of a micrometre
apart and the strip between them detaches into whichever well is cut first. Consequence for
reproducibility: classes are packed in sorted order and share one collision grid, so changing
one class's amount can change the classes sorted *after* it, never those before it. The
per-class random sub-streams (keyed by `blake2b` of the class name, never `hash()`, which Python
salts per process) keep the candidate *positions* stable regardless.

Collision detection is a dict of buckets keyed on `(x // cell, y // cell)` with
`cell = 2 * max_radius + spacing`, so only the 3x3 neighbourhood needs checking. The prototype
rebuilt a `shapely.STRtree` from every placed circle on every attempt — O(n² log n) over a run
— and queried it without a predicate, so it compared bounding boxes and rejected legal
placements.

### How much a region can actually hold

`packable_area` = `0.547 * area / (1 + spacing / (2 * mean_radius))²`. The 0.547 is measured:
randomly thrown circles of mixed size cover that share of a 2000x2000 px square with no gap.
The correction matters more than it looks — circles 100-500 µm² at 0.3467 µm/px:

| gap between circles | measured fill | formula |
| --- | --- | --- |
| 0 µm | 54.7% | 54.7% |
| 2 µm | 42.9% | 45.0% |
| 5 µm | 32.9% | 34.7% |
| 10 µm | 21.4% | 23.9% |
| 20 µm | 12.2% | 13.4% |

Within 2 points everywhere and slightly optimistic throughout. The prototype used a flat 0.55,
which at a 20 µm gap would promise a user 4.5x the tissue actually available.

### Smoothing takes area back off

`simplify` cuts the corners off a circle, and area per replicate is this workflow's whole
budget, so the loss is reported rather than absorbed. At the default 1 px tolerance a 64-sided
circle is reduced to:

| radius | vertices after | area lost |
| --- | --- | --- |
| 5-10 px | 9 | **10.0%** |
| 20-50 px | 17 | 2.6% |
| 100 px | 33 | 0.6% |

At 0.6535 µm/px a 100 µm² circle has a radius of 8.6 px, so the default circle range straddles
the worst of this: on the real export the loss is 6% of the collected area. `smoothing_loss`
computes it, and step 8 warns above 5% and states it as a caption below that. The remedies
offered are the real ones — a larger smallest circle, or a lower smoothing tolerance.

## Session state keys

Initialised in the block at the top of `streamlit_app.py`. Any new key belongs here too.

| Key | Holds |
| --- | --- |
| `session_id` | uuid4 string, shown to the user for bug reports, names the log in the zip |
| `log_file_path` | temp `.log` path; loguru sink, shipped inside the download zip |
| `workflow` | `'legacy'` \| `'cells'` \| `'regions'` — which workflow the router dispatched to |
| `pixel_size_um` | µm per pixel, entered by the user; `None` until they do |
| `selected_classes` | classes the cell workflow will collect; `None` means not chosen yet |
| `budget_mode` | `'cells'` \| `'area'` — what the per-replicate amount counts |
| `budgets` | list of `ClassBudget` as dicts: class, replicates, per-replicate amount |
| `minimum_area_um2` | per-class minimum collectable area in µm²; drives the pre-measurement filter |
| `region_params` | `RegionParams` as a dict: the radius cap that produced the current regions |
| `packing_params` | `PackingParams` as a dict: sizes, gap, effort and seed of the last packing |
| `region_budgets` | list of `ClassPacking` as dicts: per class, replicates, µm² per replicate, circle size range, gap |
| `view_mode` | `'default'` \| `'samples'` — which plate table is rendered |
| `gdf` | the working GeoDataFrame (points removed, `classification_name` added) |
| `geojson_report` | `GeojsonReport` from the last read, re-rendered on every rerun |
| `calibration_points` | `{name: [x, y]}` calibration-point pool from the geojson |
| `calibs` | `[name1, name2, name3]` selected calibration point names, order matters |
| `calib_array` | 3×2 numpy array of the selected points, passed to `py-lmd` |
| `saw` | samples-and-wells dict `{class_name: well}` |
| `use_plate_wells` | True if `saw` came from the plate builder, False if uploaded |
| `file_name` | uploaded geojson filename; change of name triggers reprocessing |
| `plate_df` | the displayed plate DataFrame |
| `plate_gen_params` | dict of plate/margin/step/randomize; change triggers regeneration |
| `show_saw_uploader` | whether the custom-saw uploader is visible |
| `zip_buffer`, `bundle_name` | the download bundle and its filename |
| `collection_image` | path to the QC image of the last processed collection |

## Domain constants and conventions

- 384-well plate = rows A–P (16) × columns 1–24. 96-well = rows A–H (8) × columns 1–12.
- Wells are strings like `"C3"`: row letter + column number, no zero padding.
- On the LMD7 with a 384-well plate, **rows A/B and columns 1/2 collect unreliably** —
  hence the margin control; margin 2 is the documented suggestion for 384.
- Row/column *step* leaves blank wells between samples for easier pipetting.
- Calibration points should sit **close to the annotations**; a wide triangle warps small
  shapes and you cut the wrong tissue. README has worked examples.
- `simplify(1)` tolerance is in image pixel units; it reduces vertex count for the LMD.

## Vocabulary

`GLOSSARY.md` is the reference; the rule is one word per thing (`decisions.md` 059).

- **shape** — one outline the laser will cut. The app's term everywhere, and py-lmd's too.
- **object** — QuPath's word. Used in code and docs where the distinction matters, because
  QuPath's own interface says annotation/cell/detection objects and its GeoJSON carries
  `objectType` — but **never in a message to the user** (`decisions.md` 063). Every count the app
  shows says "shapes", including counts of what was dropped while reading.
- **polygon** — the geometry type only, alongside `MultiPolygon` and `LineString`. Not a synonym
  for shape.
- **contour** — not used. It was a fourth name, in an image caption and the README.
- Renamed for consistency: `plot.POLYGON_LIMIT` → `plot.SHAPE_LIMIT` (it counts shapes), and
  `plate.plate_shape` → `plate.plate_dimensions` (a plate is not something the laser cuts).
- The upload summary reports `n_shapes_in_file` and the named calibration points, rather than a
  dump of `geom_type` counts — "14,145 shapes and 3 named calibration points", not
  "14145 Polygons, 3 Points". `geometry_counts` survives in the report for the log.
- **Unnamed points are reported**, not dropped silently: a point with no name cannot be chosen as
  a calibration point, and saying nothing left the user staring at "no calibration points" while
  looking at points they had just drawn.
- `tests/test_nomenclature.py` enforces this: it fails if "contour" reappears, if the old names
  come back, if a canonical term is missing from the glossary, if the glossary stops being linked,
  **or if any UI module shows the user the word "object" or a count of geometry types**.

## Conventions in the code

- **4-space indentation throughout.** The old 3-space style went out with `core.py` and
  `utils.py` in Phase 0; every remaining file is 4-space.
- **Library code does not call `st.*`.** `geojson.py`, `plate.py`, `qc.py`, `export.py` and
  `extras.py` return report objects or raise domain exceptions
  (`GeojsonError`, `SawParseError`); `streamlit_app.py` decides what to show and whether to
  stop. Only `mock_streamlit.py` mentions `st` at all, for notebook use.
- Every meaningful step logs via loguru, and the app surfaces the same information via
  `st.*`. The log file goes into the download zip, so log lines are part of the support story.
- **`st.number_input` rules, learned the hard way** (a value snapping back after entry):
  - Never pass `value=` on reruns to a widget that also has `key=`. With a key, the widget's
    own state is the source of truth; re-seeding `value` from `session_state` fights it.
    Pass `value=None` once and read the return value.
  - `step` is rendered as the HTML input's `step` attribute and **browsers snap entries to
    that grid**. A `step` coarser than `format` can display silently rounds typed input —
    `step=0.01` with `format="%.4f"` turns 0.3467 into 0.35. Keep them matched.
  - `value=None` makes the field start genuinely empty and return `None` until the user
    types, which is better than a `0.0` sentinel that has to be told apart from a real entry.
  - Pixel size is therefore `step=1e-4`, `format="%.4f"`, `min_value=1e-4` — see the
    `PIXEL_SIZE_*` constants in `ui_shared.py`. Values with more than 4 decimals are rounded.
- **How a number is shown, everywhere** (`decisions.md` 068, 072):
  - **Amounts in µm² are whole numbers with a thousands separator.** Areas run from tens to
    millions and a tenth of a square micrometre is far below anything the laser can place, so a
    decimal is noise in a number the user has to read. Inputs use `format="%d"` with integer
    bounds; tables go through `ui_shared.show_amounts`, which rounds the numeric columns, casts
    them to `Int64` and applies `NumberColumn(format="localized")` — an integer column cannot
    render a decimal, so one change satisfies both halves.
  - **A `NumberColumn` only ever goes on a numeric column.** Handing one to a text column makes
    Streamlit stamp every cell with a red "this value cannot be interpreted as a number"
    triangle, so a table of class names and amounts reads as broken when it is fine.
    `show_amounts` builds its config from `is_numeric_dtype`; `tests/test_ui_behaviour.py` pins
    it, because the symptom is invisible to every check except looking at the screen.
  - **One helper for all three workflows**, so a user switching between them meets the same
    number the same way. Audited by rendering every amount table in all three and reading back
    the dtypes and the column config.
  - **The unit is in the column header**, never left to be inferred. The cell workflow's
    feasibility and achieved tables are renamed at render time to carry `mode.unit`; the library
    keeps its own column names, so `tests/test_budget.py` still pins the contract rather than
    the rendering.
  - **Display spellings are `µm²`, `px²`, `µm/px`, `px`.** `um2`/`px2` appear only in
    identifiers. µm/px keeps 4 decimals and percentages keep 1 — those are small numbers where a
    separator means nothing and the decimal carries information. The rule is "no meaningless
    precision", not "no decimals anywhere".
- `ruff` configured in `pyproject.toml`: line-length 120, target py311, double quotes,
  google docstring convention, `E501` ignored.
- The test suite lives in `tests/` and is described below; `pytest` and `pytest-cov` are in the
  dev group.

## Known quirks and open issues

Not scope creep — recorded so nobody rediscovers them, and so a fix is a deliberate choice.

Still open:

- `demo_Qupath_project/Exemplar001.ome.tif - Image0.geojson` is Jose's real QuPath 0.7.0
  export, untracked (83.7 MB). It was re-exported on 2026-08-26 to add three named
  calibration points and measurements, so it now exercises the whole cell workflow. Its true
  scale, implied by `Cell: Area` over all 8537 classified cells, is **0.6535 µm/px** with
  0.66% spread — which is 6.5 µm ÷ 10×, a row of the reference table. Read time 1.4 s,
  triangle QC 0.01 s, statistics under 0.01 s. Do not assume the 0.3467 µm/px of
  `Single_cells.geojson` applies to it.
- **Nothing in the app requires QuPath `measurements`.** Areas are computed from the shape
  geometry and the user's µm/px, so a plain export without measurements is fully supported —
  which matters because QuPath only includes them if the user ticks the option, and a real
  14145-cell export had none. `qc.pixel_size_qc` is therefore **opportunistic**: it
  cross-checks µm/px only when area measurements happen to be present, and says so quietly
  (`st.caption`, not a warning) when they are not. Do not build anything that depends on
  measurements being there.

- **`py-lmd` accepts a degenerate calibration triangle silently.** Given three identical or
  collinear calibration points it writes a normal-looking XML with no error and no NaN, so
  the file looks fine and cuts in the wrong place. The app blocks this itself
  (`decisions.md` 031); do not assume py-lmd validates calibration geometry.
- **`py-lmd`'s `Collection.plot` calls `plt.show()`** (`lmd/lib.py:182`), so it blocks
  forever under a GUI matplotlib backend. A plain `python` run on macOS hangs unless
  `MPLBACKEND=Agg` is set; under Agg it merely warns "FigureCanvasAgg is non-interactive".
  Harmless in the deployed app and under `streamlit run` (no display, so Agg is chosen), but
  any headless script that builds a collection must set it — `tools/golden_harness.py` does.
- `pytest` is still a declared dependency with no tests (`decisions.md` 006/008).
- `ruff check .` reports **5 findings**, all in `mock_streamlit.py` (I001, D205, D212,
  2×W293) — down from 32 before Phase 0, because the files carrying the rest are gone.
  Pre-existing backlog, untouched by Phase 0 per `CLAUDE.md` rule 6.

Fixed in Phase 0 (`decisions.md` 021), kept here briefly so the history is legible:

- The `EPSG:4326` CRS mislabelling — `geojson.read_and_qc` now clears it, so `.area` and
  `.distance` work on pixels. This was blocking every area-based feature in Phases 2–4.
- Wells were validated against a hard-coded 384 grid, so `K5` passed on a 96-well plate.
  `qc.validate_saw` now takes the plate type.
- The plate CSV in the bundle was always named `_384_wellplate.csv`.
- The QC image was written to `./TheCollection.png` and Extra #1 wrote `./classes.json`,
  both into the working directory — a real problem on a shared server, where concurrent
  users overwrite each other. Both now go to a temp dir or straight to the download.
- Surplus classes beyond the available wells were dropped after a warning that did not say
  which ones; `plate.sample_layout` now returns them and the app names them.
- Class→well assignment iterated a `set`, so the layout changed between reruns. Now sorted.
- `parse_dictionary_from_file` returned `{}` on a parse error, and the app then reported
  "loaded and checked" for an empty dict. Now raises `SawParseError` with the reason.
- The MultiPolygon warning selected an `annotation_name` column that QuPath never writes,
  so that branch would have raised `KeyError` for any user who actually had a MultiPolygon.
  Verified against a synthetic file, then fixed.

## Scale, measured (Phase 6)

Profiled on Jose's real 83.7 MB / 14 145-feature export and on synthetic QuPath-shaped files.
The roadmap said to measure before designing; the measurements changed what needed doing.

| shapes | per widget change | on click | peak RSS |
| --- | --- | --- | --- |
| 8 537 (real export) | 0.49 s | 9.3 s | 690 MB |
| 50 000 | 0.64 s | 9.7 s | 710 MB |
| 150 000 | 1.94 s | 15.1 s | 940 MB |
| 1 000 000 (491 MB file) | 16.7 s | 53 s | **2 207 MB** |

Per-rerun figures are the *uncached* library cost; the app caches them, so a user pays them once
per change of input rather than once per widget touch.

**A million cells does not belong on the hosted app.** Community Cloud documents 690 MB guaranteed
and **2.7 GB maximum** per app, so 2 207 MB leaves little headroom, and before the Phase 6
optimisations it was 2 689 MB — over the line in practice. Reading the file alone costs ~1.4 GB.
Rough model: **~650 MB base + ~2 KB per shape**.

`ui_shared` warns above `HOSTED_COMFORTABLE_SHAPES` (40 000 — about the most a single TMA core
yields) with these figures and instructions for running locally (`decisions.md` 051).

- **Community Cloud specifics**, documented Feb 2024 and subject to change: CPU 0.078–2 cores,
  memory 690 MB minimum to 2.7 GB maximum, storage up to 50 GB. Apps **sleep after 12 hours**
  without traffic and anyone with view access can wake them. **Every dependency in
  `requirements.txt` is reinstalled on each reboot** — there is no documented wheel cache — so
  each package added lengthens every cold start.
- **The data is cheap; the imports are not.** Memory attribution: bare Python 14 MB, +geopandas
  and pandas 100 MB, +`lmd.lib` and matplotlib 243 MB, +the whole 83.7 MB GeoJSON ~320 MB. The
  frame itself is only 39 MB. So most of the footprint is libraries, which is why removing
  `umap-learn` (`decisions.md` 053) mattered more than anything done to the data: the real
  export now peaks at **392 MB**, down from 767 MB before Phase 6.
- **What runs on every rerun** was the thing to fix, since Streamlit re-executes the whole script
  on every widget change: `selection.select` (1.40 s at 150 000 shapes) and `pixel_size_qc`
  (0.28 s on the real file). Both are now cached in the UI layer, along with `class_statistics`,
  which was being computed twice per rerun.
- `plot_shapes` scales fine — 0.11 s at 150 000 — because it switches to centroids above
  `POLYGON_LIMIT`. That earlier decision paid off.
- Cache keys are explicit tuples, never the frame: hashing 150 000 rows would cost what the cache
  saves. `_shape_fingerprint` is `(file name, row count, sorted class names)` — the class names
  matter because exploding a class rewrites them in place, so a filename alone would serve a
  stale selection.
- **Columns nothing reads are dropped at load** (`geojson.UNUSED_COLUMNS`): `measurements`,
  `name`, `isLocked`. `measurements` is consumed once during the read to derive the implied pixel
  size, which then lives in the report — so `qc.compare_pixel_size` is pure arithmetic and costs
  nothing per rerun, down from 0.28 s and a 170 MB transient. At a million shapes the drop frees
  107 MB of a 383 MB frame.
- **The plan builders copy only the columns a plan needs** (`model.PLAN_SOURCE_COLUMNS`), not the
  whole frame. A full copy cost 99 MB at a million shapes.
- **Step 8 is an `st.fragment`**, so changing the selection mode, seed or neighbour distance
  re-runs only steps 8–9 rather than the whole script. The export lives inside the fragment, so
  nothing downstream can be left showing a stale selection. Note Streamlit forbids combining
  `@st.fragment` and caching on the *same* function — the caches here wrap different functions.
- Together these took the million-shape peak from 2 689 MB to **2 207 MB**, and
  `build_collection` from 7.6 s to 1.1 s (the latter mostly by defaulting to hilbert).

## Test suite

`tests/`, run with `uv run pytest` — 231 tests in about 7 seconds. `-m "not slow"` skips the
golden gate for a fast loop. CI runs ruff, the suite and the harness on every push and PR
(`.github/workflows/ci.yml`).

- **No environment setup needed**: `conftest.py` forces `MPLBACKEND=Agg` before anything imports
  pyplot, because py-lmd's `Collection.plot` calls `plt.show()` and hangs under a GUI backend.
  It also silences loguru so assertions are not buried in log lines.
- **Only committed files and generated fixtures.** The real 83.7 MB export is deliberately not a
  dependency, so CI runs from the repo alone. `synthetic_cells` builds QuPath-shaped exports of
  any size on demand.
- Two fixtures encode quirks that no committed file shows: `touching_chain` (20 squares in a row,
  largest non-touching set exactly 10, so the adjacency preference can be checked against a known
  optimum) and `near_touching_chain` (0.5 px gaps, reproducing the sub-pixel separation real
  QuPath segmentation leaves between adjacent cells).
- **Assertions say what breaks in the app**, not just which value differed. The golden test
  extracts the harness's DIFFER lines and explains re-blessing, rather than dumping subprocess
  output.
- Statistical properties are asserted **against baselines**, never absolute thresholds: spread
  quality against a random draw, replicate interleaving against shuffled labels averaged over
  seeds. A single draw compared against its own tail fails by chance, which was learned in
  Phase 4.
- Deliberately avoided: asserting that a package is absent by importing it. A stale virtualenv
  still has umap-learn after it is undeclared, so that test passed in CI and failed locally. The
  suite checks the declared dependencies instead.

## Regression harness

`tools/golden_harness.py`, with the reference output in `tools/golden/` (14 files, ~255 KB).

```
uv run python tools/golden_harness.py check      # compare against the golden files
uv run python tools/golden_harness.py capture    # re-bless, only when output should change
```

Seven cases, each covering a path where a change could silently move coordinates:
`annotations` (ordinary mini-bulk), `cells` (128 shapes with measurements),
`cells_exploded` (one well per shape), `annotations_96` (different plate geometry),
`multiclass_cells` (real QuPath 0.7.0 export shape), `regions` (Voronoi projection and merge,
where every coordinate is computed rather than read from the file), `packing` (circles placed by
a seeded random walk). Each produces an XML and a CSV, so 14 artefacts.

- `capture` rewrites **every** case, not only a new one, so after adding a case check
  `git diff tools/golden/` shows nothing but the new files before committing.
- `regions` and `packing` are the cases whose bytes depend on **GEOS** as well as on this repo:
  merging a class's regions is a union, and a GEOS upgrade can legitimately reorder the vertices
  it returns. If only those differ after a dependency bump, that is what happened — check the
  geometry is equivalent before re-blessing.
- `packing` additionally pins **numpy's random stream**. If it differs on its own, a recorded
  seed no longer reproduces its collection, which is breaking for anyone who wrote a seed into
  a methods section.

- The committed golden files are **byte-identical to output captured from the pre-Phase-0
  code**, so the reference traces back to the version that had been in production.
- Verified to fail as well as pass: replacing `export.ORIENTATION_TRANSFORM` with the
  identity matrix (a broken Y flip) makes every XML comparison differ and `check` exit 1.
- It sets `MPLBACKEND=Agg` itself, so it needs no special invocation.
- What it does **not** cover: the UI, the QC/warning behaviour, LineString geometries (no
  demo file has one), and any input outside the four cases. It also proves "unchanged", not
  "correct" — a pre-existing coordinate bug would be faithfully preserved.

`CLAUDE.md` rule 6 makes running it mandatory before committing anything that touches
geometry, calibration, well assignment or export.
