# ROADMAP

**Round one — two workflows on one backend — is complete.** Phases 0–5 are merged and Phase 6
is in review. Written 2026-08-26, revised 2026-08-27. Decisions behind it: `decisions.md`
009–053. This is a plan, not a contract — phases get revised as we learn, and several were.

Round two starts at [section 5](#5-round-two--usability-and-confidence).

---

## 1. The shape of the change

Today the app is one linear page: upload → calibrate → class-to-well → export. That page
*is* the legacy workflow; there is no seam to hang a second one on.

The restructuring introduces that seam. Both workflows do their own thing and then produce
the **same object**, and everything downstream of it is shared:

```
  LEGACY (annotations)                   CELLS (segmentation)
  class = sample = well                  class → replicates → budgeted cell selection
  optional "explode" into 1-shape wells  spread / random, adjacency constraint
          │                                        │
          └────────────────┬───────────────────────┘
                           ▼
                    CollectionPlan
        (shapes + class + replicate + group + well
         + calibration + pixel size + params + provenance)
                           ▼
              shared QC → smoothing → path order
                    → XML + zip bundle
```

### CollectionPlan — the seam

A GeoDataFrame plus a parameter record. One row per shape that will be cut:

| Column | Meaning |
| --- | --- |
| `shape_id` | stable id, from the QuPath `id` field |
| `geometry` | polygon, in image pixel coordinates, unmodified from QuPath |
| `class_name` | the biology (QuPath classification name) |
| `replicate` | 1-based replicate index; `None` in the legacy workflow |
| `group_key` | **the unit that maps to exactly one well** |
| `well` | e.g. `"C3"` |

`group_key` is the whole trick. Legacy: `group_key == class_name`. New workflow:
`f"{class_name}_r{replicate}"`. Legacy explode: `group_key == shape_id`. One rule for
well assignment, plate QC, and the plate CSV, three user-facing behaviours.

Alongside the frame, a record of everything that determined the output — calibration
point names and coordinates, µm/px, smoothing tolerance, path optimization, selection mode,
adjacency setting, random seed, source filename, session id. It goes in the download zip
so a collection is reproducible and citable. Today those parameters exist only as scattered
`st.session_state` keys and are not all recoverable after the fact.

### What moves where

`streamlit_app.py` becomes a router plus the shared steps. The library splits by
responsibility instead of by "core vs utils", and library functions take explicit
arguments and return values instead of reaching into `st.session_state`
(`CLAUDE.md` rule 5):

```
streamlit_app.py        router, session init, shared steps (upload, calibration, plate, export)
src/qupath_to_lmd/
  model.py              CollectionPlan, canonical schema, provenance record
  geojson.py            read + QC + sanitize          (from core.py, utils.py)
  plate.py              wells, layout df, saw dict     (from utils.py)
  qc.py                 triangle QC, plate-fit QC, shape sanity
  stats.py         NEW  per-class descriptive stats for decision support
  selection.py     NEW  budgets, spread/random sampling, adjacency graph
  export.py             collection build, smoothing, path order, XML, zip  (from core.py)
  ui_legacy.py     NEW  legacy workflow steps
  ui_cells.py      NEW  cell workflow steps
```

The two workflows are chosen on a landing step. Because `objectType` in the GeoJSON
distinguishes `annotation` from `cell`, the app can count both and *suggest* — "this file
has 121 cells and 7 annotations, the cell workflow is probably what you want" — while
leaving the choice to the user. Files legitimately contain both.

---

## 2. Phases

Each phase is a branch, a PR into `dev`, and a manual pass by Jose before it goes anywhere
(`CLAUDE.md` rule 2). Phases 0–1 are prerequisites; 2–5 are the new workflow in
dependency order and each one leaves the app working.

### Phase 0 — Foundations, no user-visible change

> **Done** (`decisions.md` 021).

- **Fix the CRS bug.** `geopandas.read_file` tags QuPath GeoJSON as `EPSG:4326`, so every
  `.area` / `.distance` call runs against a nonsense geographic projection and warns.
  Set `crs = None` on read. Nothing area-based is trustworthy until this is done.
- Introduce `model.py` (CollectionPlan + schema) and split `core.py`/`utils.py` per above.
- Rewire the legacy workflow onto CollectionPlan, behaviour unchanged.
- Fix, since we are in this code anyway: the 384-hard-coded well validation
  (`utils.py:141`) and the always-`_384_wellplate.csv` filename
  (`streamlit_app.py:301`). Both are already logged in `facts.md`.

**Regression gate.** No test suite exists (`decisions.md` 006/008), so before touching
anything: run current `master` on both demo GeoJSONs, save the XML output, and require the
refactored code to produce byte-identical XML for the same inputs. A golden file is a
cheap, honest safety net for a pure refactor and it is the one thing that makes Phase 0
safe to do at all.

### Phase 1 — Router and shared steps

> **Done** (023–025).

- Landing step: workflow choice, with the `objectType` count as a suggestion.
- Shared steps factored out and used by both workflows: upload + QC, calibration selection
  + triangle QC, **µm/px input**, plate layout + well assignment, export.
- µm/px is a required input before any area figure is shown (`decisions.md` 011). The app
  cross-checks it against the value implied by `Cell: Area` in `measurements` where those
  exist, and warns on a mismatch — it does not overwrite what the user typed.
- Legacy workflow fully functional, including explode. **From here the legacy path is
  frozen**: later phases must not change its output.

### Phase 2 — Cell workflow: class selection with decision support

> **Done** (032–036).

The user needs to answer "how much can I even collect?" before choosing budgets. QuPath
`measurements` gives 126 fields per cell to work with.

Per class, shown as a table before anything is selected:

- cell count
- total area (µm²)
- area per cell: median and IQR, min, max
- spatial extent / bounding box and cell density
- how many cells fall below a usable-area floor

Then a multi-select of classes to include. Everything after this operates on that subset.

A first version of the Phase 4 preview belongs here too: all shapes drawn, coloured by
class, so the user can see what they are including before they think about budgets. Same
plotting function, one layer instead of two.

### Phase 3 — Replicates and budgets

> **Done** (038–039).

- Per included class: number of replicates.
- Per class: budget as **either** a cell count **or** a target area in µm², per replicate.
- **Feasibility, shown before selection runs**, because this is where experiments go
  wrong: `replicates × budget` against what the class actually holds, with the shortfall
  named in the units the user chose. Warn and let them continue with a partially-filled
  replicate if they want to (`decisions.md` 003) — never silently under-deliver.
- Plate capacity: total `group_key` count against available wells, warned at this step
  rather than discovered at export.

### Phase 4 — Selection engine (`selection.py`)

> **Done**, after three rounds of rework (040–045).

The scientific core. Given a class, a budget, and constraints, choose cells.

**Default mode: spatial binning** (`decisions.md` 016). Cluster the class's cell centroids
into *k* spatial bins (k-means), then take the cell nearest each bin centre. Rationale for
spreading at all: a replicate drawn from one corner of the tissue measures that corner, not
the class — spreading averages over local gradients, staining artefacts and niche effects.

Rationale for *binning* specifically, rather than the greedy farthest-point sampling this
roadmap originally proposed: farthest-point maximises separation by racing to the extremes,
so it **over-samples the tissue boundary**. Measured on the 121 cells of
`Single_cells.geojson`, selecting 20 (`decisions.md` 016 records the prototype):

| mode | min pairwise gap | mean distance from tissue edge |
| --- | --- | --- |
| random | 26 px | 80 px |
| farthest-point | **101 px** | 67 px — *biased to the rim* |
| spatial binning | 33 px | 72 px |
| *(all 121 cells)* | — | 78 px |

Farthest-point wins on separation and loses on representativeness. Binning keeps most of
the separation with near-population-average tissue depth, and covers the tissue visibly
more evenly.

- **Replicates fall out of the same structure.** For *r* replicates of *k* cells: bin once
  into *k* bins, and give replicate *i* the *i*-th nearest cell to each bin centre. Every
  replicate then spans the whole tissue, replicates are structurally comparable, sizes are
  equal and membership cannot overlap. Verified on the demo file: 3 replicates × 12 bins →
  sizes 12/12/12, overlap 0. This is the spread-and-interleaved behaviour Jose confirmed
  (`decisions.md` 015), obtained by construction rather than by a second algorithm.
- **Random mode** as the alternative, for users who want a straightforwardly unbiased draw.
- **Adjacency constraint** (`decisions.md` 013): when adjacent cells are not allowed, no
  two selected cells may touch or overlap, evaluated on the **pre-smoothing, pre-dilation
  QuPath geometry**, via a `shapely.STRtree` neighbour graph. It composes cleanly with
  binning — if a bin's nearest candidate touches something already selected, take that bin's
  next-nearest. Note binning alone does *not* guarantee non-adjacency (min gap 33 px above),
  so the constraint does real work and is not redundant.
- **Area budgets need an iteration.** *k* is known upfront only when the budget is a cell
  count. For an area target, estimate *k* from the class's median cell area, then add or
  drop bins until the achieved area brackets the target — and report what was achieved.
- **Seed exposed and recorded** in the provenance record, so a selection can be reproduced
  and reported in a methods section.
- Report achieved vs requested cells and area per replicate.

**Live preview** (`decisions.md` 017). The selection is drawn as it is configured:
unselected shapes in grey, selected shapes coloured by replicate, calibration triangle
overlaid. This is the feature that makes the parameters above usable — a user can see
clumping, edge bias or a starved replicate immediately instead of inferring it from
numbers. Measured cost of a two-layer geopandas/matplotlib render: ~0.35 s at 10k shapes,
1.8 s at 50k, 7.6 s at 200k; falling back to centroid scatter above a threshold is 0.14 s
at 200k. Cheap enough to redraw on every widget change for realistic files, so no
interactive plotting library is needed. The same function should produce the export QC
image, replacing the separate `py-lmd` plot — one picture, drawn one way, before and after.

### Phase 5 — Export parameters

> **Done** (046–048, 052–053).

Two parameters exposed with recommended defaults and an on-screen explanation of each
(`decisions.md` 010):

- **Smoothing / simplification tolerance.** Stays as it is today: shapely Douglas-Peucker
  in **pixels, default 1** (`decisions.md` 019). What changes is that the number becomes
  visible and editable instead of hard-coded, with a plain-language explanation next to it —
  the outline may move by up to this many pixels, higher values mean fewer vertices and
  faster cutting, lower values follow the annotation more exactly. Then the user decides.
- **Cut-path optimization.** `none` / `greedy` / `hilbert`, ordering shapes to cut down
  stage travel and keep focus stable. py-lmd already ships `tsp_greedy_solve` and
  `tsp_hilbert_solve` and both are importable from `lmd.lib`, but its `Collection` path —
  the one we use — never calls them; only the mask-based `SegmentationLoader` does. So we
  compute shape centroids, get an order from py-lmd, and add shapes in that order. Matters
  little for 20 annotations and a great deal for 2000 cells.

### Phase 6 — Scale hardening

> **In review** (050–051).

Cell segmentation files can be 10⁴–10⁶ shapes; the demo has 131. Real files will surface
problems this app has never met: Streamlit reruns the whole script on every widget change,
`py-lmd`'s QC plot is matplotlib, and Community Cloud has a memory ceiling.

Needs measurement before design, so this phase starts by profiling a genuinely large file
against Phases 2–5 and reporting where it hurts. Likely: cache the expensive stages on file
hash, keep selection off the rerun path, and downsample the preview plot.

---

## 3. Sequencing

Phase 0 and 1 are prerequisites and Phase 0 pays for itself immediately by fixing the CRS
bug. 2 → 3 → 4 is a hard dependency chain: you cannot budget without stats, or select
without a budget. Phase 5 is independent and could be pulled earlier if smoothing control
is wanted sooner — it improves the legacy workflow too. Phase 6 waits for a real file.

Suggested order: **0 → 1 → 2 → 3 → 4 → 5 → 6**, with 5 promoted on request.

## 4. Open questions from round one

Not blocking, but they will need answers as the phases land.

1. ~~LMD7 positioning precision~~ — **closed.** The tolerance keeps its current pixel
   default and is simply explained to the user (`decisions.md` 019).
2. ~~Replicate spatial strategy~~ — **settled**: spread and interleaved
   (`decisions.md` 015), obtained structurally by the binning scheme in Phase 4.
3. **Is a dilation step coming?** Defining adjacency as "pre-dilation" implies one may be.
   If shapes are ever grown outward, dilated neighbours can overlap even with the adjacency
   constraint on — that would need saying on screen when it happens.
4. **Measurement-ranked selection** (take the top N by Ki67, panCK, …) is out of scope now,
   but the data supports it and `selection.py` should not be built in a way that forecloses it.
5. **Multiple slides into one plate** — **deferred by Jose**, to think about. Context for
   when we return to it: README FAQ 6 tells users to do this by hand with a shared
   samples-and-wells scheme, and the new workflow generates well assignments per file,
   which makes that manual recipe harder rather than easier.

---

# 5. Round two — usability and confidence

Six items from Jose, planned 2026-08-27. Round one made the app do the right thing; this round
is about making it pleasant to use and safe to change.

Ordered so that each PR lands on ground the previous one made solid. **PR 1 first on purpose:**
everything after it is either a wide rename or a change to how amounts are computed, and both
are much safer with a test suite underneath.

| PR | Branch | What | Depends on |
| --- | --- | --- | --- |
| 1 | `test/pytest-suite` | A real test suite | — |
| 2 | `refactor/nomenclature` | One word for a cuttable outline, plus a glossary | 1 |
| 3 | `feat/pixel-size-estimated` | Estimate the scale; ask only when we cannot | 1 |
| 4 | `feat/minimum-area-filter` | Per-class minimum area, default 150 µm² | 3 |
| 5 | `feat/plate-control` | Start well, and an editable plate grid | 1 |

---

## PR 1 — `test/pytest-suite`: a real test suite

**Why now.** `decisions.md` 006 and 008 deliberately deferred this until asked, on the grounds
that the code was too entangled with `st.session_state` to test well. That is no longer true:
since Phase 0 the library layer is pure, and round one accumulated **nine verification scripts
with roughly 130 assertions** that already exercise it. They live in a scratchpad and vanish
with the session, which is the wrong place for the only checks this app has.

**What.** Convert them into `tests/`, one module per concern:

```
tests/
  test_geojson.py      reading, QC, multi-class, MultiPoint, unused columns
  test_plate.py        wells, layouts, assignment, saw parsing
  test_qc.py           triangle, saw validation, pixel-size comparison
  test_stats.py        per-class statistics, display rounding
  test_budget.py       modes, feasibility, group keys
  test_selection.py    spread, interleaving, adjacency, determinism
  test_export.py       cut order, path stats, tolerance, bundle contents
  test_model.py        CollectionPlan, plan builders, exclusion classification
  test_golden.py       runs tools/golden_harness.py check
  conft.py             fixtures: the demo files, a synthetic touching chain
```

**Details that matter:**

- The statistical assertions (spread quality, interleaving) must stay **baseline-relative**, not
  absolute — a single draw against its own 95th percentile fails 5% of the time by construction,
  which is a lesson already paid for once in Phase 4.
- The synthetic fixtures are load-bearing and should be committed as *generators*, not files: the
  20-square touching chain, and small QuPath-shaped exports. The real 83.7 MB export stays out of
  the repo.
- Mark the slow ones (`@pytest.mark.slow`) so the default run stays quick.
- `pytest` and `pytest-cov` are already in the dev group; nothing new to install.

**Also worth deciding in this PR:** whether to add CI. A GitHub Action running `pytest` and the
golden harness on every PR would enforce this automatically, which is the point of having tests.
Left as a question rather than assumed.

**Supersedes** 006 and 008 explicitly.

---

## PR 2 — `refactor/nomenclature`: one word, and a glossary

**The problem.** The same thing is called a **shape**, a **polygon** and an **object** across the
code, the UI and the docs. `n_shapes_kept`, `POLYGON_LIMIT`, "unclassified objects" — all three
appear within a few lines of each other.

**Proposal: "shape" is the canonical term** for one cuttable outline.

- It is what **py-lmd** calls them (`new_shape`, `Collection.shapes`), and py-lmd is what
  actually cuts.
- It is already the dominant term in this codebase.
- It does not collide with either of the other two meanings, which stay useful:
  - **object** — reserved for QuPath's vocabulary when discussing the input file, because that is
    what QuPath's own UI says (annotation objects, cell objects, detection objects).
  - **polygon** — reserved for the *geometry type*, alongside MultiPolygon and LineString.

So: "QuPath exports **objects**; we read them as **shapes**, each with a **Polygon** geometry;
the LMD cuts **shapes**."

**What changes.** `POLYGON_LIMIT` → `SHAPE_LIMIT`; UI strings saying "objects" where they mean
cuttable shapes; docstrings; `facts.md`. A `GLOSSARY.md` at the root defines shape, object,
polygon, class, group, replicate, well, calibration point, collection, plan — linked from
`README.md` and `CLAUDE.md`.

**Risk.** A wide, mechanical diff touching almost every file, which is exactly why it comes after
PR 1. It changes no behaviour, so the golden harness should stay green throughout — if it does
not, something was renamed that was not a name.

---

## PR 3 — `feat/pixel-size-estimated`: estimate first, ask only when necessary

**The question Jose raised:** when *can't* we estimate the scale? Answered from real data:

| situation | estimable? |
| --- | --- |
| Cell/detection export **with** measurements | **Yes** — median ratio 0.9998, spread 0.23–0.66% |
| Cell export **without** measurements | No — QuPath only includes them if the user ticks the box |
| Annotation-only export | No — annotations carry no area measurements |

So it cannot be removed outright: **the first `Exemplar001` export had no measurements at all**,
and annotation-only files never will. But the estimate is excellent when available, and it is
available whenever the user ticks one box.

**Proposal — invert the current design** (`decisions.md` 011, which had the app ask and merely
cross-check):

1. If the file supports an estimate, **use it**, stated plainly with the object count and spread.
2. **Move the input next to the area budget control**, not a step of its own. Previous users were
   confused about why the app wanted a pixel size at all; sitting beside "budget by area" it
   explains itself, because that is the only thing it feeds. A small number input with a help
   icon carrying the longer explanation and the magnification reference table.
3. Warn if the spread across objects is wide (say >2%), since that is the signature of a mixed or
   rescaled export rather than a clean one.

**Consequence for the step order.** Step 4 disappears as a separate step and the cell workflow
becomes: 4 classes → 5 replicates, budgets and scale → 6 plate → 7 selection → 8 export. The
scale stops being a gate the user must pass before seeing anything.

**Why this is now the better trade.** 011 chose explicit entry so nobody accepts a derived number
unread. In practice the estimate has been right every time it could be computed, and the input is
the step users stumble on. It also matters more than it did: PR 4's default minimum area is in
µm², so without a scale that default means nothing — auto-estimating makes it work out of the box.

**Supersedes** 011, and refines 029 and 038 (which established that nothing *requires* the scale).

---

## PR 4 — `feat/minimum-area-filter`: per class, default 100 µm²

**Why.** Microdissection cannot reliably collect below a certain area, and that floor is real
regardless of what the user asks for. Different biologies have genuinely different sizes, so one
global number is wrong — which is why the global floor added in Phase 2 was removed again
(`decisions.md` 034), with the note that it belonged per class alongside the budgets.

**What.** In the per-class editor, a column for **minimum area (µm²), default 100** — Jose's
figure, on the grounds that collecting less tissue than that reliably is very difficult.

**The filter applies before anything is measured.** Jose was explicit: it runs *pre* per-replicate
area measurement, so every figure the user sees is post-filter. Concretely, the order is:

1. Filter each class by its minimum area.
2. Compute per-class statistics — count, total area, median — **on what survives**.
3. Compute feasibility against those post-filter figures.
4. Select from the filtered pool.

That ordering is the whole point: it makes "available area" mean *collectable* area. Getting it
wrong in the other direction — filtering after the statistics — would show the user an amount
they cannot actually have, which is exactly the class of error this app exists to prevent.

**Other details:**

- **Report the exclusions per class**, as counts and as a share, before the user commits.
- Zero disables it, for a user who wants everything.
- Recorded in `provenance.json` per class.
- On the real export at 0.6535 µm/px, `Immune cells` has a median of 87.7 µm² and `Tumor` 142.8 —
  so a 100 µm² floor removes roughly half the immune cells. Not cosmetic, and worth seeing on
  screen before it silently changes what a budget can deliver.

**Depends on PR 3**, because a µm² default is meaningless without a scale, and asking every user
to type one before the default filter works would be a step backwards. It also depends on PR 3's
relocation of the scale input: the filter and the scale both belong beside the budgets, since all
three are about how much tissue ends up in a well.

---

## PR 5 — `feat/plate-control`: start well, and an editable plate

Two related things, both about how classes reach wells.

### Start well

**What.** An input for the first well to fill; assignment proceeds from there through the usable
wells in order.

**Why.** This is the multi-slide-into-one-plate problem (round one open question 5,
`decisions.md` 020) solved the simple way: process slide 1 from `B2`, note that it ended at
`B9`, then process slide 2 from `B10` into the same plate. No cross-file state, no new concepts.
The app should **report the last well used** so the next run's starting point is obvious, and
warn if the range would overflow the plate.

### Editable plate, not drag-and-drop

**Researched.** Streamlit has **no native drag-and-drop** of items into a grid.
[`streamlit-sortables`](https://github.com/ohtaman/streamlit-sortables) does do multi-container
drag (pip-installable, no build step, Apache 2.0, ~137 stars) but its model is items between a
handful of named buckets — with 384 wells as drop targets it would be unusable. A genuine plate
drag-and-drop means a custom React component: a build step, a frontend to maintain, and more
weight on a deployment we have just spent a PR slimming down.

**Proposal instead: make the plate itself editable** with `st.data_editor` and a
`SelectboxColumn` per well, so each cell offers a dropdown of the user's classes. This is direct
manipulation of the actual plate grid, with **no new dependency**, and it is *better* than dragging
for correctness — a dropdown cannot produce a typo or a class that does not exist.

**If that proves insufficient**, `streamlit-sortables` remains a fallback for a coarser
"drag classes into groups" step, to be decided on evidence rather than now.

---

## 6. Open questions for round two

1. ~~CI?~~ **Settled: yes.** Jose delegated the design; PR 1 includes a GitHub Action and the
   tests are to state plainly what is broken when they fail.
2. ~~Is 150 µm² right?~~ **Settled: 100 µm².** Still worth watching whether one default across
   biologies is right, given `Immune cells` sits below it on the real export.
3. **Do the well dropdowns actually feel good?** Jose is not convinced. Build it, look at it, and
   be willing to throw it away — the fallback is the read-only plate we have now plus the start
   well from PR 5, which already covers the multi-slide case.
4. **Does the estimated pixel size need an audit trail** beyond `provenance.json` — e.g. stated on
   the QC image — given PR 3 makes it the default rather than something the user typed?
5. **Dilation** is still unanswered from round one, and still shapes what "adjacent" means.

---

# 7. Round three — cellular neighbourhoods and circle packing

Planned 2026-09-17, from Jose's brief. New scope rather than a deferred phase, so it gets its
own round. Decisions behind it: `decisions.md` 066.

**The experiment it serves.** Collecting by cell type, but as mini-bulk rather than cell by
cell. A single cell is too little tissue; a whole class outlined by hand is one enormous
irregular cut the LMD would trace for hours. So: derive the tissue belonging to each class from
the cells themselves, then fill it with many small circles that cut fast and add up to a chosen
area per replicate.

```
cells → Voronoi territory per cell → merge by class → regions
                                                        │
                                          PR 1: cut the regions
                                          PR 2: pack circles into them
                                                        ▼
                                                  CollectionPlan → .xml
```

## PR 1 — regions

> **Done.** Third workflow, `regions.py`, `ui_packing.py`, a `regions` golden case.

Voronoi projection capped at a disc and clipped to the hull of the cell outlines, merge by
class into one region per contiguous area, area-balanced dealing across replicates, and the
existing plate and export path. Useful on its own: it collects whole neighbourhoods.

## PR 2 — folded into the same branch

> **Done.** Jose: "without the circle packing the main functionality is half baked… one
> functionality one PR." So both halves ship together on `feat/circle-packing`.

Ported from the prototype at `PY38_CirclePackingStreamlit`, which is random dart-throwing with
rejection sampling — the right algorithm here — but which starts from an already-tessellated
file, has no LMD export, and is not reproducible across sessions. The port is not a copy;
measured changes, each to be logged:

- `numpy.random.default_rng` rather than the legacy global `numpy.random.seed`, with **one
  sub-stream per class** — in the prototype the stream is consumed sequentially across classes,
  so changing one class's spacing silently reshuffles every later class, which makes a
  feedback loop misleading.
- **Grid-hashed collision detection** in place of rebuilding a `shapely.STRtree` on every dart.
  That rebuild is O(n² log n) and is the dominant cost; the replacement is also *more correct*,
  because `STRtree.query` with no predicate is bounding-box only and rejects legal placements.
  Measured: 0.04 s for 148 circles, 0.09 s to saturation at 691.
- Pack **per region** with `shapely.prepare`, distributing a class's target area across its
  regions in proportion to area, rather than against a class-wide union.
- Areas from `polygon.area`, not analytic `πr²` — a buffered circle is a polygon and the
  prototype's accounting is ~0.4% optimistic.
- **A spacing-aware capacity estimate.** The prototype's flat 0.55 packing density ignores the
  gap between circles. Measured on a 2000×2000 px square at 0.3467 µm/px, circles 100–500 µm²:
  54.7% fill at a 0 µm gap, 42.9% at 2, 32.9% at 5, 21.4% at 10, **12.2% at 20**. A flat 0.55
  would tell a user with a 20 µm gap they had 4.5× more tissue than they do.
- `n_permutations` (best-of-N restarts) dropped: ×5 cost for a marginal gain, and it makes the
  random stream depend on it.

**What the measurements turned into, once it was built** — see `decisions.md` 067 and the
`facts.md` tables. Three bugs only running it could find: regions too narrow for any circle burned
the whole attempt budget each (119 of 684 on the real core), a one-sided deficit over-packed a
class by more than twice its target, and packing exactly the total left the last replicate ~5%
short. 8.6 s and 6-of-9 replicates filled became 0.7 s and 9 of 9.

**The smoothing interaction needs saying on screen.** At the default 1 px tolerance a circle of
radius ≤ 10 px goes from 65 vertices to 9 and **loses 10% of its area**; at radius ≥ 20 px the
loss is 2.6%. Area per replicate is this workflow's entire budget, so the achieved figure has
to describe the geometry that will actually be cut.

The feedback loop is the point: circle sizes, the gap, the seed, the area per replicate and the
replicate count all live in one step, and the projection and packing caches are what let it
redraw without re-tessellating.

## Revised after review — `decisions.md` 068 and 069

Jose's two rounds of notes on the working version, and what they changed:

- **The collection step moved above the plate.** The amount and the replicate count are what size
  the plate, so deciding them first shows the plate once, already correct. The `st.fragment` went
  with it: a fragment reruns only itself, so the plate and export below the loop would have sat
  there stale.
- **Step 6 became side by side** — inputs at a third of the width, a live picture of the
  collection at two thirds. It is the only step where a user changes a number specifically to see
  what it does, and scrolling between the two meant nobody tuned at all.
- **The amount per replicate became per class**, in one table with the replicate count, on
  `budget.ClassBudget`. On the real core two classes hold ~900 000 µm² each and a third holds
  220 000, so one global amount could not ask each for what it can give.
- **Class by fill, replicate by tab20 outline.** Rendering the real core showed both palettes
  contain an orange, so the fill had to be held back to alpha 0.7 or an orange circle of an
  orange class hid its own replicate ring.
- **Removed:** the enclosed-class warning (a non-issue that fired on 21 of 684 regions, so it
  appeared on an ordinary run) and the "Effort" control (it exposed `max_attempts`, which is how
  the sampler gives up, not a decision about the experiment).
- **Every µm² figure is a whole number with a thousands separator.** Areas here run to millions
  and a tenth of a square micrometre is far below anything the laser can place.
- **The merge was checked, not argued.** Jose read the picture as a broken merge; the merge was
  right and the *plot* was wrong — it drew region exteriors only, painting one region over
  207 000 µm² of the class it surrounds. The invariant (no two regions share any tissue) is now
  asserted, and holes are drawn as holes.
- **Colours are measured.** Two full-hue palettes have a fill-outline pair at contrast 1.00 —
  the same colour. Fills are now tinted toward white and outlines shaded toward black, giving
  1.78 everywhere with outlines 0.198 apart. Both floors are asserted.
- **Circle parameters are per class**, in the same table as the replicates and the amount. A
  sparse class needs smaller circles than a solid one before anything fits.

---

# 8. Round four — generalise the regions UI to the cell workflow

Jose, reviewing round three: *"I like this UI, and should be generalized to the segmentation-based
workflow as well."*

What the regions workflow got that the cell workflow has not:

- **One per-class table** carrying every number that class needs, instead of a global budget mode
  plus a separate per-class editor plus separate selection controls.
- **A live picture directly below the settings**, with a stable encoding — pale fill for the
  class, dark outline for the replicate — and both legibility floors asserted.
- **A metrics row** answering "is this a sensible collection?" at a glance.
- **Whole-number µm² with thousands separators** everywhere. The cell workflow's per-class table
  still shows two decimals, because `stats.for_display` and `DECIMALS = 2` are shared and asserted
  by `tests/test_stats.py`. That is the smallest piece of this and could be done on its own.

**Why it is its own PR.** The cell workflow has a minimum-area filter that must run before
anything is measured (`decisions.md` 060), a budget mode that switches the meaning of a column,
and a `st.fragment` whose existence depends on the plate sitting *above* it — the opposite of the
regions order. Converting it means re-deciding all three, and it needs its own manual pass over a
path that currently carries the most tests in the repo. `tools/golden_harness.py` covers the cell
output byte-for-byte, so the refactor is safe to attempt; it is the interaction design that needs
Jose's eye, not the plumbing.
