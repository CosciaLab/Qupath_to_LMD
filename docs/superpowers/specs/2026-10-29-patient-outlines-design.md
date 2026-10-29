# Outlines: group shapes by the outline they sit in — design

**Date:** 2026-10-29 · **Branch:** `feat/patient-outlines` (one branch, one PR into `dev`)
**Not in this branch:** a "replicates for all classes" input, which gets its own branch after.

---

### Intent

Loren's lab annotates 13 patients × 19 biological categories by hand in QuPath, with several
patients' tissue on one image. Defining every patient × category × replicate combination as a
QuPath class (the Extras generator) means 200+ classes, which is unworkable when annotating by
hand. They want to:

1. mark each patient's tissue once,
2. annotate structures with only the 19 categories,
3. have the app combine the two into the sample, `P1_gf`, pooled across every image and slide,
   and split into replicates later.

Replicates already work this way: *Selected shapes* and *Regions and circles* pool a class across
slides and split it into replicates. What is missing is step 3, combining patient and category.
Pooling by class name alone would put `gf` from P1 and `gf` from P2 into one well.

Agreed with Jose during brainstorming:

- **An outline is a shape whose class names a group**, e.g. one class per patient (`P1`…`P13`). It
  takes its label from **its class**, not its `name`, so labels come from a list and cannot be
  mistyped.
- **Outlines are never cut.**
- A shape belongs to the outline that contains its **interior point** (`representative_point()`),
  so a structure that pokes slightly over the outline's edge is still assigned.
- Shapes in **no outline**, or in **two or more overlapping outlines**, are **left out, counted
  on screen and drawn grey**. They are not kept under their plain class, because that would
  quietly make a sample from several patients. The app does not block.
- The new class is **`<outline>_<class>`**, outline first, e.g. `P1_gf`.
- The control is **opt-in and empty by default**. With nothing ticked the app is exactly as
  before, and so are the golden bytes.
- Rejected: a slide → patient table (B), because patients that share a glass slide would have to
  be split into duplicated images, giving two `.xml` files and two calibrations for one physical
  slide at the LMD. Also rejected: a QuPath Groovy script (C), because the step that decides the
  wells would then happen outside the app and its tests.

### Success criteria

1. A GeoJSON with outlines of class `P1`, `P2` and structures of class `gf`, `yf` inside them
   reaches Stage 2 as classes `P1_gf`, `P1_yf`, `P2_gf`, … once `P1` and `P2` are ticked as outline
   classes.
2. With several slides, `P1_gf` from each slide is one sample, in one well.
3. Every shape in the file is accounted for: grouped + left out + outlines = shapes after reading.
   Left-out counts are on screen per slide and class before anything is downloaded.
4. No outline is ever in a `CollectionPlan`, `.xml`, or samples-and-wells scheme.
5. With no outline class ticked, `tools/golden_harness.py check` passes unchanged and Stage 2
   receives the identical `SlidesContext` object.
6. A MultiPolygon of an outline class, such as one patient with two tissue pieces merged in
   QuPath, works as an outline.
7. The processed GeoJSON in the download reopens in QuPath with `P1_gf` as the class.

---

### What the scientist sees

**In QuPath:** the 19 category classes plus one class per patient. One outline around each
patient's tissue, or several outlines of the same class for separate pieces. Several patients
on one image, or one patient over several images, are both fine.

**In the app, at the top of Stage 2, before the method choice:**

- A multiselect, **"Classes that outline a group of shapes"**, over every class on every slide.
  Empty by default. Help text: outlines are never cut, and every shape inside one is renamed
  `<outline>_<class>`.
- When something is ticked:
  - an `st.success` line: *"13 outlines grouped 4 210 shapes into 203 classes."*
  - if any shapes are left out, an `st.warning`, plus a table per slide and class: *no outline*,
    *several outlines*. The warning says these shapes will not be collected and how to fix it in
    QuPath (extend or redraw the outline, or remove the overlap).
  - an `st.info` if an outline class appears more than once on one slide. That is allowed, since
    a patient can have two tissue pieces.
  - an `st.warning` if two combinations produce the same name (`P1` + `a_b` and `P1_a` + `b`),
    because they would share a well.
  - if a MultiPolygon outline was used, a caption saying Stage 1 listed it as ignored because it
    cannot be cut, and that it is used here as an outline.
- In the **classes picture**, outlines are drawn as labelled borders and left-out shapes in grey.
- The class table, the method, replicates, the collector and the export all see `P1_gf` as an
  ordinary class.

---

### Code

**Library: `src/qupath_to_lmd/outlines.py`, new, with no Streamlit in it**

- `OUTLINE_SEPARATOR = "_"`
- `group_by_outlines(gdf, outline_classes, multipolygons=None) -> tuple[GeoDataFrame, OutlineReport]`
  - Outlines are the shapes of `outline_classes`, plus any MultiPolygons of those classes from
    `multipolygons`.
  - The remaining shapes' `representative_point()` is spatially joined (`geopandas.sjoin`,
    `within`) against the outlines.
  - Exactly one outline: `CLASS_NAME` becomes `f"{outline}{OUTLINE_SEPARATOR}{class}"`, and the
    nested `classification` is rewritten to match (see the open item below).
  - Zero, or two or more: removed from the frame and kept in the report with a reason.
  - Returns the frame with outlines and leftovers removed. Its index is a subset of the input's.
- `OutlineReport` (dataclass):
  - `outlines`: GeoDataFrame of outlines, with label, for drawing
  - `left_out`: GeoDataFrame with `CLASS_NAME` and `reason` (`"no outline"` / `"several outlines"`)
  - `n_grouped`
  - `repeated_outlines`: `{class: count}` for outline classes appearing more than once
  - `colliding_names`: `{name: [(outline, class), …]}`
  - `n_multipolygon_outlines`
  - `.summary()`: per-class table of left-out counts, for the warning
  - `.provenance()`: dict
- `outline_classes` empty → returns `(gdf, empty report)` with the frame untouched.

**Reading: `geojson.read_and_qc`.** `report.multipolygons` keeps the geometry of dropped
MultiPolygons as a GeoDataFrame, not only name and class. Nothing that is cut changes. The
"ignored" row in the Stage 1 table is unchanged. Check that `GeojsonReport.summary()` and
anything else reading `multipolygons` still work with a GeoDataFrame.

**UI**
- `ui_slides.SlidesContext` gets `outlines: dict[str, OutlineReport] = field(default_factory=dict)`.
- `ui_samples.outline_step(context) -> SlidesContext`, called in `ui_samples.render` before
  `method_step`.
  - Nothing ticked → return `context` itself.
  - Otherwise group each slide, with `st.cache_data` keyed on the slide fingerprint and the
    sorted outline classes. Return `dataclasses.replace(context, slides=[replace(slide,
    gdf=grouped)…], outlines={…})`.
  - `SlidesContext.fingerprint` already includes the class names, so every cache and picture key
    downstream changes when outlines change.
- `plot.plot_shapes(…, outlines=None, left_out=None)`: borders with a label at each outline's
  representative point, and left-out shapes in grey. `ui_samples._draw_input` passes both from
  `context.outlines`.
- Session key `outline_classes`, in `DEFAULTS` and in `facts.md`'s key table.
- Provenance: an `outlines` entry with the classes used and the counts from
  `OutlineReport.provenance()`. Where provenance is assembled is settled in the plan.

**Unchanged:** the three methods, `slides.py`, `plate.py`, `export.py`, `model.py`.

**Known interaction:** "give every shape its own well" numbers the grouped class, `P1_gf_001`,
using the existing `original_classification_name` mechanism. `outlines.py` must not write that
column, or exploding would match nothing.

### Open item for the plan

`geojson.rewrite_classification` sets `name` but leaves a multi-class `names` list in place. For a
multi-class structure inside an outline, the processed GeoJSON would carry both. The plan should
check how QuPath reads such a value and, if needed, have `outlines.py` drop `names` when it writes
the grouped name, without changing `rewrite_classification` for other callers.

---

### Testing

- `tests/test_outlines.py`, using hand-built frames:
  - inside one outline → `P1_gf`
  - no outline, and two overlapping outlines → left out with the right reason
  - the accounting invariant: grouped + left out + outlines = shapes in
  - a structure crossing the edge with its interior point inside → assigned
  - a MultiPolygon outline groups shapes in both pieces
  - two `P1` outlines on one slide → both feed `P1_*`, reported as repeated
  - colliding names are reported
  - no outline class in the output frame
  - the classification dict reads `P1_gf`
  - empty `outline_classes` → frame unchanged
- Several slides: `P1_gf` from two slides is one sample in `slides.whole_shape_samples`.
- `tests/test_ui_behaviour.py`: nothing ticked → same context object; leftovers →
  `st.warning`, not `st.error` and no stop.
- Golden: `tools/golden_harness.py check` passes with no re-bless. No new case, because outlines
  only rename classes before the paths the existing cases cover.
- Every assertion message says what breaks in the app.
- Manual fixture: a scratchpad script adds two outlines (`P1`, `P2`) to
  `demo_Qupath_project/TD_01_verysmall_mIF.geojson`, with one shape deliberately outside both and
  one in their overlap. It is not committed to the repo.

### Documentation

- `GLOSSARY.md`: **outline**. Update **sample** (pooling is by grouped class when outlines are
  used) and the **group** table.
- `decisions.md`: entries for outlines and the rejected B and C; the interior-point rule and
  leaving leftovers out; MultiPolygon outlines.
- `facts.md`: module row, pipeline step, session key, `SlidesContext.outlines`.
- `README.md`: a short "several patients or regions on one image" section.
