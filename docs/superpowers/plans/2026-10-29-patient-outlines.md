# Outlines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user tick classes as *outlines*, so every shape inside a `P1` outline becomes class `P1_<class>` before any collection method sees it, and outlines themselves are never cut.

**Architecture:** A pure library module, `outlines.py`, relabels one slide's frame and returns a report. At the top of Stage 2, `ui_samples.outline_step` applies it to every slide and returns a new `SlidesContext`. The router passes that context on to Stages 2–4, so the methods, plate and export are unchanged.

**Tech Stack:** Python, geopandas/shapely 2 (`sjoin`, `representative_point`), Streamlit, pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-patient-outlines-design.md`

## Global Constraints

- Follow `CLAUDE.md`. Library modules never import `streamlit`. Match each file's indentation (the `ui_*` and library files are 4-space).
- Separator `OUTLINE_SEPARATOR = "_"`. Grouped class = `f"{outline}{OUTLINE_SEPARATOR}{class}"`, outline first.
- Membership is decided by `representative_point()` `within` an outline. "Several outlines" means outlines of **two or more distinct classes**. Two overlapping outlines of the same class are not ambiguous.
- With no outline class ticked, Stage 2 receives **the same `SlidesContext` object**, and `uv run python tools/golden_harness.py check` passes **without re-blessing**.
- Leftovers: `st.warning`, never `st.error` or `st.stop()`.
- `outlines.py` must **not** write `original_classification_name`, because `geojson.explode_classes` uses it to stay idempotent.
- Every assertion message says what breaks in the app.
- Before each commit:
  - `uv run ruff check <touched files>` is clean
  - `uv run pytest -m "not slow"` passes
  - before the last commit: full `uv run pytest` and the golden check
- Commit with `git commit --only <paths>`. The trailer is `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`. Never push.

## Review Focus

1. **A slide with none of the ticked outline classes:** every shape on it is left out as "no outline", and the UI names that slide in its own warning. *(Task 1: `test_slide_without_outlines_leaves_everything_out`; Task 3: `test_slide_without_outlines_is_named`)*
2. **Nested outlines of different classes,** e.g. a core outline inside a whole-tissue outline: the shapes count as "several outlines", not silently given to one of them. *(Task 1: `test_nested_outlines_of_different_classes_are_ambiguous`)*
3. **A multi-class structure inside an outline:** the classification is written as `{"name": "P1_Immune cells--Tumor", …}` with no leftover `names` list, so QuPath reopens one class. *(Task 1: `test_multiclass_structure_is_written_as_one_class`)*
4. **"Every shape its own well" after grouping:** it numbers `P1_gf_001`, not nothing. *(Task 1: `test_explode_after_grouping_numbers_the_grouped_class`)*
5. **An outline holding no shapes** (drawn in the wrong place, or a line rather than an area): it is counted, and the UI says so. *(Task 1: `test_empty_outlines_are_counted`; Task 3: `test_empty_outline_is_mentioned`)*

---

### Task 1: `outlines.py`, the library

**Files:**
- Create: `src/qupath_to_lmd/outlines.py`
- Test: `tests/test_outlines.py`

**Interfaces:**
- Consumes: `model.CLASS_NAME`. Frames as `geojson.read_and_qc` returns them: columns include `classification` (dict or its `str`), `CLASS_NAME`, `geometry`.
- Produces:
  - `OUTLINE_SEPARATOR: str = "_"`
  - `NO_OUTLINE = "no outline"`, `SEVERAL_OUTLINES = "several outlines"`
  - `@dataclass OutlineReport`, with:
    - `outlines: GeoDataFrame` (columns `label`, `geometry`)
    - `left_out: GeoDataFrame` (columns `CLASS_NAME`, `reason`, `geometry`)
    - `n_grouped: int = 0`
    - `repeated_outlines: dict[str, int]`, outline class → count, only where > 1
    - `empty_outlines: dict[str, int]`, outline class → how many of its outlines hold no shape
    - `n_multipolygon_outlines: int = 0`
    - `combinations: dict[str, set[tuple[str, str]]]`, grouped name → {(outline, class)}
    - property `n_outlines -> int`
  - `group_by_outlines(gdf, outline_classes: Collection[str], multipolygons: GeoDataFrame | None = None) -> tuple[GeoDataFrame, OutlineReport]`
  - `left_out_table(reports: dict[str, OutlineReport]) -> pandas.DataFrame`, one row per (slide, class), with columns `slide`, `class`, `no outline`, `several outlines`. Integer counts; only rows with a nonzero count.
  - `colliding_names(reports: dict[str, OutlineReport]) -> dict[str, list[tuple[str, str]]]`, across all slides; only names with ≥2 distinct pairs; pairs sorted.
  - `provenance(classes: list[str], reports: dict[str, OutlineReport]) -> dict`, giving `{"classes": sorted(classes), "slides": {slide: {"outlines", "shapes_grouped", "left_out_no_outline", "left_out_several_outlines"}}}` with int values.

- [ ] **Step 1: Write the failing tests**

Helper `_frame(features)` in the test file: build a GeoDataFrame from `(class_name, shapely_geometry)` pairs, with `classification = str({"name": c, "color": [255, 0, 0]})` and `CLASS_NAME = c`. Box helpers: `P1 = box(0, 0, 100, 100)`, `P2 = box(200, 0, 300, 100)`.

```python
def test_shape_inside_one_outline_is_renamed():
    grouped, report = outlines.group_by_outlines(_frame([("P1", P1), ("gf", box(10, 10, 20, 20))]), ["P1"])
    assert list(grouped[CLASS_NAME]) == ["P1_gf"], "A gf shape inside the P1 outline must become sample P1_gf, or patients mix in one well."
    assert "P1" not in set(grouped[CLASS_NAME]), "An outline reached the frame the methods collect from — it would be cut."
    assert ast.literal_eval(grouped["classification"].iloc[0])["name"] == "P1_gf", "The processed GeoJSON would reopen in QuPath under the old class."

def test_accounting_every_shape_is_somewhere(): ...
    # Frame: P1, P2 outlines; gf in P1; yf in P2; gf at (500, 500) outside both; gf in an overlapping P3 box over P1.
    # assert report.n_outlines + report.n_grouped + len(report.left_out) == len(frame)
def test_shape_outside_every_outline_is_left_out(): ...        # reason == NO_OUTLINE, class kept as "gf"
def test_nested_outlines_of_different_classes_are_ambiguous(): ...  # core "C1" inside "P1": reason == SEVERAL_OUTLINES
def test_overlapping_outlines_of_one_class_are_not_ambiguous(): ... # two overlapping P1 boxes → P1_gf; repeated_outlines == {"P1": 2}
def test_shape_crossing_the_edge_is_assigned_by_its_interior_point(): ...  # box(90, 10, 105, 20) in P1 → P1_gf
def test_multipolygon_outline_groups_both_pieces(): ...
    # multipolygons = GeoDataFrame({CLASS_NAME: ["P1"], "name": [None]}, geometry=[MultiPolygon([P1, P2])])
    # gf in each piece → both P1_gf; report.n_multipolygon_outlines == 1
def test_slide_without_outlines_leaves_everything_out(): ...    # outline_classes=["P1"], frame has only gf/yf → all NO_OUTLINE, n_grouped == 0
def test_empty_outlines_are_counted(): ...                      # P1 holds gf, P2 holds nothing → empty_outlines == {"P2": 1}
def test_multiclass_structure_is_written_as_one_class(): ...
    # classification str({"names": ["Immune cells", "Tumor"], "color": [...]}), CLASS_NAME "Immune cells--Tumor"
    # → dict has name "P1_Immune cells--Tumor" and no "names" key
def test_colliding_names_are_found_across_slides(): ...
    # slide A: P1 outline + "a_b" shape; slide B: "P1_a" outline + "b" shape
    # → colliding_names({"A": ra, "B": rb}) == {"P1_a_b": [("P1", "a_b"), ("P1_a", "b")]}
def test_no_outline_classes_returns_the_frame_untouched(): ...  # `grouped is frame` is not required; assert_frame_equal and n_outlines == 0
def test_explode_after_grouping_numbers_the_grouped_class(): ...
    # geojson.explode_classes(grouped, ["P1_gf"]) → CLASS_NAME starts with "P1_gf_"
def test_left_out_table_and_provenance_count_the_same_shapes(): ...
    # sum of table counts == sum of provenance left_out_* == len(left_out) over two slides
def test_grouped_class_from_two_slides_is_one_sample(): ...
    # group two frames that each have P1 + gf; slides.whole_shape_samples({"A": ga, "B": gb}, ["P1_gf"], {"A": None, "B": None})
    # → samples == ["P1_gf"], and both slides' shapes carry group_key "P1_gf"
    # message: "P1's tissue from two images would land in two wells instead of being pooled."
```

The bodies marked `...` follow the first test's pattern. The comments give the inputs and the exact assertions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_outlines.py -q`
Expected: collection error, `ImportError: cannot import name 'outlines'`.

- [ ] **Step 3: Implement `src/qupath_to_lmd/outlines.py`**

The module docstring explains the domain reason: patients share an image, and class names alone would pool them.

`group_by_outlines`:
1. If `outline_classes` is empty, return `(gdf, OutlineReport(empty frames))`.
2. `is_outline = gdf[CLASS_NAME].isin(outline_classes)`. Outlines = those rows plus `multipolygons` rows of those classes (`n_multipolygon_outlines` = how many of the latter). Build `outlines` with `label = CLASS_NAME`.
3. `points = gdf.loc[~is_outline].geometry.representative_point()`, then `geopandas.sjoin(points_gdf, outlines, predicate="within", how="left")`.
4. Count distinct `label` per original index: 0 → `NO_OUTLINE`, 1 → that label, ≥2 → `SEVERAL_OUTLINES`.
5. For assigned rows, set `CLASS_NAME` to the grouped name, and rewrite `classification` with a private `_grouped_classification(value, name) -> str`. It parses a `str` with `ast.literal_eval` (as `geojson._tidy_classification` does), pops `"names"`, sets `"name"`, and returns `str(dict)`.
6. `empty_outlines`: per outline row, whether any point fell in it (from the sjoin's `index_right`), counted by label.
7. Return the frame of assigned rows only, as a copy with its original index.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_outlines.py -q`. Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/qupath_to_lmd/outlines.py tests/test_outlines.py
uv run pytest -m "not slow" -q
git add src/qupath_to_lmd/outlines.py tests/test_outlines.py
git commit --only src/qupath_to_lmd/outlines.py tests/test_outlines.py -m "Group shapes by the outline they sit in" -m "<why + trailer>"
```

---

### Task 2: Keep MultiPolygon geometry when reading

**Files:**
- Modify: `src/qupath_to_lmd/geojson.py:184-190` (and the `multipolygons` field's type at `:46`)
- Test: `tests/test_geojson.py`, `tests/test_outlines.py`

**Interfaces:**
- Produces: `GeojsonReport.multipolygons: geopandas.GeoDataFrame | None`, with columns `name` (where present), `CLASS_NAME` and `geometry`. `n_multipolygons_dropped` is unchanged.
- Consumes: `outlines.group_by_outlines(..., multipolygons=report.multipolygons)` from Task 1.

- [ ] **Step 1: Write the failing tests**

- In `tests/test_geojson.py`, extend `test_multipolygons_are_reported_and_dropped`: `assert set(report.multipolygons.geom_type) == {"MultiPolygon"}`, with the message "An outline merged from two tissue pieces in QuPath would have no geometry to group shapes with."
- In `tests/test_outlines.py`, add `test_multipolygon_outline_read_from_a_file_groups_shapes`. It writes a FeatureCollection to `tmp_path` with three named calibration Points, a `P1` MultiPolygon of two boxes and a `gf` Polygon in each box. It runs `geojson.read_and_qc`, then `group_by_outlines(gdf, ["P1"], report.multipolygons)`, and asserts both shapes are `P1_gf`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_geojson.py::test_multipolygons_are_reported_and_dropped tests/test_outlines.py::test_multipolygon_outline_read_from_a_file_groups_shapes -q`. Expected: FAIL (a plain DataFrame, no geometry).

- [ ] **Step 3: Implement**

Store `geopandas.GeoDataFrame(gdf.loc[is_multi, columns + ["geometry"]], geometry="geometry")`. Add a one-line comment on the field: the geometry is kept so an outline can be a MultiPolygon. Nothing that is cut changes.

- [ ] **Step 4: Run the tests and the golden check**

Run: `uv run pytest -m "not slow" -q && uv run python tools/golden_harness.py check`. Expected: pass, golden unchanged.

- [ ] **Step 5: Lint and commit**

Commit `src/qupath_to_lmd/geojson.py tests/test_geojson.py tests/test_outlines.py` with the subject "Keep MultiPolygon geometry so it can serve as an outline".

---

### Task 3: The outline step in Stage 2

**Files:**
- Modify: `src/qupath_to_lmd/ui_slides.py:28-33` (a new field)
- Modify: `src/qupath_to_lmd/ui_samples.py` (new `outline_step`, `render` at `:281-287`)
- Modify: `streamlit_app.py`: `DEFAULTS` (`:15-48`) and `collect()` (`:94`)
- Modify: `facts.md`: the module table (`:61-72` area), *The pipeline, step by step*, the session-key table, *Several slides*
- Test: `tests/test_ui_behaviour.py`

**Interfaces:**
- Consumes: everything in Task 1. `Slide.report.multipolygons` from Task 2.
- Produces:
  - `SlidesContext.outlines: dict[str, outlines.OutlineReport] = field(default_factory=dict)`
  - `ui_samples.outline_step(context: SlidesContext) -> SlidesContext`
  - `ui_samples.render(context) -> tuple[SlidesContext, SampleSet | None]`
  - session key `outline_classes` (default `None`)

- [ ] **Step 1: Write the failing tests**

In `tests/test_ui_behaviour.py` (add `ANNOTATIONS_FILE` to its `tests.conftest` import), monkeypatch `streamlit.multiselect` to return a chosen list. Build slides by writing GeoJSONs with outlines to `tmp_path` and loading them through `slides.read_slides`. The calibration Points are required, so reuse the shape of Task 2's file writer.

```python
def test_no_outline_classes_leaves_the_context_alone(fake_streamlit, monkeypatch):
    context = _context(_load(fake_streamlit, ANNOTATIONS_FILE))
    monkeypatch.setattr(streamlit, "multiselect", lambda *a, **k: [])
    assert ui_samples.outline_step(context) is context, "With nothing ticked, the frozen whole-shapes output could change."

def test_left_out_shapes_warn_and_do_not_stop(...): ...
    # one gf outside P1 → `"will not be collected" in fake_streamlit.shown("warnings")`, no errors, no Stopped
def test_slide_without_outlines_is_named(...): ...   # two slides, only A has P1 → a warning containing slide B's name
def test_empty_outline_is_mentioned(...): ...        # P2 holds nothing → `"hold no shapes" in fake_streamlit.shown("infos")`
def test_grouped_context_carries_grouped_classes(...): ...
    # outline_step(context).frames[name][CLASS_NAME] == {"P1_gf"}; .outlines has one report per slide
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_ui_behaviour.py -k outline -q`. Expected: `AttributeError: module 'qupath_to_lmd.ui_samples' has no attribute 'outline_step'`.

- [ ] **Step 3: Implement**

- `SlidesContext.outlines` field, imported from `outlines`.
- `outline_step`:
  - Options are the sorted union of `CLASS_NAME` over `context.frames` and over every `slide.report.multipolygons`.
  - Remember and write back `st.session_state.outline_classes` the way `class_step` handles `selected_classes`: filter to options, then `st.multiselect(default=…)`. No widget `key`, so a stale value cannot raise.
  - Empty → `return context`.
  - Otherwise, per slide, call a cached `_grouped(_gdf, _multipolygons, cache_key, classes)` decorated with `@st.cache_data(show_spinner=False)`, keyed on `(context.fingerprint(slide.name), tuple(sorted(classes)))`. Return `dataclasses.replace(context, slides=[dataclasses.replace(slide, gdf=grouped)…], outlines=reports)`.
- Copy (exact):
  - multiselect label `"Classes that outline a group of shapes"`
  - help `"Optional. Shapes of these classes are never cut. Every other shape takes the name of the outline it sits in — gf inside a P1 outline becomes P1_gf — so samples keep patients, or any other group, apart, also across slides."`
  - `st.success(f"{n_outlines} outlines grouped {n_grouped:,} shapes into {n_classes} classes.")`
  - if `left_out_table` is non-empty: `st.warning(f"{n:,} shapes will not be collected: they sit in no outline, or inside outlines of two different classes at once. Extend or redraw the outline in QuPath, or remove the overlap, and export again.")`, then the table via `ui_shared.show_amounts` or `st.dataframe`. Drop the `slide` column when there is one slide.
  - per slide with no outline at all: `st.warning(f"No outline on {slide}: none of its shapes can be collected.")`
  - per repeated outline: `st.info(f"{count} separate {label} outlines on {slide}: shapes in all of them go to {label}_….")`
  - per empty outline class: `st.info(f"{count} {label} outline(s) on {slide} hold no shapes.")`
  - per collision: `st.warning(f"{name} is made by both " + " and ".join(f"{c} inside {o}" for o, c in pairs) + ": they would share a well. Rename one class in QuPath.")`
  - if any MultiPolygon outlines: `st.caption(f"{n} outline(s) made of several pieces are used here although step 1 listed them as ignored: they cannot be cut, but they can hold shapes.")`
- `render`: call `context = outline_step(context)` after the pooling note and before `method_step`. If a sample set comes back and `context.outlines` is non-empty, replace its `params` with `{**params, "outlines": outlines.provenance(classes, context.outlines)}`. Return `(context, sample_set)`.
- `streamlit_app.py`:
  - add `"outline_classes": None` next to `"selected_classes"`
  - in `collect()`: `context, sample_set = ui_samples.render(context)`, so Stages 3–4 receive the grouped context
- `facts.md`:
  - module row `outlines.py`
  - a Stage 2 paragraph (where the step runs, what it returns, the interior-point rule, distinct-class ambiguity, leftovers warned about)
  - `outline_classes` in the key table
  - `SlidesContext.outlines`
  - one line in *Several slides*: with outlines, pooling is by grouped class

- [ ] **Step 4: Run the tests and the app tests**

Run: `uv run pytest -m "not slow" -q`. Expected: all pass, including `tests/test_app.py` through the changed router.

- [ ] **Step 5: Lint and commit**

Commit `src/qupath_to_lmd/ui_slides.py src/qupath_to_lmd/ui_samples.py streamlit_app.py facts.md tests/test_ui_behaviour.py` with the subject "Offer outline classes at the top of Stage 2".

---

### Task 4: Outlines and leftovers in the classes picture

**Files:**
- Modify: `src/qupath_to_lmd/plot.py:110-196` (`plot_shapes`)
- Modify: `src/qupath_to_lmd/ui_samples.py:222-248` (`_draw_input` and its two callers in `class_step`)
- Test: `tests/test_plot.py` (exists; add beside the existing `plot_shapes` tests)

**Interfaces:**
- Consumes: `OutlineReport.outlines` and `.left_out` (Task 1); `context.outlines` (Task 3).
- Produces: `plot_shapes(..., outlines: GeoDataFrame | None = None, left_out: GeoDataFrame | None = None)`

- [ ] **Step 1: Write the failing test**

```python
def test_outlines_are_labelled_and_leftovers_in_the_legend():
    figure = plot.plot_shapes(grouped, outlines=report.outlines, left_out=report.left_out)
    axes = figure.axes[0]
    assert "P1" in {t.get_text() for t in axes.texts}, "The user cannot see which outline is which patient."
    legend = " ".join(t.get_text() for t in figure.legends[0].get_texts())
    assert "left out (1)" in legend, "Shapes that will not be collected are invisible in the picture."
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_plot.py -k outlines -q`. Expected: `TypeError: unexpected keyword argument 'outlines'`.

- [ ] **Step 3: Implement**

- Leftovers are drawn first, in `MUTED`, through `_draw`, with a legend handle `f"left out ({len(left_out)})"`.
- Outlines are drawn as `boundary` lines in `#444444`, linewidth 1, `zorder=3`, with `axes.annotate(label)` at each outline's `representative_point()`.
- `_draw_input` takes `report: OutlineReport | None`. It passes both frames, and adds `sorted(report.outlines["label"])` and `len(report.left_out)` to its `picture_key`. When there is a report it appends to the caption: "Outlines are drawn as dark borders with their class; grey shapes outside them will not be collected."
- In `class_step`, pass `context.outlines.get(slide.name)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -m "not slow" -q`. Expected: pass.

- [ ] **Step 5: Lint and commit**

Commit `src/qupath_to_lmd/plot.py src/qupath_to_lmd/ui_samples.py tests/test_plot.py` with the subject "Draw outlines and left-out shapes in the classes picture".

---

### Task 5: Documentation, full verification, manual test

**Files:**
- Modify: `GLOSSARY.md`, `decisions.md` (append 091, 092, 093), `README.md`
- Create (scratchpad, **not committed**): `make_outlines_fixture.py`, which writes `TD_01_outlines.geojson`

- [ ] **Step 1: Documentation**

- `GLOSSARY.md`:
  - **outline** (under *Grouping and collection*): a shape whose class names a group, never cut, giving its class as a prefix to every shape inside it.
  - Add a row to the **group** table: "any method, with outlines → outline + class, e.g. `P1_Tumor_r2`".
  - Amend **sample**: with outlines, slides pool by grouped class.
- `decisions.md`:
  - **091**: outlines group shapes. Label from the class. Opt-in. Rejected B (slide → patient table: duplicated images mean two `.xml` files and two calibrations for one glass slide) and C (a Groovy script: the step that decides wells would sit outside the app and its tests).
  - **092**: membership by interior point. Ambiguity only across distinct classes. Leftovers warned about and left out, rather than kept under the plain class (which would make a mixed-patient sample) or blocked (one stray shape would halt 13 patients).
  - **093**: MultiPolygon geometry is kept in the read report so a merged outline works. What is cut is unchanged.
  - **Also:** `render` now returns the grouped context so Stage 4's palette matches.
- `README.md`: a short section, "Several patients or regions on one image": what to draw in QuPath, what to tick, and what the names become.

- [ ] **Step 2: Full verification**

```bash
uv run ruff check src/qupath_to_lmd/outlines.py src/qupath_to_lmd/ui_samples.py src/qupath_to_lmd/ui_slides.py src/qupath_to_lmd/plot.py src/qupath_to_lmd/geojson.py streamlit_app.py tests/test_outlines.py tests/test_ui_behaviour.py tests/test_plot.py tests/test_geojson.py
uv run pytest
uv run python tools/golden_harness.py check
```

Expected: ruff clean on these files; every test passes; golden "OK" with no re-bless.

- [ ] **Step 3: Commit the docs**

Commit `GLOSSARY.md decisions.md README.md` with the subject "Document outlines".

- [ ] **Step 4: Manual fixture and smoke test**

- Write `make_outlines_fixture.py` in the scratchpad. It reads `demo_Qupath_project/TD_01_verysmall_mIF.geojson`, finds the bounding box of its annotation polygons, and splits that box in half. It adds outline features of class `P1` (left half) and `P2` (right half), overlapping by 10% in the middle. It writes `TD_01_outlines.geojson` to the scratchpad. It prints how many annotations fall in each half and in the overlap, so the manual script can state the expected counts.
- `uv sync && uv run streamlit run streamlit_app.py --server.headless true --server.port 8599`, then confirm the log shows no exception.

- [ ] **Step 5: Hand over**

Give Jose a numbered manual test script using the fixture's printed counts:
- upload, then confirm calibration
- tick `P1` and `P2` as outlines
- the expected success line and the leftover warning
- the picture: borders, labels, grey shapes
- *Whole shapes* → the expected `P1_*`/`P2_*` samples on the plate
- download → `provenance.json` has `outlines`, and `_processed.geojson` reopens in QuPath with `P1_…` classes
- untick → back to the original classes

Then say "ready to push" and stop.
