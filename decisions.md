# decisions.md

Append-only log of decisions about this app. **Never edit or delete an entry.** If a
decision changes, add a new entry and state which number it supersedes. Newest at the
bottom.

Entry template:

```
## NNN — <short title>
**Date:** YYYY-MM-DD · **Status:** active | superseded by NNN
**Decision:** what we do.
**Why:** the reasoning, including what we gave up.
**Alternatives rejected:** and why.
```

---

## 001 — Claude branches and commits; Jose pushes and opens the PR
**Date:** 2026-08-26 · **Status:** active
**Decision:** Claude works on a branch cut from `dev` and commits there. Claude never runs
`git push` and never opens a PR. Jose pushes and opens the PR into `dev`, every time.
**Why:** Jose stays the gate between local work and anything that reaches the shared
repo or the deployed app. Reviewing a local branch is cheap; unwinding a pushed branch is not.
**Alternatives rejected:** Claude pushing to a feature branch and letting Jose review the
PR — still puts code on the remote without a human having read it first.

## 002 — Manual verification in the running app is required before every push
**Date:** 2026-08-26 · **Status:** active
**Decision:** No feature is finished until Jose has opened the app locally and exercised
the change by hand. Claude's job is to smoke-test first and then hand over explicit
numbered manual test steps.
**Why:** Output correctness here means "the laser cut the tissue the scientist meant",
which no automated check in this repo currently asserts. A human clicking through the real
UI is the only end-to-end test that exists.
**Alternatives rejected:** Trusting a boot-without-exception smoke test — it catches import
errors and nothing about coordinates, wells, or plate layout.

## 003 — Warn rather than block
**Date:** 2026-08-26 · **Status:** active
**Decision:** When the app detects something suspicious, it explains the consequence and
lets the user proceed. `st.stop()` is reserved for states where no meaningful output is
possible at all, and each new one must be justified in this log.
**Why:** Users are scientists with a legitimate need for unusual setups, working on
irreplaceable samples. A hard stop on a heuristic ("only 20% of shapes are inside the
calibration triangle") blocks valid work; a clear warning preserves both agency and
informed consent. Transparency over paternalism.
**Alternatives rejected:** Strict validation gates — would make the app unusable for the
edge cases that motivate it, and pushes users to hand-edit XML instead.

## 004 — QuPath GeoJSON is the only input format
**Date:** 2026-08-26 · **Status:** active
**Decision:** All processing starts from a QuPath-exported GeoJSON FeatureCollection.
Alternative inputs (coordinate CSVs, raw XML, images) require a new entry here first.
**Why:** One input contract means one set of assumptions about geometry, classification and
calibration points to get right, and one thing to document for users. It is also what
QuPath already exports well.
**Alternatives rejected:** Accepting XML or CSV for re-editing existing collections —
plausible future work, but it would fork the QC logic and the coordinate handling.

## 005 — Two documents: facts.md (living) and decisions.md (append-only)
**Date:** 2026-08-26 · **Status:** active
**Decision:** `facts.md` records what is true about the app and is corrected in place when
it goes stale. `decisions.md` records why, is append-only, and later entries supersede
earlier ones by number. Working rules for Claude live in `CLAUDE.md`.
**Why:** Separating the two keeps facts trustworthy (no stale contradictions to sift) while
keeping reasoning auditable (no silently rewritten history).
**Alternatives rejected:** A single changelog-style document — mixes "what is" with "what
we decided", and one of the two always ends up wrong.

## 006 — No pytest suite for now; verification is manual plus scratch scripts
**Date:** 2026-08-26 · **Status:** active
**Decision:** The repo has no test suite (removed in commit `0530833`) and Claude will not
reintroduce one unprompted. Logic changes are verified with throwaway scripts in the
scratchpad that import from `src/qupath_to_lmd/` and use `mock_streamlit.patch_streamlit()`,
plus the manual UI pass from 002.
**Why:** Records the existing state rather than quietly reversing a deliberate removal.
Most of the code is entangled with `st.session_state`, so tests would either be shallow or
demand a refactor that is its own project.
**Alternatives rejected:** Adding pytest back as part of the next feature branch — that
buries a project-shaping decision inside an unrelated diff. Worth doing, worth doing
openly, and worth doing after the library layer takes explicit arguments (see `CLAUDE.md`
rule 5).

## 007 — Dependencies must land in both pyproject.toml and requirements.txt
**Date:** 2026-08-26 · **Status:** active
**Decision:** Any dependency change edits `pyproject.toml` and then regenerates
`requirements.txt` via `uv pip compile pyproject.toml -o requirements.txt`, in the same commit.
**Why:** Streamlit Community Cloud installs from `requirements.txt`; local dev resolves
from `pyproject.toml`/`uv.lock`. Touching only one produces a change that works on Jose's
machine and 500s in production.
**Alternatives rejected:** Dropping `requirements.txt` and having the cloud read
`pyproject.toml` — not reliably supported on the current Community Cloud runtime, and not
worth risking the deployed app to find out.

## 008 — Rules live in CLAUDE.md; features come before test infrastructure
**Date:** 2026-08-26 · **Status:** active
**Decision:** Confirmed by Jose. The working rules stay in `CLAUDE.md` so they auto-load
into every session rather than needing to be pointed at. And feature development takes
priority over building test infrastructure: Claude does not propose or add a test suite
until Jose asks. Reaffirms 006 rather than superseding it.
**Why:** Auto-loading means the rules apply by default instead of on remembering. On
testing, the app's value right now is in the features scientists are waiting on; the manual
UI pass from 002 is the accepted verification in the meantime.
**Alternatives rejected:** A `rules.md` Claude has to be handed each session — same content,
worse odds of being read.

## 009 — Two workflows converge on one CollectionPlan
**Date:** 2026-08-26 · **Status:** active
**Decision:** The app gets two entry workflows — *legacy* (manual annotations, one QuPath
class = one sample = one well, with the optional explode into per-shape wells) and *cells*
(segmentation shapes, class → replicates → budgeted selection). Both produce the same
object: a GeoDataFrame of shapes carrying `class_name`, `replicate`, `group_key` and `well`,
plus calibration points, µm/px and a parameter/provenance record. QC, smoothing, path
ordering and XML export live downstream of that object and are shared. See `ROADMAP.md`.
**Why:** `group_key` — the unit that maps to exactly one well — unifies all three
behaviours: legacy is `class_name`, explode is `shape_id`, new workflow is
`class_name + replicate`. One well-assignment and export path, three user experiences,
no duplicated coordinate handling (the part where a bug means cutting the wrong tissue).
**Alternatives rejected:** Two parallel apps or pages each with their own export — would
double the coordinate/QC logic, and the two copies would drift.

## 010 — Export exposes smoothing tolerance and cut-path optimization
**Date:** 2026-08-26 · **Status:** active
**Decision:** The two user-facing export parameters are the smoothing/simplification
tolerance and the cut-path optimization mode (`none`/`greedy`/`hilbert`). Tolerance is
specified in **µm**, not pixels. Both ship with a recommended default and an on-screen
explanation of the reasoning.
**Why:** Tolerance in pixels — the current hard-coded `simplify(1)` — silently changes
meaning with objective magnification; µm is the unit the instrument works in. The
recommendation is anchored to the cutting laser's positioning precision: below it,
simplification cannot change which tissue is cut, it only stops the stage tracing vertices
that do not matter. Path optimization cuts stage travel and focus drift, which is
negligible for 20 annotations and significant for 2000 cells. py-lmd already provides
`tsp_greedy_solve` / `tsp_hilbert_solve`, but only wires them into the mask-based
`SegmentationLoader`, not the `Collection` path this app uses — so we order shapes
ourselves using its solvers.
**Alternatives rejected:** Exposing shape dilation instead — deferred, see 013 and
`ROADMAP.md` open question 3. Exposing a minimum shape area — a per-class stats
concern (Phase 2), not an export parameter.

## 011 — The user supplies µm per pixel; the app cross-checks it
**Date:** 2026-08-26 · **Status:** active
**Decision:** µm/px is a required user input before any area figure is displayed. Where
QuPath `measurements` are present, the app computes the implied scale and **warns** on a
mismatch, without overwriting the entered value.
**Why:** Jose's call: an explicit input is unambiguous and auditable, and the scale is a
property of the acquisition that the user is responsible for knowing. Auto-filling a
derived number invites accepting it unread. The cross-check is nearly free and catches the
expensive typo — a 10× error turns an area budget into the wrong experiment.
**Alternatives rejected:** Auto-derive with override (recommended, not chosen). Working in
px² — area budgets in pixels² are not a quantity anyone can reason about experimentally.
**Note for implementers:** the derivation works because QuPath writes `Cell: Area` in µm²
while GeoJSON coordinates stay in pixels; `sqrt(Cell:Area / polygon_area_px)` gave
0.3467 µm/px with 0.2% spread across all 121 cells of `Single_cells.geojson`.

## 012 — Cell selection defaults to maximum spatial spread
**Date:** 2026-08-26 · **Status:** active
**Decision:** The default selection mode maximises spatial dispersion of the chosen cells
within a class, implemented as greedy farthest-point sampling. Random selection is the
alternative. Spread is the default regardless of whether adjacent cells are allowed, and
interacts with that constraint rather than being replaced by it.
**Why:** A replicate drawn from one corner of the tissue measures that corner, not the
class. Spreading averages over local biological gradients, staining artefacts and niche
effects, which is what a scientist means when they ask for N cells of a type. The
constraint case composes naturally — a farthest-point sampler already avoids neighbours.
**Alternatives rejected:** Ranking by a QuPath measurement as the primary mode — deferred,
supported by the data and worth building later; `selection.py` must not foreclose it
(`ROADMAP.md` open question 4). Random as the default — reproducible but noisier for the
same number of cells.

## 013 — Adjacency is judged on pre-dilation geometry
**Date:** 2026-08-26 · **Status:** active
**Decision:** When the user disallows adjacent cells, the constraint is that no two
selected shapes touch or overlap, evaluated on the original QuPath geometry **before** any
smoothing or dilation the export path applies. No minimum-gap parameter.
**Why:** The raw segmentation is the ground truth about which cells are neighbours; a
constraint measured on shapes the app has already modified would depend on export settings
chosen later. No extra input to explain, and QuPath's expansion-based segmentation produces
exactly-touching cells, which this catches.
**Alternatives rejected:** A user-set minimum gap in µm — more control, another parameter
to explain, and no evidence yet that users want it. Consequence to keep in view: if a
dilation step is ever added, dilated neighbours could overlap even with this constraint on,
and the app would have to say so on screen.

## 014 — Phase 0 is gated on byte-identical XML
**Date:** 2026-08-26 · **Status:** active
**Decision:** Before the Phase 0 refactor, capture XML output from current `master` for
both demo GeoJSONs as golden files, and require the restructured code to reproduce them
byte-for-byte on the same inputs.
**Why:** Phase 0 moves the coordinate and export code with no intended behaviour change,
and there is no test suite (006/008). Byte equality is the strongest available assertion
that a pure refactor stayed pure, and it costs one afternoon rather than a test framework.
It is also the only check that would catch a silent change to the Y-flip, calibration
ordering or simplification.
**Alternatives rejected:** Relying on the manual UI pass alone — a human clicking through
the app cannot see that a coordinate shifted by a pixel.

## 015 — Replicates are spread and interleaved
**Date:** 2026-08-26 · **Status:** active
**Decision:** Confirmed by Jose. Replicates of a class each span the class's full spatial
extent and interleave with one another. The class is **not** partitioned into one spatial
block per replicate.
**Why:** It makes replicates statistical repeats of the same population rather than samples
of different regions, which is what a replicate is normally taken to mean. Regional
comparison remains expressible by the user as separate classes in QuPath.
**Alternatives rejected:** Spatial partitioning — appropriate if the question is regional
variation, but the wrong default and it silently confounds replicate with location.

## 016 — Spatial binning, not farthest-point, is the default selection algorithm
**Date:** 2026-08-26 · **Status:** active · **supersedes the algorithm named in 012**
**Decision:** The spread default is implemented as k-means spatial binning of cell
centroids, taking the cell nearest each bin centre; for *r* replicates, replicate *i* takes
the *i*-th nearest cell in each bin. 012's intent (spread by default) stands; its named
implementation (greedy farthest-point sampling) is replaced.
**Why:** Prototyped both on the 121 cells of `Single_cells.geojson`, selecting 20.
Farthest-point gave the best separation (min pairwise gap 101 px vs 26 px random) but
**biased selection to the tissue rim** — mean distance from the tissue edge 67 px against a
population mean of 78 px — because maximising separation means racing to the extremes. That
is a representativeness bug in a feature whose entire purpose is representativeness.
Binning gave min gap 33 px at depth 72 px: most of the separation, near-population-average
depth, visibly even coverage. It also produces 015's interleaved replicates by construction
(verified: 3 replicates × 12 bins → 12/12/12, zero overlap) instead of needing a second
mechanism.
**Alternatives rejected:** Farthest-point (edge bias, above). Random as default (unbiased
depth, but clumps — min gap 26 px, with visibly touching pairs among the 20 selected).
**Consequence:** binning does not by itself guarantee non-adjacency, so 013's constraint is
load-bearing and applies on top: if a bin's nearest candidate touches an already-selected
shape, take that bin's next-nearest.

## 017 — Live selection preview
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose's idea, adopted. The cell workflow draws the shapes as parameters are
set — unselected in grey, selected coloured by replicate, calibration triangle overlaid —
using a static matplotlib/geopandas render redrawn on each change. One plotting function
serves the Phase 2 class view, the Phase 4 selection preview and the export QC image,
replacing the separate `py-lmd` plot. Above a shape-count threshold it falls back to
plotting centroids instead of polygons. No interactive plotting library.
**Why:** It is the difference between choosing selection parameters and guessing at them,
and it is the most direct expression of the transparency goal in 003 — clumping, edge bias
or a starved replicate become visible rather than inferred. Jose asked not to do it if it
got complicated; measured, it does not: a two-layer render is ~0.35 s at 10k shapes, 1.8 s
at 50k, 7.6 s at 200k, and the centroid fallback is 0.14 s at 200k. Streamlit already
reruns on every widget change, so redraw-on-change needs no extra machinery.
**Alternatives rejected:** Interactive pan/zoom via plotly or pydeck — genuinely nicer, but
a new dependency plus browser memory risk on Community Cloud, for a benefit the static
render mostly already delivers. Revisit if users ask to zoom.

## 018 — Smoothing tolerance in µm, warned against shape size
**Date:** 2026-08-26 · **Status:** active · refines 010
**Decision:** Smoothing tolerance is specified in µm with a default of 0.5 µm, displayed as
a percentage of the median shape diameter, and warns when it exceeds roughly 2% of that.
No instrument-precision figure is required.
**Why:** 010 anchored the default to the cutting laser's precision, which needs a number
nobody had to hand. Anchoring to shape size instead is self-calibrating and better
targeted: the same 0.5 µm is negligible on a 200 µm mini-bulk annotation and material on a
10 µm cell, and the warning fires exactly in the case that matters. The underlying problem
010 identified is unchanged — `simplify(1)` is one *pixel*, so its physical effect scales
with magnification (0.35 µm at 0.347 µm/px, ~1.7 µm on a 4× overview) and the number
therefore means nothing physical on its own.
**Alternatives rejected:** Blocking on the LMD7 spec figure — would stall Phase 5 on a
detail the shape-relative warning handles better anyway.

## 019 — Smoothing default stays simplify(1) in pixels; explain and let users decide
**Date:** 2026-08-26 · **Status:** active · **supersedes 018, and the µm part of 010**
**Decision:** Jose's call. The simplification tolerance keeps its current value and unit —
shapely Douglas-Peucker, **1 pixel** — and is exposed with a plain explanation of what it
does. No µm conversion, no shape-relative warning threshold. The user decides.
**Why:** The default is battle-tested across 60+ users and changing it would alter output
for everyone for a theoretical gain. Explaining a parameter is the transparency this app
owes its users (003); tutoring them with a derived threshold is not. 018 was overthinking a
default that already works.
**Alternatives rejected:** 018's µm-with-warning scheme, and 010's µm framing — both
withdrawn. The observation behind them is still true (a pixel tolerance means different
physical distances at different magnifications) but it belongs in the on-screen
explanation, not in the units or in a warning.

## 020 — Multi-slide-into-one-plate deferred
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose is deferring the multiple-slides-into-one-plate question to think about.
Not designed for in the current phases; `ROADMAP.md` open question 5 keeps the context.
**Why:** It affects how well assignment is scoped (per file vs across files) and so is
better answered before Phase 3 hardens well assignment than retrofitted after — but it does
not block Phases 0–2.

## 021 — Phase 0 delivered: library split, CollectionPlan, CRS fix
**Date:** 2026-08-26 · **Status:** active
**Decision:** `core.py` and `utils.py` are deleted and replaced by `model.py`,
`geojson.py`, `plate.py`, `qc.py`, `export.py` and `extras.py`. Library functions take
explicit arguments and return report objects or raise domain exceptions; only
`streamlit_app.py` touches `st.*` and `st.session_state`. The legacy workflow runs through
`CollectionPlan`. Verified byte-identical XML and CSV across four cases per 014.
**Why:** `ROADMAP.md` Phase 0. The seam has to exist before a second workflow can hang off
it, and the `st.session_state` reads inside library code made anything untestable outside
Streamlit — the golden harness could only be written because the new functions are pure.
**Behaviour changes shipped alongside**, each a bug the refactor put in reach:
CRS mislabelling cleared; wells validated against the chosen plate rather than always 384;
plate-aware CSV filename; QC image and `classes.json` no longer written to the working
directory; unplaced surplus classes named rather than merely counted; plate layout sorted so
it is stable across reruns; `SawParseError` raised instead of silently returning `{}`; and
the MultiPolygon branch fixed, which would have raised `KeyError` for any user who had one.
`provenance.json` is now in the download bundle (009).
**Alternatives rejected:** Keeping `core.py`/`utils.py` and adding modules alongside — the
duplication would have to be unpicked later, and the point of Phase 0 is that Phase 1
inherits one clear structure. Indentation normalises to 4-space as a side effect of the
files being new, so the codebase is now internally consistent (`CLAUDE.md` rule 9).

## 022 — The golden harness lives in the repo and gating on it is mandatory
**Date:** 2026-08-26 · **Status:** active
**Decision:** The Phase 0 throwaway harness becomes `tools/golden_harness.py` with
reference output committed in `tools/golden/` (8 files, ~220 KB). `CLAUDE.md` rule 6 now
requires `check` to pass before committing any change that touches geometry, calibration,
well assignment or export. Re-blessing with `capture` is allowed only when output is meant
to change, must be stated in the commit message, and the files are never hand-edited.
**Why:** It is the only check that can see the failure mode that matters here — a
coordinate shifted by a pixel, an inverted Y flip, shapes added in a different order. All
of those leave the app looking perfectly healthy and cut the wrong tissue. Phase 4's
selection engine will change which shapes are chosen, so having a fixed reference for
*how* a chosen shape is rendered becomes more valuable, not less. Committing the reference
also means it traces to a specific version rather than to a scratchpad that gets cleaned.
**Verified both directions:** the committed goldens are byte-identical to output captured
from the pre-Phase-0 code, and replacing `ORIENTATION_TRANSFORM` with the identity matrix
makes all four XML comparisons differ and `check` exit 1. A gate that cannot go red is
worthless, so this was tested explicitly.
**Alternatives rejected:** Leaving it in the scratchpad — it would be gone next session and
the discipline would not survive. Reintroducing pytest to host it — reverses 006/008; the
harness is a script, and it can be moved under pytest later if a suite ever arrives.
**Known gaps, recorded rather than papered over:** no LineString case (no demo file has
one), nothing covering the UI or the QC/warning behaviour, and it proves "unchanged" rather
than "correct" — a pre-existing coordinate bug is faithfully preserved.

## 023 — Phase 1 delivered: router, shared steps, and the image-scale input
**Date:** 2026-08-26 · **Status:** active
**Decision:** `streamlit_app.py` becomes session init plus a router. Steps 1–3 (upload,
workflow choice, calibration) are shared; the router then dispatches to `ui_legacy.render`
or `ui_cells.render`. The `ui_*` modules are the UI layer and may own `st.session_state`;
the library modules stay pure. `CLAUDE.md` rule 5 is rewritten around that boundary,
replacing its stale references to `core.py` and `utils.py`.
**Why:** `ROADMAP.md` Phase 1. The legacy workflow keeps its order and wording so existing
users are not disoriented, while the second workflow gets somewhere to live.
**Verified:** golden harness clean — all 8 artefacts identical, so the legacy path is
untouched and now frozen. Router detection exercised against both demo files with stubbed
widgets: `Single_cells.geojson` (121 cells vs 7 annotations) defaults to cells,
`TD_01_verysmall_mIF.geojson` to legacy, no file to legacy with no hint.

## 024 — Step numbers are parameters, not literals
**Date:** 2026-08-26 · **Status:** active
**Decision:** The `ui_shared` step functions take a `step` label used in their heading,
rather than hard-coding "Step 2". The workflows pass their own numbers.
**Why:** The two workflows reach the shared steps at different points, so a literal is
wrong for one of them. The first attempt used "Step 1.5" and "Step 1.6" to avoid
renumbering, which read worse than simply renumbering: the flow is now 1–6 in both
workflows. Renumbering does shift what "Step 2" means for returning users, which is the
cost accepted here.
**Alternatives rejected:** Hard-coding numbers per workflow by duplicating the headings —
two places to keep in sync for no benefit.

## 025 — Image scale is asked for in the cell workflow, not globally
**Date:** 2026-08-26 · **Status:** active
**Decision:** `pixel_size_step` lives in `ui_shared` (both workflows may use it) but is
only called by the cell workflow. The legacy workflow does not ask for µm/px.
**Why:** 011 requires the value before any area figure is shown; the legacy workflow shows
no area figures, so requiring it there would be a new obstacle with no benefit for the
existing users. The roadmap listed it under "shared steps", which this satisfies as shared
*code* invoked where area actually matters.
**Alternatives rejected:** Asking globally — adds a required field to a workflow that does
not need it. Putting it only in `ui_cells` — Phase 3's area budgets and any future legacy
area reporting would want it back in the shared module.

## 026 — A file with no calibration points is readable, not rejected
**Date:** 2026-08-26 · **Status:** active · supersedes the `name`-column gate added in 021
**Decision:** `read_and_qc` no longer requires a `name` column. Its absence means the file
has no named point annotations, which is reported as "no calibration points" at the
calibration step with instructions for adding them in QuPath. Only a genuinely unusable
file — no features, or no `classification` column at all — still raises `GeojsonError`.
**Why:** Jose hit this with a real QuPath 0.7.0 export of 14145 segmented cells. QuPath
omits a property entirely when no object in the export carries it, so a file without
calibration points has no `name` column, and the app rejected it with "Export as a
FeatureCollection with named calibration points" — which the user *had* done. The message
named the wrong cause and blocked a readable file, against 003. Missing calibration points
is a real problem, but the fix is in QuPath and the app should say so precisely.
**Alternatives rejected:** Keeping the hard stop with a better message — the file loads
fine, and the user can still inspect classes and plate layouts while going back to QuPath
for the points.

## 027 — Multi-class QuPath objects become one combined class
**Date:** 2026-08-26 · **Status:** active
**Decision:** A QuPath object classified with several classes exports as
`{"names": ["Tumor", "Immune cells"]}` — plural. Those names are joined with `": "` into a
single class, mirroring how QuPath displays derived classes. The count and the resulting
names are warned about on screen, saying explicitly that such objects are *neither* of
their parent classes here.
**Why:** Found in the same export: 1130 of 8537 classified cells were multi-class, and the
app read them as `None`. That polluted the class list and then crashed the plate layout,
because `sorted()` cannot compare `None` with a string. Joining is the least surprising
repair — the class reads the same as it did in QuPath — and a double-positive cell is a
genuine biological category someone may well want in its own well. Silently folding them
into one parent class would misassign tissue.
**Alternatives rejected:** Dropping them with a warning — throws away real data the user
classified deliberately. Assigning them to the first parent class — silently wrong, and the
kind of wrong that only shows up in the mass spec. Asking the user per file — a dialog for
something QuPath already has a display convention for.
**Consequence:** anything with a classification but no usable name at all is now dropped
with its own count, so `classification_name` is never `None` downstream.

## 028 — Multi-class names are joined with `--`, sorted, and imply no hierarchy
**Date:** 2026-08-26 · **Status:** active · **supersedes the separator chosen in 027**
**Decision:** Jose's call. The multi-class separator is `--`, not `": "`. Class names are
also sorted before joining, so `["Tumor", "Immune cells"]` and `["Immune cells", "Tumor"]`
both give `Immune cells--Tumor`.
**Why:** `": "` reads as a hierarchy — parent and child — and a multi-class object has
neither. There can easily be four or more classes in a combination, where a colon-chained
name would be actively misleading. 027's reasoning (mirror QuPath's display) put fidelity
to QuPath above clarity for the user, which is the wrong trade for a name that decides which
well tissue lands in. Sorting follows from the same premise: if order carries no meaning,
two orderings must not produce two classes, or one biological category would silently split
across two wells.
**Cost accepted:** the class name is alphabetical rather than in QuPath's own order, so
`Tumor, Immune cells` in QuPath appears here as `Immune cells--Tumor`.
**Verified:** renaming shifts the class's alphabetical position, so its auto-assigned well
moves. Confirmed against the golden files that the **828 coordinate values are byte-identical**
and only the class-to-well mapping changed (B3 and B4 swapped). `multiclass_cells` goldens
re-blessed on that basis; the other four cases untouched.

## 029 — The pixel-size cross-check is opportunistic, never required
**Date:** 2026-08-26 · **Status:** active · refines 011
**Decision:** Nothing in the app requires QuPath `measurements`. Areas are computed from
shape geometry and the user's µm/px. When area measurements happen to be present, the
cross-check runs; when they are not, the app says so in a caption rather than a warning, and
does not suggest re-exporting.
**Why:** Jose pointed out that relying on users to tick "include measurements" is
unreliable — and the real 14145-cell export proves it, having none at all. So the absent
case is the *normal* case, and warning about it every time trains users to ignore warnings,
which is expensive in an app whose warnings are the safety mechanism (003). The cross-check
is a free bonus when the data allows it, not a prerequisite.
**Consequence for Phase 2:** per-class statistics must derive area from geometry × µm/px,
never from `Cell: Area`.

## 030 — Pixel size input: empty until typed, 4 decimals, step matched to format
**Date:** 2026-08-26 · **Status:** active
**Decision:** The µm/px input starts empty (`value=None`) and returns `None` until the user
types. It accepts 4 decimal places, with `step` set to `1e-4` to match `format="%.4f"`, and
a minimum of `1e-4` so zero is not enterable. `value=` is never re-passed on reruns.
**Why:** Jose reported the field snapping back to a different number after entry. Three
compounding causes, all mine: `value=` was re-seeded from `session_state` on every rerun
while the widget also had a `key=`, so the two fought; `step=0.01` was coarser than
`format="%.4f"`, and since Streamlit renders `step` as the HTML input's `step` attribute,
browsers snap off-grid entries to it — typing 0.3467 with a 0.01 grid gives 0.35; and the
`0.0` initial value had to be distinguished from a real entry by `if not entered`, which
also treats a legitimate 0 as absent.
**Cost accepted:** a pixel size with more than 4 decimals is rounded, e.g. 0.34675 becomes
0.3468. Four decimals is what QuPath reports for pixel width, so this is precise enough in
practice, and the field's help text states the limit rather than leaving it to be discovered.
**Alternatives rejected:** a free-text field with our own float parsing — accepts any
representation including scientific notation and cannot snap, but gives up the numeric
keyboard, the arrows and range validation for a problem the matched step already solves.
Worth revisiting if snapping is ever reported again, since it is the only option that removes
the browser from the equation entirely.

## 031 — Missing or degenerate calibration points are hard stops
**Date:** 2026-08-26 · **Status:** active · supersedes the warning chosen in 026
**Decision:** Jose's call. The calibration step calls `st.error` and `st.stop()` when the
file has fewer than three calibration points. Extended to a second case found while
implementing it: three points that do not form a triangle, because one is repeated or all
three are collinear. `qc.TriangleReport.is_degenerate` reports it, the UI blocks on it.
**Why:** 026 made a missing-calibration file merely warn, on the reasoning that the file is
readable and the user could still look around. Jose overruled that, and correctly — nothing
downstream can produce a valid collection without three points, so letting the user proceed
only defers the failure to a worse place. This is the "continuing cannot produce a
meaningful result" case that 003 reserves `st.stop()` for.
The degenerate case is worse than missing points and was found by testing rather than
assumed: **py-lmd accepts three identical or collinear calibration points and writes a
perfectly well-formed XML** — no exception, no NaN. The user gets a file that looks correct,
loads in the LMD software, and cuts in the wrong place. Nothing downstream would catch it,
so this is the one place it can be caught.
**Cost accepted:** stopping at the calibration step makes the Extras section below it
unreachable while a file without calibration points is loaded. Removing the file restores
it. Worth revisiting by moving Extras above the workflow if anyone is bitten.
**Verified:** six cases — zero, one and two calibration points, three identical, three
collinear, and three valid — with only the valid case proceeding.

## 032 — Phase 2 delivered: per-class statistics and a class overview
**Date:** 2026-08-26 · **Status:** active
**Decision:** `stats.py` computes per-class shape counts, areas (total, median, quartiles,
min, max), convex-hull spread and density; `plot.py` draws the shapes with the chosen
classes coloured and the rest grey. The cell workflow shows the table, an optional
area-floor count, a class multiselect defaulting to everything, and the overview figure.
**Why:** `ROADMAP.md` Phase 2 — a user cannot sensibly choose budgets without seeing what
each class actually holds. Confirms 029 in practice: areas derived from geometry × µm/px
match QuPath's own `Cell: Area` to a median ratio of 0.9998 over 121 cells, so nothing
depends on measurements being exported.
**Details worth keeping:** the area floor **counts** small shapes rather than removing them,
because a threshold for "too small to be worth collecting" is the scientist's judgement, not
ours (003). Density uses the hull of centroids rather than of full geometries — far cheaper,
and it degrades to `NaN` instead of infinity for classes with fewer than three shapes.
Excluded classes are drawn grey rather than hidden, so what is being left out stays visible.
**Alternatives rejected:** filtering by the area floor automatically — silently drops data
the user classified deliberately. Hiding excluded classes — makes an exclusion invisible at
exactly the moment it matters.

## 033 — Plotting uses Figure, not pyplot, and Okabe-Ito colours
**Date:** 2026-08-26 · **Status:** active
**Decision:** `plot.py` constructs `matplotlib.figure.Figure` objects directly and never
imports `pyplot`. Qualitative colours come from the Okabe-Ito palette, assigned by sorted
class name. Above 20 000 shapes, one dot is drawn per shape instead of its outline.
**Why:** pyplot keeps every figure in a global registry, and Streamlit reruns the whole
script on every widget change, so a pyplot-based preview would leak figures for the lifetime
of the session — on a free-tier deployment with a memory ceiling that matters. Okabe-Ito
stays distinguishable under the common forms of colour blindness, which a diagnostic picture
of which tissue gets cut ought to be. Sorting the assignment means a class does not change
colour when the selection changes. The dot fallback is measured, not guessed: polygon
rendering is ~1.8 s at 50 000 shapes and ~7.6 s at 200 000, against 0.14 s for centroids.
**Alternatives rejected:** an interactive plotting library (see 017) — still deferred.
Matplotlib's default tab10 — not colourblind-safe.

## 034 — Phase 2 statistics table: µm² throughout, std dev, no density, no area floor
**Date:** 2026-08-26 · **Status:** active · revises 032
**Decision:** Jose's review of the Phase 2 table. Total area is reported in **µm²**, not mm².
The convex-hull "Spread" column is replaced by the **standard deviation of shape area**. The
**density** column is removed. The optional area-floor count is **removed** entirely.
**Why:** every other column was already in µm², so mm² for the total meant reading two units
in one row. Standard deviation belongs with the median and quartiles as a dispersion measure
of the same quantity, where the hull spread was answering a different question nobody had
asked. Density followed the hull and went with it. The area floor was a global threshold
bolted onto a per-class table, and a minimum shape area is really a *selection criterion* —
it belongs with the per-class replicate and budget inputs in Phase 3, not here.
**Consequence:** `_extent_mm2` and its shapely hull machinery are gone, which also removes
the only part of `stats.py` that was more than a groupby.

## 035 — The plot legend sits outside the axes
**Date:** 2026-08-26 · **Status:** active
**Decision:** `plot.plot_shapes` places the legend with
`figure.legend(loc="outside right upper")` rather than inside the axes, and the default
figure is wider than tall to give it a column.
**Why:** Jose reported the legend overlapping the figure. An inside legend covers tissue,
and the tissue is the entire point of the picture — for a class with shapes in the top-right
corner the legend would hide exactly what the user is trying to judge. The `outside ...`
locations require constrained layout, which the figure already uses.

## 036 — Areas are displayed to two decimal places
**Date:** 2026-08-26 · **Status:** active
**Decision:** `stats.for_display` rounds every area column to `stats.DECIMALS` (2) decimal
places and the UI formats them `%.2f`. Shape counts are left exact. The underlying frame
keeps full precision; only the display is trimmed, and the columns stay numeric so the table
still sorts numerically.
**Why:** the raw values carry a long float tail — `147479.0769032064 µm²` — which reads as
precision that segmentation boundaries and a hand-entered pixel size cannot support.
Two decimals is enough to distinguish any two shapes anyone cares about.
**Note:** I first implemented this as two *significant* figures, which turned 147479.08 into
150000 — far coarser than intended and a real loss of information on totals. Jose clarified
he meant decimals. Recorded because the two readings of "fewer sig figs" differ by orders of
magnitude, and the wrong one silently destroys data in a table people make decisions from.

## 037 — A magnification reference table beside the pixel size input
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose's request. The µm/px step shows a reference table to the right of the
input: objectives 4×–63× against two representative camera sensor pitches (3.45 and 6.5 µm),
each cell computed as `pitch / magnification`. A warning next to it states that magnification
does not determine pixel size.
**Why:** users often know their objective but not their scale, and would otherwise guess or
abandon the step. The table is deliberately built from the formula with two pitches rather
than quoting one "typical" value per magnification, because the whole point is that the same
20× objective spans 0.173–0.325 µm/px across common cameras — a 1.9× spread visible in the
table itself. A single authoritative-looking number per magnification would invite exactly
the mistake the warning is trying to prevent, and would be a claim about instruments we have
no basis for.
**Alternatives rejected:** a single "typical µm/px" column — looks authoritative, is wrong
for most microscopes, and a 2× error in pixel size is a 4× error in every area budget.
Auto-filling the input from a chosen magnification — 011 and 029 already settled that the
user enters this value deliberately; prefilling it from a guess undermines that.

## 038 — Pixel size is optional; only area-based features require it
**Date:** 2026-08-26 · **Status:** active · constrains 011, 029, 037
**Decision:** Jose's guidance. Some users will budget purely by number of cells and have no
interest in µm/px. The app nudges them toward entering it but must never require it. Missing
pixel size disables area figures and area-based budgets, with the reason stated; everything
expressible in cell counts stays fully available.
**Why:** 003, applied to a case the earlier pixel-size decisions did not consider. "Collect
200 cells of this class" is a complete, valid experimental request that needs no scale at
all, so demanding a number the user may not have would block legitimate work — and invite
them to type a plausible-looking guess, which is worse than leaving it blank.
**Consequence:** Phase 2's class table shows counts alone until a scale is entered, and
Phase 3 offers the cell-count budget mode unconditionally while gating the area mode. Any
later phase adding an area-based feature must degrade the same way.

## 039 — Phase 3 delivered: replicates, budgets and feasibility
**Date:** 2026-08-26 · **Status:** active
**Decision:** `budget.py` holds `BudgetMode` (cells or area), `ClassBudget`, `feasibility`
and `total_groups`. The cell workflow gains step 6 — a per-class editor for replicates and
per-replicate amount, with a feasibility table — and step 7, plate settings plus a
well-capacity check.
**Why:** `ROADMAP.md` Phase 3. Feasibility is shown *before* any selection runs, because
this is where an experiment goes wrong: asking three replicates of 500 cells from a class
holding 1130 is a decision to make knowingly, not to discover afterwards.
**Details worth keeping:** the editor defaults to the whole class in one replicate — what the
annotations workflow would do — so the starting point is neutral rather than an invented
number. The feasibility table reports **how many whole replicates each class can fill**,
which is the actionable figure rather than a raw shortfall. An area budget without a pixel
size raises `KeyError` from `budget.feasibility` rather than being quietly skipped, so the
failure surfaces in the library where it can be tested. The `data_editor` key is an md5 of
the class selection plus mode, because a fixed key leaves stale rows behind when the
selection changes.
**Alternatives rejected:** per-class budget modes — mixing cells and area across classes in
one plan is harder to reason about than it is useful; revisit if asked. Blocking on
infeasible budgets — 003, and a partly-filled replicate is sometimes exactly what the user
wants.

## 040 — Spread uses a regular grid, not k-means
**Date:** 2026-08-26 · **Status:** active · **supersedes the implementation in 016**
**Decision:** Spatial spread is implemented by binning centroids on a regular grid whose cell
size is binary-searched so the occupied-cell count lands near the number of shapes wanted,
then taking the shape nearest each bin centre. 016's intent — spread by default, replicates
interleaved via bin offsets — is unchanged; k-means is replaced.
**Why:** measured on 4214 real centroids, `scipy.cluster.vq.kmeans2` costs 0.03 s at k=100,
0.8 s at k=500, **14 s at k=2000 and 62 s at k=4000**. Streamlit reruns the whole script on
every widget change, so a user asking for 2000 cells per replicate would wait 14 s per
keystroke. The grid is ~0.03 s at any k *and* separates better: min pairwise gap 118 px
against k-means' 82 px at k=100.
**Trade-off, measured and accepted:** the grid is more edge-biased than k-means at small k —
mean distance from the tissue edge 328 px against k-means' 396 px, with a population mean of
450 px. Some of that is inherent to any spatially uniform sample of an interior-dense
population, and the honest answer for a user who wants population-proportional sampling is
the random mode, which is offered alongside and is unbiased by construction (depth 454 px).
Documenting both is better than pretending one mode dominates.
**Alternatives rejected:** k-means (above). A Hilbert-curve stride — comparable spread and
speed, but "we lay a grid over the tissue and take one shape per square" is explainable to a
user in a sentence, and this app's warnings and choices have to be legible to be useful.

## 041 — Phase 4 delivered: selection engine, preview, and a complete cell workflow
**Date:** 2026-08-26 · **Status:** active
**Decision:** `selection.py` chooses shapes; `model.plan_from_selection` turns the result into
a `CollectionPlan` with one well per `class_r<replicate>` group; step 8 shows the selection
parameters, the achieved-versus-requested table and a preview coloured by replicate, then
hands off to the existing shared export step. The cell workflow now produces a downloadable
collection.
**Why:** `ROADMAP.md` Phase 4. The preview is the point (017): a user can see clumping, a
starved replicate or edge bias immediately rather than inferring it from numbers.
**Details worth keeping:** filling is round-robin across bins until the budget is met, which
serves count and area budgets with one loop instead of two code paths. Adjacency is enforced
**globally** rather than per replicate, because the laser cuts a shared boundary regardless of
which well each cell goes to. Unselected shapes stay in the plan as `skipped` so the app can
say what it left out. The preview colours by replicate and merges classes, since the question
at that step is whether the replicates are spread and comparable — the class overview in step
5 already answered the other question.
**Verified end to end on the real 8537-shape export:** 19 checks including exact count
budgets, zero touching pairs under the adjacency constraint with a control proving touching
pairs occur without it, area budgets overshooting under 5%, seed determinism, and a real XML
build of 900 shapes and 7863 vertices.

## 042 — Bins size the whole class budget, and adjacency is a preference
**Date:** 2026-08-26 · **Status:** active · **supersedes the replicate scheme in 015/016/040**
**Decision:** Jose's review of Phase 4. Three changes.
1. The grid is sized to **one bin per shape in the whole class budget**, not per replicate,
   and one shape is taken per bin. Replicates are then dealt from that spread set in a
   spatially shuffled order.
2. The adjacency setting is a **strong preference, not an obligation**. Conflicting
   candidates are deferred and used only when the non-conflicting ones run out.
3. The number of collected shapes touching another collected shape is **always reported**, per
   replicate, whether or not the preference is on.
**Why:** the previous scheme binned per replicate and gave replicate *i* the *i*-th nearest
shape to each bin centre. That is co-location, not interleaving: measured on the real export,
**100% of collected shapes had their nearest collected neighbour in a different replicate**,
with a 12 px minimum gap. Jose spotted it in the preview. Sizing bins to the whole budget
fixes it at the root — median nearest-neighbour distance is now 104 px, 2.2× better than
random — while replicates stay interleaved (centroid spread 5.6% of extent against a
shuffled-label null of 5.8%, i.e. indistinguishable from random assignment).
On adjacency: in dense tissue a large budget cannot always be filled without touching, and
silently under-delivering is worse than touching — the user asked for an amount. So the
constraint relaxes and reports instead. Reporting unconditionally matters because a user who
leaves the preference off still needs to know how much of their collection shares boundaries.
**On the graph question:** the touching graph *is* what drives this — `shapely.STRtree` with
an `intersects` predicate, consulted per candidate. What is deliberately not done is solving
for a maximum independent set: that is NP-hard, and greedy rejection over a spread-ordered
stream already reaches zero conflicts whenever the budget admits one, verified on a chain
graph where the optimum is known exactly.
**Verified:** 22 checks, including a 20-square touching chain with a known optimum of 10 —
asking for 10 yields 0 conflicts, asking for 12 still delivers 12 and reports 8 conflicts.

## 043 — The cell workflow shows the plate layout
**Date:** 2026-08-26 · **Status:** active
**Decision:** `ui_shared.plate_preview` renders the plate as a table with each group in its
well, plus a download of the samples-and-wells scheme. The cell workflow calls it after well
assignment; it replaces the raw dictionary in an expander.
**Why:** Jose noticed the plate view present in the annotations workflow was missing from the
cell workflow — an oversight in Phase 3/4, not a decision. The plate is how a user checks the
layout against what they will physically handle, so a dictionary dump is not a substitute.

## 044 — Neighbours are shapes within a distance, not shapes that intersect
**Date:** 2026-08-26 · **Status:** active · **supersedes the intersects test in 013/042**
**Decision:** Adjacency uses `shapely.STRtree.query(..., predicate="dwithin", distance=d)`
with a user-adjustable `d`, defaulting to 1 pixel, rather than a strict `intersects` test.
Zero reverts to strict intersection.
**Why:** Jose reported that no shapes were being counted as touching while the preview
clearly showed many adjacent ones. Not a counting bug — the wrong predicate for the physical
question. **QuPath's cell segmentation leaves a sub-pixel gap between adjacent cells**:
measured on the real 8537-cell export, the median boundary-to-boundary gap to the nearest
neighbour is 0.57 px (p95 0.87 px) and only 4% of cells actually intersect. So `intersects`
found 350 pairs where a 1 px tolerance finds 26 336, involving 8213 of the 8537 shapes. Cells
that are adjacent in every sense that matters for cutting were invisible to the check, which
made both the constraint and its report meaningless.
**Why exposed rather than fixed:** the right tolerance depends on the segmentation and on how
much boundary sharing the user will accept, and it is cheap to explain — "shapes closer than
this count as neighbours". The µm equivalent is shown when the image scale is known. This
follows 019: expose the number, explain it, let the user decide.
**Verified:** at 1 px, 900 of 3193 Tumor cells selects with 0 conflicts while 2700 of 3193
reports 2206 — unavoidable at 85% of a dense class, and now visible instead of hidden.
Cost 0.09 s over 8537 shapes.

## 045 — One plate menu and one plate renderer for both workflows
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose's request. `ui_shared.plate_settings_step` is the only place plate options
live — type, margin, row spacing, column spacing, randomize — and `ui_shared.plate_preview` is
the only plate renderer. Both workflows use both. The randomize toggle moves out of the
annotations-only layout step into the shared options. The cell workflow shows its plate
**directly under the plate options**, because the well assignment depends only on the budgets
(`budget.group_keys`), not on which shapes the selection picks.
**Why:** the two workflows had drifted — the cell workflow had a duplicate step heading, no
randomize toggle, and showed its plate at the end of the *next* step. None of that was
intended; it accumulated across Phases 3 and 4. A user should not have to learn two plate
interfaces because of an implementation detail.
**The one difference kept, and why:** the annotations workflow retains its **Confirm** button
and custom samples-and-wells upload. There the user maps classes to wells themselves, and the
upload is an established escape hatch for schemes the plate builder cannot express. The cell
workflow derives `class_r<replicate>` groups from the budgets and assigns them automatically,
so there is nothing to confirm and nothing the builder cannot express. If a cell-workflow user
ever needs to hand-place groups, the upload path should be added there too rather than the
confirm gate.
**Also:** `plate.assign_wells` is seeded, so a randomized layout is reproducible — closing the
unseeded-randomize gap that had been recorded as a known quirk. `plan_from_selection` now
accepts the assignment already shown to the user, so a replicate that ends up with no shapes
keeps its well rather than quietly disappearing from the plate.

## 046 — Phase 5 delivered: smoothing tolerance and cutting order
**Date:** 2026-08-26 · **Status:** active
**Decision:** The shared export step exposes two parameters for both workflows: the
simplification tolerance in pixels (default 1.0, as 019 settled) and the cutting order
(`none` / `grouped` / `hilbert`, default `none`). Defaults reproduce today's output exactly,
which the golden files verify.
**Why:** `ROADMAP.md` Phase 5. Both parameters were previously hard-coded, and the order one
turned out to matter more than expected — see below.
**What the investigation found:** writing shapes in load order makes the LMD move its
collector **759 times for 900 shapes across 9 wells**, where 8 movements would do; the XML had
760 contiguous cap runs against a possible 9. That is a real instrument cost that has always
been there. Grouping by well fixes the movements but slightly lengthens stage travel
(392 877 px vs 346 038 px) because it ignores position within a well; hilbert fixes both,
reaching 213 295 px — 62% of unordered — with 8 movements.
**Why the default is still `none`:** the legacy workflow is frozen, and reordering changes
every existing user's XML. The improvement is offered, explained, and reported with
before-and-after numbers so the choice is informed — which is 003 applied to a performance
question rather than a safety one. If Jose would rather make grouping the default, that is a
one-line change plus a golden re-bless.
**Why hilbert and not greedy:** py-lmd's `tsp_greedy_solve` lazily imports `umap`
(`lmd/segmentation.py:120`) and umap-learn is not a py-lmd dependency, so it raises
`ModuleNotFoundError`. Adding umap-learn would pull numba and scikit-learn onto a free-tier
deployment for a solver that loses to hilbert on this data. Hilbert order 7 is used, per
py-lmd's own guidance for whole-slide areas; it is not exposed, because it is a knob whose
meaning is hard to convey and 7 was best at every size measured.
**Verified:** 18 checks — every mode is a permutation of all shapes, grouping yields exactly
one movement per well, hilbert beats both, reordering never moves a shape to a different well,
the XML holds the same shapes in a different order, tolerance trades vertices as expected, and
both parameters reach `provenance.json`.

## 047 — The cutting order defaults to the best option, and greedy is offered
**Date:** 2026-08-26 · **Status:** active · **supersedes the default chosen in 046**
**Decision:** Jose's call, on the grounds that stage movement between shapes is a leading
cause of cutting misalignment. Two changes:
1. The default cutting order is **`GREEDY`** — grouped by well with the path shortened inside
   each well — not `NONE`. Best available, not historical.
2. `umap-learn` is added as a dependency so py-lmd's `tsp_greedy_solve` can be offered at all.
**Why greedy over hilbert:** measured on 900 real shapes across 9 wells, greedy gives
197 563 px of stage travel against hilbert's 213 295 px and an unordered 346 038 px — 57% vs
62% — and is faster (0.41 s vs 0.69 s per collection). Both collapse collector movements from
759 to 8.
**Why the dependency is acceptable:** it adds umap-learn, pynndescent, scikit-learn, joblib and
threadpoolctl; numba, the heaviest transitive piece, was already required by py-lmd. While
regenerating `requirements.txt` it became clear `pytest` was duplicated into the runtime
dependencies as well as the dev group, so it was removed from runtime — the deployed footprint
therefore grows by five packages and shrinks by three.
**Consequence, stated plainly:** this changes the XML for every existing user. Verified across
all four golden cases that the **coordinate multiset and the well assignment of every shape are
unchanged** — only the order differs, and the contiguous cap runs collapse to exactly the
number of distinct wells. Goldens re-blessed on that evidence (`CLAUDE.md` rule 6).
**Known limit:** with roughly one shape per well — the exploded single-cell case — travel is
dominated by the order wells are visited in, and `cells_exploded` gets 1% *longer*
(30 755→31 066 px) while its collector movements still drop 126→123. Wells are visited in plate
order because that minimises collector travel, which is the movement that matters there.
Optimising the well visiting order against tissue positions would trade collector travel for
stage travel; not attempted.
**Also:** greedy's first call in a process costs ~14.7 s of numba compilation, then 0.1–0.6 s.
It only runs on a button press and the help text says so.

## 048 — Exclusions are reported by cause, not by count
**Date:** 2026-08-26 · **Status:** active
**Decision:** `CollectionPlan` gains two properties that split what `skipped` used to conflate.
`not_selected` is shapes with no group at all; `unplaced` is shapes that belong to a group whose
group got no well. The cell workflow states `not_selected` in a caption and the annotations
workflow warns about it; `unplaced` warns in both. `plan_from_class_wells` now assigns a
`group_key` only to classes present in the samples-and-wells scheme, so the two workflows
classify exclusions identically.
**Why:** Jose hit "6988 of 8537 shapes have no well and will not be cut" on a normal cell
collection. In the annotations workflow that message means a class is missing from the scheme,
which is worth a warning. In the cell workflow it means the shapes were not selected — which is
the entire purpose of the workflow, so the warning fired on every single collection. Warning
about the intended outcome is how a warning stops being read, and this app's warnings are its
safety mechanism (003).
**What genuinely warrants a warning in the cell workflow** is a shape the user *asked* to
collect that will not be cut anyway because its group ran out of wells. That is now the
`unplaced` case, and it replaced a duplicate check that lived in `ui_cells`.
**Verified:** no change to the export — goldens identical — and 9 checks covering both
classifications in both workflows.

## 049 — The cell workflow needs no plate confirmation
**Date:** 2026-08-26 · **Status:** active · records what 045 left implicit
**Decision:** Confirmed by testing rather than changed. The cell workflow has no Confirm step
because the well assignment is recomputed from the current plate settings on every rerun; a
change to plate type, margin, spacing or the randomize toggle takes effect immediately, and the
plate table directly under the options always shows what will actually be used.
**Why recorded:** Jose asked whether a plate change he made had been tracked, which means the
absence of a confirmation step reads as an absence of feedback. The behaviour is correct — the
same settings re-derive the same assignment, and a changed setting propagates into the plan's
wells, both now asserted — so the answer is that the plate table *is* the confirmation. If it
still reads as ambiguous, the fix is a clearer statement next to the table rather than a
Confirm button, which would only add a step that can be forgotten.

## 050 — Phase 6: cache the rerun path, narrow the measurement parsing
**Date:** 2026-08-27 · **Status:** active
**Decision:** `ROADMAP.md` Phase 6, which said to profile before designing. Three changes came
out of the measurements, and one deliberate non-change.
1. `geojson.area_measurements` reads only QuPath's area field instead of building a frame of all
   ~100 measurements to get one column: 0.28 s and a 170 MB transient down to 0.16 s and no
   measurable allocation, with identical results (0.6535 µm/px over 8537 objects, 0.66% spread).
2. `selection.select`, `class_statistics` and `pixel_size_qc` are cached in the UI layer. Streamlit
   re-executes the whole script on every widget change, and selecting from 150 000 shapes costs
   1.40 s — without caching, nudging any unrelated control re-ran the entire selection.
   `class_statistics` was additionally being computed twice per rerun.
3. Cache keys are explicit tuples, never the GeoDataFrame: Streamlit would hash 150 000 rows on
   every rerun, costing what the cache saves. The shape fingerprint is
   `(file name, row count, sorted class names)`; the class names are load-bearing because
   exploding a class rewrites them in place, so a filename alone would serve a stale selection.
**The non-change:** nothing breaks at 150 000 shapes — 1.94 s uncached per rerun, 15 s per export,
936 MB. No downsampling, streaming or chunking was added, because the profile does not justify it
and speculative machinery would be harder to reason about than the problem it solves.
**What the profile revealed that was not expected:** the footprint is libraries, not data. The
83.7 MB GeoJSON adds 77 MB; the frame is 39 MB. The largest single item is the numba/umap JIT
behind the greedy solver — about **354 MB, paid once per process on the first export**, against
33 MB for hilbert. That is a consequence of 047 worth Jose knowing about, since it lands on a
free-tier deployment; it is recorded rather than acted on, because the default is his call.
**Verified:** 6 checks that the caches hit on repeated reruns and invalidate on a changed seed,
changed replicates, an exploded class and a different file. All other suites and the golden
harness unaffected.

## 051 — Scale guardrail, and the optimisations the research justified
**Date:** 2026-08-27 · **Status:** active
**Decision:** After researching Community Cloud's limits and profiling a genuine 1 000 000-cell
export, four changes and one refusal.
1. **Columns nothing reads are dropped at load** — `measurements`, `name`, `isLocked`. The
   implied pixel size is derived from `measurements` once during the read and kept in the
   report, so `qc.compare_pixel_size` became pure arithmetic: 0.28 s and a 170 MB transient per
   rerun down to nothing. At a million shapes the drop frees 107 MB of a 383 MB frame.
2. **The plan builders copy only the columns a plan needs**, not the whole frame — a full copy
   cost 99 MB at a million shapes.
3. **Step 8 is an `st.fragment`**, so changing the selection mode, seed or neighbour distance
   re-runs only steps 8–9. The export sits inside the fragment, so nothing downstream can show
   a stale selection.
4. **A warning after load above 40 000 shapes** — about the most a single TMA core yields —
   carrying the measured timings and memory, and instructions for running locally.
**Refused:** making the processed-GeoJSON re-export optional, which was the largest remaining
cost at 27.9 s for a million shapes. Jose: that re-export is critical. It stays.
**Why the guardrail is a warning and not a block:** 003. A million cells is a bad idea on the
hosted app, not an impossible one, and a user who understands the trade-off may still want it.
**What the research established** (documented Feb 2024, subject to change): Community Cloud
gives 0.078–2 CPU cores, **690 MB guaranteed to 2.7 GB maximum**, 50 GB storage; apps sleep
after 12 hours; and **every dependency is reinstalled on each reboot** with no documented wheel
cache, so each package added lengthens every cold start.
**Measured outcome:** the million-shape peak fell from 2 689 MB to **2 207 MB** and
`build_collection` from 7.6 s to 1.1 s. A million cells now fits under the ceiling with headroom
instead of sitting on it — while remaining slow enough that the guardrail is still right.

## 052 — Hilbert is the default again; greedy stays available
**Date:** 2026-08-27 · **Status:** active · **supersedes the default chosen in 047**
**Decision:** Jose's call: "354 MB is not worth 8% shorter travel. User experience wins here."
`DEFAULT_PATH_ORDER` is `HILBERT`. Greedy remains selectable, and `umap-learn` stays a
dependency so it works.
**Why:** greedy's first call costs about 354 MB of numba/umap JIT and roughly 15 seconds, against
33 MB and a fraction of a second for hilbert, in exchange for ~8% shorter stage travel
(197 563 px vs 213 295 px on 900 shapes). On a deployment capped at 2.7 GB that reinstalls every
dependency on each reboot, the memory and the cold start cost more than the travel saves. Both
still collapse collector movements from 759 to 8, which was the large win in 047 and is
unaffected.
**Cost accepted:** keeping greedy selectable keeps umap-learn, pynndescent and scikit-learn in
`requirements.txt`, so every cold boot still installs them even for users who never pick it. If
cold starts become the complaint, removing greedy entirely is the next step — but that is a
different decision from which default to ship.
**Verified:** goldens re-blessed after confirming, as in 047, that the coordinate multiset and
every shape's well are unchanged and only the order differs. The Phase 5 check now asserts the
*intent* — the default shortens the path and is not the JIT-heavy solver — rather than pinning a
particular value.

## 053 — Greedy is removed entirely, not just un-defaulted
**Date:** 2026-08-27 · **Status:** active · **supersedes 047's dependency addition and 052**
**Decision:** Jose's call: hilbert is enough. `umap-learn` is removed from `pyproject.toml` and
`requirements.txt`, and `PathOrder.GREEDY` is removed from the enum and the dropdown.
**Why:** 052 kept greedy selectable, which meant every Community Cloud cold boot still installed
umap-learn, pynndescent, scikit-learn, joblib and threadpoolctl — five packages, reinstalled on
every reboot with no wheel cache — for an option almost nobody would pick once it was no longer
the default. The 8% shorter travel never justified that.
**Why removed rather than hidden:** with the dependency gone, a `GREEDY` member left in the enum
would be a code path that raises `ModuleNotFoundError` if anything ever reached it — a landmine
for whoever next reads the file. Removing the member makes the absence checkable, and the Phase 5
harness now asserts both that `PathOrder` has no `GREEDY` and that `umap` will not import.
**Measured outcome:** five packages out of the deployed requirements; the real export's peak fell
to **392 MB**, less than half the 767 MB before Phase 6 began. Goldens unchanged, because hilbert
was already the default from 052.
**What was given up:** about 8% of stage travel against greedy. Hilbert still delivers the large
win — 62% of the unordered path length and 8 collector movements instead of 759.

## 054 — Round two planned: five PRs, tests first
**Date:** 2026-08-27 · **Status:** active
**Decision:** Jose's six usability items are planned as five PRs in `ROADMAP.md` section 5, in
this order: test suite, nomenclature, estimated pixel size, minimum-area filter, plate control.
**Why this order:** PR 2 is a wide mechanical rename and PR 3–4 change how amounts are computed.
Both are far safer with tests underneath, so the suite comes first. PR 4 depends on PR 3 because
a 150 µm² default is meaningless without a scale, and requiring every user to type one before the
default filter works would be a step backwards.
**Why the items were grouped as they were:** start well and editable plate are one PR because
both answer "how does a class reach a well". Nomenclature is alone because a rename that touches
every file should not share a diff with behaviour changes.
**Alternatives rejected:** doing the rename first, unprotected — cheaper in total work, but the
golden harness only covers output bytes, and a missed rename in a UI string or a docstring would
go unnoticed. Deferring tests to last, after the features — leaves the riskiest changes
unverified for the longest.

## 055 — Drag-and-drop is not worth a custom frontend; edit the plate instead
**Date:** 2026-08-27 · **Status:** active
**Decision:** No drag-and-drop of classes into wells. The plate becomes **editable in place**
via `st.data_editor` with a `SelectboxColumn` per well, so each cell offers a dropdown of the
user's classes.
**Why:** Streamlit has no native drag-and-drop into a grid. `streamlit-sortables` (pip-installable,
no build step, Apache 2.0, ~137 stars) does support multi-container drag, but its model is items
between a handful of named buckets — with 384 wells as drop targets it would be unusable. A genuine
plate drag-and-drop means a custom React component: a build step, a frontend to maintain, and more
weight on a deployment `decisions.md` 053 just spent a PR slimming.
**Why the editable plate is arguably better anyway:** a dropdown of existing classes cannot produce
a typo or name a class that does not exist, which dragging can. It is direct manipulation of the
real plate grid with no new dependency.
**Alternatives rejected:** `streamlit-sortables` for a coarser "drag classes into groups" step —
kept as a fallback if the editable plate proves insufficient, to be decided on evidence.

## 056 — Estimating the pixel size will become the default, not the cross-check
**Date:** 2026-08-27 · **Status:** planned · **will supersede 011**
**Decision:** Planned for PR 3. Where the file allows an estimate, the app uses it and offers an
override behind a small expander; where it does not, it asks as it does today.
**Why:** 011 chose explicit entry so that nobody accepts a derived number unread. Measured across
every file where it could be computed, the estimate has been right — median ratio 0.9998 on
`Single_cells.geojson`, spread 0.23–0.66% — while the input is the step users stumble on.
**Why it cannot simply be removed, which was Jose's question:** the estimate needs QuPath area
measurements, and those are only present if the user ticked the box on export. The first
`Exemplar001` export had none at all, and annotation-only files never will. So the input has to
survive as the fallback; what changes is that it stops being the default path.
**Recorded now because** it is a reversal of a decision Jose made deliberately, and the reasoning
should be visible before the code changes rather than after.

## 057 — Refinements to the round-two plan
**Date:** 2026-08-27 · **Status:** active · refines 054, 056
**Decision:** Three of Jose's corrections to the plan, recorded before any of it is built.
1. **The pixel size input moves next to the area budget control** rather than being its own step.
   Users were confused about why the app wanted it; beside "budget by area" it explains itself,
   because that is the only thing it feeds. Small input, help icon for the longer explanation.
   The cell workflow loses a step as a result.
2. **The minimum area default is 100 µm², not 150** — Jose's figure, on the grounds that
   collecting less than that reliably is very difficult.
3. **The minimum area filter applies before anything is measured.** Statistics, feasibility and
   selection all work on the filtered pool, so "available area" means *collectable* area. The
   opposite ordering would show a user an amount they cannot have, which is the class of error
   this app exists to prevent.
**Also settled:** CI is wanted, and its design is delegated — with the explicit requirement that
a failing test says plainly what is broken. And the well dropdowns are to be built and looked at,
not assumed: Jose is not convinced, and the fallback is the current read-only plate plus PR 5's
start well, which already covers the multi-slide case.

## 058 — A pytest suite and CI, replacing the scratchpad scripts
**Date:** 2026-08-27 · **Status:** active · **supersedes 006 and 008**
**Decision:** `tests/` holds 119 pytest tests covering the library layer, the UI behaviour that
protects a collection, and the golden gate. `.github/workflows/ci.yml` runs ruff, the suite and
the harness on every push and pull request. `CLAUDE.md` rule 6 now requires `uv run pytest` before
any commit.
**Why now:** 006 deferred this on the grounds that the code was too entangled with
`st.session_state` to test well, and 008 confirmed features came first. Neither holds any more —
since Phase 0 the library is pure, and round one accumulated nine verification scripts with ~130
assertions that lived in a session scratchpad and vanished with it. That was the wrong home for
the only checks this app had.
**Choices worth recording:**
- **`conftest.py` forces `MPLBACKEND=Agg`** before anything imports pyplot. Without it a plain
  `pytest` hangs, because py-lmd's `Collection.plot` calls `plt.show()`. Documenting that as a
  required environment variable would have been a footgun; removing the need is better.
- **Assertion messages state what breaks in the app**, per Jose's request. The golden test
  extracts the harness's DIFFER lines and explains re-blessing rather than dumping subprocess
  output, because the useful line was otherwise buried under log noise.
- **Statistical properties are asserted against baselines**, never absolute thresholds — spread
  against a random draw, interleaving against shuffled labels over several seeds. A single draw
  compared with its own 95th percentile fails 5% of the time by construction, which Phase 4 had
  already paid for once.
- **Only committed files and generated fixtures.** CI cannot see the real 83.7 MB export, so
  nothing depends on it. Two fixtures encode quirks no committed file shows: a 20-square touching
  chain with a known optimum of 10, and a chain with 0.5 px gaps reproducing the sub-pixel
  separation real QuPath segmentation leaves.
**A test that was written and then rejected:** asserting umap-learn is gone by attempting
`import umap`. It passed in CI and failed locally, because a stale virtualenv keeps the package
after it is undeclared. The suite checks the declared dependencies and the source instead — an
environment-dependent test is worse than no test, because it teaches people to ignore red.
**Verified by breaking things on purpose:** inverting the Y flip fails two tests, one naming the
transform and one naming all five differing artefacts; setting the neighbour distance to zero
fails with an explanation of why strict intersection finds nothing on a real segmentation.

## 059 — One word per thing: "shape" is what the laser cuts
**Date:** 2026-08-27 · **Status:** active
**Decision:** Round two, PR 2. **shape** is the app's term for one cuttable outline, everywhere —
code, interface messages, docs. **object** is reserved for QuPath's vocabulary when discussing the
input file. **polygon** means the geometry type only. **contour** is not used at all. `GLOSSARY.md`
records these plus class, group, replicate, well, margin, collection, plan, calibration point,
pixel size, smoothing tolerance, cutting order and neighbour, and is linked from `README.md` and
`CLAUDE.md`.
**Why "shape" and not one of the others:** it is what py-lmd calls them (`new_shape`,
`Collection.shapes`), and py-lmd is what actually cuts; it was already the dominant term here; and
it leaves the other two words free for the distinct meanings they genuinely carry. "Object" cannot
be the app's term without colliding with QuPath's `objectType`, and "polygon" cannot without
colliding with `MultiPolygon` and `LineString`.
**What the survey found:** the rename was much smaller than planned. Of 39 uses of "object", almost
all were already correct — they describe QuPath objects during reading, before they become shapes.
Only two names were genuinely wrong (`POLYGON_LIMIT`, which counts shapes, and `plate_shape`, where
a plate is not something the laser cuts), plus an image caption and three README lines using a
**fourth** term nobody had mentioned: "contour". The app title was also ungrammatical — "Convert a
GeoJSON polygons for Laser Microdissection" — and now reads "Turn QuPath shapes into a Laser
Microdissection cutting file".
**Made enforceable rather than aspirational:** `tests/test_nomenclature.py` fails if "contour"
returns, if either old name comes back, if a canonical term is missing from the glossary, or if the
glossary stops being linked. Verified by reintroducing "contour" and watching it fail. A convention
that only lives in a document drifts back within a few PRs.
**Deliberately left alone:** `objectType` and every `Polygon`/`MultiPolygon` geometry check, because
those names come from QuPath and shapely and must match. Also `frame.shape`, which is pandas.
**Noted, not fixed:** `README.md` still describes only the annotations workflow and never mentions
cell segmentation. That is a real gap but it is a documentation rewrite, not a rename, and belongs
in its own PR.

## 060 — A per-class minimum collectable area, applied before anything is measured
**Date:** 2026-08-27 · **Status:** active · supersedes the global floor removed in 034
**Decision:** Each class gets a **minimum area in µm², default 100** — Jose's figure, on the
grounds that collecting less tissue than that reliably is very difficult. It is a **filter**, and
it runs **before** statistics, feasibility and selection.
**Why the ordering is the whole point:** Jose was explicit that the filter applies pre-measurement,
so the figures show available area *after* filtering. That makes "available" mean *collectable*.
The opposite ordering would show a user an amount they cannot have, which is precisely the class of
error this app exists to prevent. Concretely: filter, then per-class statistics on what survives,
then feasibility against those figures, then select from the filtered pool.
**Why per class:** 034 removed a global floor with the note that it belonged per class alongside
the budgets, because different biologies genuinely differ in size. On the real export at
0.6535 µm/px, `Immune cells` has a median of 87.7 µm² and `Tumor` 142.8 — one number cannot be
right for both.
**Details:** the suggested per-replicate amounts are computed from the post-default-floor pool, so
they do not describe shapes that are about to be filtered out. Zero disables the filter for a
class. Without an image scale nothing is filtered and the column is hidden, because a µm² floor
cannot be evaluated — the editor says so rather than silently doing nothing. The plan is still
built from the whole frame so filtered and unselected shapes stay reportable. Floors are recorded
per class in `provenance.json`.
**Measured:** on `Single_cells.geojson` a 100 µm² floor excludes 21 of 121 shapes and lifts the
surviving median from 157 to 170 µm².
**Still open** (`ROADMAP.md` round-two question 2): whether one default across biologies is right.
It is wrong for `Immune cells` on the real export, and a default that is wrong for most classes
trains users to change it, which defeats the purpose.

## 061 — A start well, which is how several slides reach one plate
**Date:** 2026-08-27 · **Status:** active · answers the question deferred in 020
**Decision:** `plate.wells_from(wells, start_well)` returns the usable wells from a chosen one
onwards, exposed as a "Start at well" input beside the other plate options. The plate caption
reports the range used and names the well to begin the next slide from.
**Why this solves multi-slide-into-one-plate:** run slide one from `B2`, read off that it ended at
`B9`, run slide two from `B10` into the same plate. No cross-file state, no new concepts, and
nothing to remember between sessions beyond a well name the app tells you. 020 deferred the
problem as needing thought; this is the version that needs none.
**Degrades rather than fails:** an unknown or unusable start well — a typo, or a well the margin
excludes — returns the full list with a warning, because silently collecting nothing or half a
plate is worse than ignoring the input.
**Verified end to end:** two consecutive assignments into one plate reuse no wells.

## 062 — The plate is editable by hand, opt-in
**Date:** 2026-08-27 · **Status:** active · implements the alternative chosen in 055
**Decision:** `ui_shared.editable_plate` shows the plate as an `st.data_editor` with a dropdown of
samples per well, behind a checkbox that defaults to off. Ticking it switches from the automatic
assignment to whatever the user arranges.
**Why opt-in:** the automatic assignment is almost always what the user wants, and an editor shown
unasked invites fiddling with something that was already correct. Off by default costs one click
for the people who need it and nothing for everyone else.
**Why a dropdown and not drag-and-drop:** 055 settled that — Streamlit has no drag-and-drop into a
grid, `streamlit-sortables` cannot address 384 wells as targets, and a real plate drag-and-drop
means a custom frontend on a deployment we spent a PR slimming. A dropdown is also *safer*: it
cannot produce a typo or name a sample that does not exist.
**Guards:** it errors if a sample has been dropped off the plate entirely, naming which, and warns
if two share a well. Both are recoverable by unticking the box.
**Jose is not convinced by dropdowns**, and said to build it and look. If it reads badly the
fallback costs nothing: remove the checkbox and the automatic layout plus the start well already
cover the multi-slide case.

## 063 — "object" and "polygon" never appear in a message to the user
**Date:** 2026-08-27 · **Status:** active · **corrects 059**
**Decision:** Jose's correction. Every count the app *shows* says **shapes**, including counts of
things dropped while reading. "object" survives in code and documentation, where the QuPath
distinction is real; "polygon" survives for the geometry type, and appears in a message only to
explain why something cannot be cut. The upload summary now reads "This file holds 14,145 shapes
and 3 named calibration points" instead of "Geometries in file: 14145 Polygons, 3 Points".
**Why 059 was wrong:** it drew the line between "QuPath's object" and "our shape" and applied that
line to the interface as well as the code. Internally coherent, but it produced a screen where the
same 14,145 things were called Polygons, then shapes, then objects within a few lines — which is
exactly the confusion the glossary was written to remove. The distinction is real in the code and
invisible in a message.
**What changed:** nine user-facing strings, the geometry-count line, and the log lines for
consistency. Nothing about the code's internal vocabulary or `objectType`.
**Made enforceable:** `tests/test_nomenclature.py` now fails if any `ui_*` module shows the user
the word "object" (excluding `objectType` and "objective") or reports a count of geometry types.
The earlier version only checked that "shapes" appeared *somewhere*, which is why this slipped
through a PR whose entire purpose was naming.
**Found while fixing it, and fixed too:** a point with no name cannot be chosen as a calibration
point, and was being dropped without a word. A user who drew three points but did not name them
saw "This file has no calibration points" — while looking at their points. The report now carries
`n_unnamed_points` and the app says to name them in QuPath's annotation list.

## 064 — the GeoJSON check is one table, not a stack of warnings
**Date:** 2026-08-27 · **Status:** active
**Decision:** Jose's report: "the warnings are not nice to look at. They are somewhat verbose and
people might ignore them." `upload_step` now shows one line about what the file holds, then a
three-column table (`GeojsonReport.summary()`) — *In the file*, then each finding that applies —
with a plain-language "What happens" for each row. No per-class or per-shape breakdown at this
step. **The surviving count is not a row**: Jose asked for it to come from the `st.success`
line instead, which already said it. Two figures for the same thing on one screen makes the
reader work out which is authoritative, so the table states the file total and the deductions
and the success line states the answer.
**Why:** the previous screen could stack six `st.warning` boxes before the user reached Step 2.
Everything in them was true and rule 3 says to show it, but a wall of yellow is skimmed, and a
skimmed warning informs nobody. Rule 3 requires the loss to be *visible*, not to be shouted. A
table of five rows is read; six boxes are scrolled past.
**What is deliberately not in the table:** the names of multi-class combinations and the
MultiPolygon listing. Those are per-class detail, and the class table in Step 4/5 is where a user
is actually deciding about classes. The `--` joining rule stays in the table's note, because it
explains a class name they will see later and would otherwise not recognise.
**Still a warning box:** unnamed points. That is about calibration, not about shapes, and it
decides whether the user gets past the hard stop at Step 3.
**Guard:** `tests/test_geojson.py` asserts a clean file yields exactly one row (no rows of
zeros — the noise this replaced), that no row restates the surviving count, and that the
"ignored" rows sum exactly to
`n_shapes_in_file - n_shapes_kept`, so no drop cause can go unexplained.

## 065 — the size filter reports itself in the feasibility table, not in prose
**Date:** 2026-08-27 · **Status:** active · **extends 060**
**Decision:** Jose: "that whole box of text is too much and cuts the flow a bit… I think adding
two columns to the bottom table could communicate this info." `_report_minimum_area` is gone.
`budget.feasibility` takes an optional `excluded` series and adds **Too small to collect** (count)
and **% too small** (share of that class's own shapes) to the table already on screen. One caption
under it gives the total and says the minimum area can be changed.
**Why:** the box appeared in the middle of the step where the user is typing replicates and
amounts, so it interrupted the decision it was meant to inform. The information is per class, and
there was already a per-class table three lines below it — putting it there costs no vertical space
at all and lets the user compare classes, which the prose list did not.
**The share is of the class, not of the file.** A class that loses 80% of itself must read 80%. As
a share of the file the same loss could read 2% and the user would sign off on a replicate with
almost nothing in it.
**Fixed while doing it:** raising one class's floor above every shape in it dropped that class from
the pool and `feasibility` then raised `KeyError` on `stats.at[...]` — a traceback instead of the
app, one keystroke away in an editable column. A class absent from the pool now reads as zero
available, 100% too small.

## 066 — a third workflow: cells become regions, and regions get cut
**Date:** 2026-09-17 · **Status:** active
**Decision:** cellular neighbourhoods become a **third option on the workflow step**, in a new
`ui_packing.py`, rather than a mode inside the cell workflow. A new `regions.py` gives each cell
the space closest to it, caps and clips it, merges touching cells of the same class into regions,
and deals those across replicates. `plan_from_selection` gains a `workflow` argument; nothing else
in the shared path changed, and all 10 pre-existing golden artefacts stayed byte-identical.
**Why a third workflow rather than a mode:** Jose asked that this path "not disturb users who do
not care for it". The cell workflow's steps 4–7 exist to *choose among the cells that are there* —
minimum area, spread versus random, the adjacency preference, the selection fragment. None of them
means anything when the thing being cut is derived tissue rather than a cell. A mode would have put
a branch through four steps including the cached fragment, on the path that carries the most
tests, to reuse controls that would then all have to be hidden. A third radio option reuses upload,
calibration, class selection, the plate and the export — which is what `ui_shared` is for — and
leaves the two existing workflows untouched by construction.

### The classes are filtered after tessellating, never before
A region is the territory nearest its cell. Drop a class *before* tessellating and its neighbours
expand into the space it occupied, so a well labelled `Tumor` holds immune tissue. `project` takes
`include=` for this reason instead of being handed a pre-filtered frame, and
`test_an_unwanted_class_still_holds_its_own_territory` is the guard.

### Regions are capped at a disc and clipped to the hull of the cell bodies
**Alternatives considered.** openDVP's `adata_to_voronoi` drops unbounded regions and then drops
everything above the 98th area percentile; clipping to a tissue-outline annotation is the most
faithful but only works when the user drew and exported one, and can produce MultiPolygons.
Chosen instead: `voronoi ∩ disc(R) ∩ hull`, with `R = 3 × median nearest-neighbour distance` and
editable. It needs nothing extra from QuPath, it bounds every region *locally* rather than by a
global percentile, and all three pieces are convex — so a region is always a single Polygon and
the MultiPolygon problem never arises before the merge.
**The hull is of the cell bodies, not their centroids.** A test caught this: a rim cell's centroid
sits exactly *on* the centroid hull, so clipping to it cut away half of that cell's own tissue.
`shapely.convex_hull` over a geometry collection gives the same answer as
`union_all().convex_hull` for **0.09 s against 9.1 s** at 100 000 cells.

### shapely, not scipy, for the tessellation
`shapely.voronoi_polygons(..., ordered=True)` (shapely ≥ 2.1, already pinned) guarantees the i-th
polygon belongs to the i-th point, so classes map by position. openDVP and the prototype's upstream
notebooks both use `scipy.spatial.Voronoi`, which needs `point_region` indirection and explicit
`-1` unbounded-region rejection, and which loses the rim cells rather than bounding them. No new
dependency either way: `scipy` was already declared and unimported, and `regions.py` now uses
`scipy.spatial.cKDTree` for the neighbour spacing.

### Pinhole slivers are filled, and the threshold comes from the data
The radius cap is a 64-sided polygon while Voronoi edges are exact, so where three capped regions
meet they leave a gap of a pixel or two that belongs to no region; merging turns those into
interior rings. Left alone, an ordinary run raises "2 regions completely surround tissue of another
class, totalling 1 px²" — a warning about nothing, on the same screen as a warning that can mean
real contamination. `close_slivers` fills any ring smaller than **the smallest region of the whole
tessellation**: a genuine hole is another cell's territory, so it cannot be smaller than that.
Measured — slivers 0–8 px² against smallest regions of 48–702 px² across the demo files, no
overlap. The threshold is taken over *all* regions rather than the kept ones, because a hole may be
the region of an excluded class and that class can hold smaller regions than any kept one.
**Rejected:** a fixed pixel threshold, which would need retuning per file and per radius factor.

### Holes are warned about; the dilation is explained
Where a region genuinely surrounds another class, `extract_coordinates` takes the outer outline
only, so the enclosed tissue is cut into the same well. Warned, with the area in µm², never
blocked — the user may want exactly that, and this is `003`.
Separately, a region reaches past the cell outlines QuPath drew. **This is the dilation case `013`
and ROADMAP round-one open question 3 left open**, both of which promised the app would say so on
screen if it ever arrived. It is stated in the step's own text rather than in a warning box,
because it is how the workflow works rather than an anomaly, and a box on every single run is how
`064` says a warning stops being read.

### Dealing regions across replicates is deterministic and area-balanced
Largest region first into whichever replicate holds the least area so far. Replicates of a class
have to be comparable amounts, and dealing in order would give replicate 1 every large region. No
RNG at all here, so the same file reaches the same wells in a later session — which is what makes
the collection reportable in a methods section. Where a class has fewer regions than replicates
the extra replicates stay empty, keep their wells, and are named on screen.

### Circle packing is not in this change
Packing circles inside the regions is the point of the exercise and is the next branch. Splitting
it out means this one is independently clickable and cutting whole neighbourhoods is useful on its
own. The prototype at `PY38_CirclePackingStreamlit` contains no Voronoi code — it reads an
already-tessellated file — so that half had to be written here regardless.

## 067 — circle packing: ported from the prototype, with the parts that were wrong fixed
**Date:** 2026-09-17 · **Status:** active · **completes 066**
**Decision:** `packing.py` fills each region with circles by rejection sampling, which is what
Jose's prototype at `PY38_CirclePackingStreamlit/functions.py` does and is the right method here.
Step 8 of the regions workflow offers a choice — **circles packed inside the regions** (default)
or **the whole regions** — and the packing controls live in an `st.fragment` below the plate, so
tuning them never invalidates wells the user already approved.
**Why circles at all:** a merged region is one enormous irregular outline. The stage traces it for
a long time, and there is no way to ask for *some* of it. Circles cut quickly and add up to a
chosen area, which is the unit a proteomics experiment is actually specified in.
**Why whole regions stays available:** it was already built and tested in `066`, it is the only
thing possible without an image scale, and "collect all of this neighbourhood" is a real request.

### Measured, on Jose's real 8 411-cell TMA core at 0.6535 µm/px
684 regions, 3 replicates of 10 000 µm² for each of 3 classes:

| | first working version | after the three fixes below |
| --- | --- | --- |
| wall time | 8.6 s | **0.7 s** |
| circles kept / placed | 479 / 718 | **422 / 431** |
| replicates reaching target | 6 of 9 | **9 of 9** |

1. **Skip regions that cannot hold one smallest circle.** `buffer(-min_radius).is_empty` is exact
   and costs 0.01 s for 141 regions. 119 of 684 regions on real tissue are in that state, and each
   one otherwise spent the full 2 000-attempt budget finding out. Reported on screen, because the
   remedy — a smaller minimum circle — is something only the user can choose.
2. **Ask each region for its share of what is still outstanding.** Every region overshoots its
   share by up to one circle; carrying only a one-sided deficit forward therefore over-packed a
   141-region class by more than twice its target, which is where 239 discarded circles came from.
3. **One circle of slack per replicate.** A replicate fills until it *reaches* the target, so
   packing exactly the total left the last replicate holding only the remainder — reliably about
   5% short while its siblings overshot. A replicate systematically smaller than the others is not
   a comparable measurement, which is the whole point of having replicates.

### Spacing is global, and what that costs
The gap is enforced across the **whole collection**, not within a class as the prototype did.
Regions of two classes touch by construction, so two circles either side of a boundary could
otherwise sit a fraction of a micrometre apart; the strip between them detaches and falls into
whichever well is cut first. The laser does not care which class a neighbouring cut belongs to.
**The cost, stated plainly:** classes are packed in sorted order and share one collision grid, so
changing one class's amount can change the classes sorted after it — never those before it.
Everything is still reproducible from the recorded parameters. Judged worth it: a wrong-class
fragment in a well is a ruined sample, while coupling is an inconvenience in a preview.

### Randomness
`numpy.random.default_rng`, never the legacy global `numpy.random.seed` the prototype used, which
clobbers any other code drawing from `numpy.random` and is not this repo's convention
(`selection.py` has used `default_rng` since Phase 4). **One sub-stream per class, keyed by
`blake2b` of the class name** — not by position, so adding or removing a class leaves the others
drawing the same candidate positions, and not by `hash()`, which Python salts per process and
would make the same seed pack differently tomorrow. The prototype ran one stream through every
class in turn, so nudging one class's spacing re-rolled every draw in every later class, and a
settings-and-preview loop then shows the user changes they did not ask for and cannot attribute.
`seed=None` is gone: a seed is always set, default 0, and recorded in `provenance.json`.

### Collision detection
A dict of buckets keyed `(x // cell, y // cell)` with `cell = 2 * max_radius + spacing`, so only
the 3x3 neighbourhood is ever checked. The prototype rebuilt a `shapely.STRtree` from every placed
circle **on every single attempt** — O(n² log n) over a run, and the dominant cost despite the
comment claiming it was "blazingly fast". It also queried the tree with no predicate, so it
compared bounding boxes and rejected placements that were legal.

### The capacity estimate accounts for the gap
`0.547 * area / (1 + spacing / (2 * mean_radius))²`. The 0.547 is measured, not assumed: randomly
thrown circles of mixed size cover that share of a 2000x2000 px square at a zero gap. Circles
100-500 µm² at 0.3467 µm/px, measured against the formula: 54.7%/54.7% at 0 µm, 42.9%/45.0% at 2,
32.9%/34.7% at 5, 21.4%/23.9% at 10, 12.2%/13.4% at 20 — within 2 points everywhere and slightly
optimistic. **The prototype's flat 0.55 ignored the gap entirely**, so at 20 µm it would have told
a user they had 4.5x the tissue actually available. Shown before packing runs, so an impossible
request is visible without waiting for it; what was achieved is always reported separately.

### Smoothing is reported, not absorbed
`simplify` cuts the corners off a circle, and at the default 1 px tolerance a 64-sided circle of
radius ≤ 10 px drops to 9 vertices and **loses 10% of its area**; 2.6% at radius 20 px, 0.6% at
100 px. At 0.6535 µm/px a 100 µm² circle has a radius of 8.6 px, so the default circle range sits
squarely in the worst of it — on the real export the loss is 6% of everything collected. Area per
replicate is this workflow's entire budget, so `smoothing_loss` measures it and step 8 warns above
5%, naming both real remedies: a larger smallest circle, or a lower tolerance.
**Rejected:** inflating each circle to compensate. It would hit the requested number, but by
cutting a shape the user did not ask for to correct an error they cannot see. Saying what will
happen and letting them decide is `003`.

### Also dropped or changed
- `n_permutations`, the prototype's best-of-N restarts: x5 cost for a marginal gain, and it made
  the random stream depend on it. One loop if it is ever wanted back.
- Areas come from `polygon.area`, not analytic `pi r²` — a buffered circle is a 64-gon and about
  0.4% smaller than the circle it approximates, so the prototype's accounting was optimistic.
- An empty result now carries its columns (`empty_circles`). The prototype returned a bare
  `GeoDataFrame()`, so every caller had to remember to check before touching a column; a test
  caught this app raising `KeyError` instead of reporting that nothing fitted.
- A shortfall concentrates in the **last** replicates rather than being spread thinly over all of
  them, so a class that can only half-fill its request yields whole comparable replicates plus a
  remainder. Consistent with `budget.feasibility`'s "replicates fillable" in the cell workflow.

### A `packing` golden case, driven by the demo file
The plan for this feature proposed a synthetic square, to keep the reference bytes free of GEOS.
Changed on the grounds that the `regions` case already depends on GEOS, so a synthetic case buys
no new robustness, while going through the demo file covers projection, merging, the synthesised
QuPath fields, dealing and export as well. `packing` also pins numpy's random stream — if it
differs on its own, a recorded seed no longer reproduces its collection, which is breaking for
anyone who has written a seed into a methods section. `tests/test_packing.py` carries the
seed-stability guarantee independently.

## 068 — the collection step moves above the plate, and µm² loses its decimals
**Date:** 2026-09-17 · **Status:** active · **refines 066 and 067**
All four from Jose, reviewing the first working version of the regions workflow.

### The collection step comes before the plate
**Was:** 4 classes, 5 regions, 6 replicates, 7 plate, 8 what-to-collect and export.
**Now:** 4 classes, 5 regions, 6 what-to-collect, 7 plate, 8 export.
Jose: "Let users decide on how much area, and how many samples, before they see the plate… Here
is where users will loop through parameters to reach the settings they want."
**Why he is right:** the amount per replicate and the replicate count are exactly what the plate
has to accommodate. Deciding them first means the plate is shown once, already correct, instead
of being redrawn under the user on every keystroke while they are still thinking about circle
sizes rather than wells. The circle settings and the amount now sit together in one step, in his
order — circle parameters, then the amount, then the number of replicates.
**Consequence: the `st.fragment` had to go.** A fragment reruns only itself, so nothing below it
re-executes; with the plate and export below the loop, they would sit there showing a stale
collection — the exact trap `051` describes. The caches on the projection and the packing are
what make a full rerun affordable instead, which is what they were added for.

### The enclosed-class warning is removed
`066` warned when a region completely surrounds tissue of another class, on the grounds that the
export path follows a shape's outer outline only and would collect the enclosed tissue too.
Jose: "Surrounded tissue is not an issue, please remove that warning."
**Removed.** It fired on 21 of 684 regions on his real core, so it was appearing on an ordinary
run, and by `064` a warning that always appears is one that gets ignored when it matters. It was
also misleading in the default mode: circles are never packed into a hole, because
`patch.contains` respects interior rings. The geometry still behaves this way and
`RegionReport` still counts it into the log, so the numbers are there if the judgement changes.
**Supersedes** the hole-warning half of `066`; the dilation explanation in that entry stays.

### Amounts in µm² are whole numbers
Jose: "sometimes we will get millions of um^2, it should not need me to count the number of
digits to understand the number… remove any decimals from any um2 parameter, these are noise."
Inputs use `format="%d"` with integer bounds. Tables are cast to `Int64` and rendered with
`st.column_config.NumberColumn(format="localized")` — an integer column cannot show a decimal,
and `localized` is what adds the thousands separator, so one change satisfies both halves.
`regions.RegionReport.summary` stopped rounding at all: deciding how to display a number is the
UI layer's job, and the library rounding it to two decimals was the reason the UI could not.
**Still showing decimals, deliberately not changed:** the per-class table in step 4, because
`ui_shared.class_selection_step` and `stats.for_display` are shared with the cell workflow and
`DECIMALS = 2` is asserted by `tests/test_stats.py`. Flagged to Jose rather than changed here
(rule 9).

### The "Effort" control is removed
Jose: "I do not understand what the Effort parameter is for." It exposed `max_attempts`, the
number of consecutive failed placements before a region is called full — a property of how the
rejection sampler gives up, not a decision about the experiment. It stays in `PackingParams` at
its default of 2000 because the algorithm needs a stopping rule, and it is still recorded in
`provenance.json`, but nothing asks the user about it. A control nobody can interpret is worse
than no control: it invites fiddling with something that has no experimental meaning.

### Added while in there
A metrics row above the achieved table, borrowed from the shape of Jose's own prototype: tissue
in the regions, how much is being collected and what share of the whole that is, the number of
circles, and the mean circle diameter. It answers "is this a sensible collection?" at a glance,
which the per-replicate table alone does not. The share is `delta_color="off"` so it does not
read as a change.

## 069 — step 6 is a side-by-side loop, and the amount is per class
**Date:** 2026-09-17 · **Status:** active · **refines 067 and 068**
Jose: "Step 6 needs better visual feedback loop, currently I have to scroll up and down too
much."

### Inputs at a third of the width, the picture at two thirds
`st.columns([1, 2])`. Every control the user touches is in the narrow left column — what to
collect, the image scale, the circle sizes, the gap, the seed, and the per-class table. The
picture is in the wide right column, with the four summary metrics under it. The per-replicate
table, the capacity expander and the warnings sit below, full width: they are what a user reads
once they have settled on an arrangement, not what they watch while tuning it.
**Why it matters more than it sounds:** this is the only step in the app where the user changes a
number specifically to see what it does to the geometry. With the controls above the picture,
every adjustment cost a scroll down to look and a scroll up to change — so in practice nobody
tunes, they accept the first result.

### The amount per replicate is per class, in the same table as the replicates
Jose: "There should be one table, where users define the number of replicates, and the area per
replicate for each class."
`PackingParams` lost `area_per_replicate_um2`; `pack` and `capacity` now take
`list[budget.ClassBudget]`. **`ClassBudget` is reused, not reinvented** — `class_name`,
`replicates`, `per_replicate` is exactly its shape, it already has `.required`, and
`budget.group_keys` then sizes the plate with no new code. The cell workflow has used it since
Phase 3, so the two workflows now describe an amount the same way.
**Why per class is right:** on Jose's real core `Tumor` and `Immune cells` hold about 900 000 µm²
each while `Immune cells--Tumor` holds 220 000. One global amount forced the same target on all
three, so the user could not ask each class for what it could actually give. A class asked for
zero still appears in the report at zero, because a row vanishing looks like a failure rather
than a choice.

### Two variables, two channels
Jose: "you should plot the circles with the colors of each class, not replicate. The outline of
the circle should be colored with tab20, and that should mean the replicate number."
`plot.plot_regions_and_circles`: **fill is the class**, for the regions and the circles alike, so
a circle is visibly part of the tissue it came from; **outline is the replicate**, from tab20.
Two legends, both outside the axes, because one combined key would not say which channel carries
which meaning.
**tab20 deliberately, and deliberately not the class palette.** The classes keep Okabe-Ito, which
stays legible for the common forms of colour blindness but only has seven entries; tab20 has
twenty, which is what makes more than a handful of replicates tellable apart. One picture carries
both scales, so they must not be mistakable for each other.
**One thing measurement forced:** both palettes contain an orange, and at full opacity an orange
circle of an orange class hid its own replicate ring — the ring being the only thing carrying the
replicate. Fill dropped to alpha 0.7 and the ring to 1.5 pt, checked by rendering the real core
at full extent and at working zoom. `replicate_colors` keys tab20 by replicate *number* rather
than by position, so replicate 2 keeps its colour when a class with fewer replicates is added;
keyed by position, a user comparing two screenshots would read a change that never happened.
Drawing costs 0.02 s for 684 regions and 422 circles, so it redraws on every keystroke for free.

### Refreshing
Jose: "should refresh after the table has been updated by user." `st.data_editor` already reruns
the script on an edit, and the projection and packing caches make that rerun cheap, so the
picture is rebuilt from the edited table with nothing extra. This is the second reason the
`st.fragment` removed in `068` had to go: a fragment would have redrawn the picture but left the
plate and export below it stale.

### Session state
`replicates` became `region_budgets`, holding a list of `ClassBudget` as dicts rather than a
`{class: count}` map. Not folded into the cell workflow's existing `budgets` key: the two
workflows would then overwrite each other's on a switch, and one key silently meaning two things
is how `059` says a vocabulary rots.

## 070 — the merge was right, the picture was wrong; and packing goes per class
**Date:** 2026-09-17 · **Status:** active · **refines 066, 067 and 069**
Jose, on the first side-by-side version: "There seems to be issues with plotting the
neighborhoods, in some places two different colors overlay… The visualization right now tells me
the merging is not working."

### The merge was correct and is now asserted
Checked rather than argued, over the 684 regions of the real core: the largest **area** shared by
any two regions is **0.000000 µm²**; the 18 same-class pairs that intersect at all do so at
`Point`/`MultiPoint` only, which is what separate components of a union legitimately do; the 1 177
different-class pairs intersect along `LineString`/`MultiLineString`, i.e. shared boundaries,
which is exactly right. `tests/test_regions.py::test_no_two_regions_share_any_tissue` holds it.
And yes, the pipeline is what Jose described: Voronoi of the cell centroids, capped and clipped,
then `dissolve` by class and `explode` into one row per contiguous area.

### What he was actually seeing was a plotting bug, and a large one
`polygon_paths` draws compound paths over the exterior **and every interior** ring. The first
version drew `exterior.coords` alone, which on one region painted **207 000 µm² of Immune cells
straight over the Tumor inside it**. A picture that shows one class covering another is indeed
evidence of a broken merge — it just was not this merge.
**The part that would have bitten silently:** a compound path only reads an interior ring as a
hole if it winds *against* the exterior, GEOS promises nothing about which way a ring came out,
and on this data the unoriented rings rendered filled. `orient(polygon, sign=1.0)` fixes it, and
the test renders the figure and reads the pixel in the hole, because nothing short of drawing it
proves the winding is right.

### Jose's two Voronoi caveats, and what the app does about them
- **"empty spaces within the tissue are labelled improperly."** True of plain Voronoi: a lumen or
  a tear is nearest to *some* cell, so it gets handed to it. The **radius cap is the mechanism** —
  a gap wider than twice the cap is left unassigned, which at the default factor of 3 on the real
  core means anything wider than about 58 µm. Nothing in the centroids distinguishes "empty" from
  "sparse", so this has to stay a dial rather than a judgement the app makes; what the app owes
  the user is that the dial exists, says what it does, and shows the result.
- **"the edges of tissue would extend to infinity."** Also true, and handled by the same cap plus
  the clip to the convex hull of the cell outlines. `066` recorded that choice.

### Colours: measured, not chosen by eye
Jose: "Colors are weird, please choose a set of colors for class and circle colors, and a
different set for outlines."
Two full-hue palettes cannot do this. Across every fill-outline pair with Okabe-Ito fills and
tab20 outlines, the tightest WCAG contrast is **1.00** — the same colour — which is precisely why
an orange circle of an orange class hid its own ring. So the channels differ in **lightness** as
well as hue: fills tinted toward white, outlines shaded toward black. Scanning both factors
against the contrast of every pair and the RGB separation of every pair of outlines gave
`CLASS_FILL_TINT = 0.55` with tab10 shaded 0.25 — contrast **1.78** everywhere, outlines
**0.198** apart. tab10 rather than tab20 because shading compresses a palette and tab20's twenty
entries become indistinguishable once darkened; ten replicates already exceeds what a plate is
for, and it cycles. `class_colors` remains the one source of truth for a class's hue, with
`class_fill_colors` tinting it, so a class looks like itself in every picture. Both floors are
asserted in `tests/test_plot.py`, so a future palette change cannot quietly break legibility.

### Circle parameters are per class
Jose: "we should add class-specific circle packing parameters… build that table were users input
how many replicates, and how much area, and add the parameters to that table."
New `packing.ClassPacking` carries everything one class asks for — replicates, µm² per replicate,
smallest and largest circle, gap. `PackingParams` keeps only what genuinely cannot differ per
class: the seed and the attempt budget. `.as_budget()` converts to `ClassBudget` so
`budget.group_keys` still owns the `class_rN` rule — one naming rule, one place.
**Why it is right, not just asked for:** a sparse, stringy class needs smaller circles than a
solid one before anything fits at all. With one global range the user had to pick whichever class
was worst off and impose it on every class. On the real core 119 of 684 regions hold no circle at
100 µm²; that number is a property of one class's geometry, and now so is the remedy.
**One new rule this forces:** two classes can ask for different gaps, and a pair of circles either
side of a class boundary has to satisfy **both** — so the collision test takes the *wider* of the
two. Taking the narrower would silently override whichever class asked for more room. The buckets
therefore store each circle's own gap alongside its radius.
**Verified faithful:** all 14 golden artefacts stayed byte-identical through this refactor,
because every class received what the global parameters used to impose.

### Layout: the table, then the picture
`069` put the inputs in a 1/3 column beside the picture. With five per-class columns that no
longer fits, and Jose asked for the picture below the table anyway. Now: the mode radio, then the
scale and seed, then the full-width table, then the metrics, then the picture. Still one screen
from first input to feedback, which was the point of `069`.

### Still to do, not in this change
Jose: "I like this UI, and should be generalized to the segmentation-based workflow as well."
Agreed and noted in ROADMAP. Not done here: the cell workflow has its own per-class editor, its
own minimum-area filter and a `st.fragment` whose shape depends on the plate being above it, so
converting it is its own piece of work with its own manual pass — and this branch is already one
feature's worth of diff.

## 071 — a picture beside every table, and the reach asked for in µm
**Date:** 2026-09-17 · **Status:** active · **refines 069 and 070**
Five notes from Jose on the working version. He kept the design and cut what was not earning
its place.

### The metrics row is removed
"I think the number below the user input for the circle packing and replicates is unnecessary."
Tissue in the regions, being collected, circles to cut, mean circle across — added in `068`,
gone now. They sat **between** the settings and the picture and pushed the two apart, which is
the one thing step 6 is arranged to avoid (`069`). Every figure they carried is either in the
per-replicate table below or derivable from it. A summary that costs the reader the thing it is
summarising is a bad trade.

### A NumberColumn only goes on a numeric column
"the Class column has a red triangle that states 'this value cannot be interpreted as a number'."
`_show_table` was handing `st.column_config.NumberColumn` to every column including the class
names, so Streamlit stamped each one with a warning triangle and a table that was perfectly fine
read as an error. Config is now built from `is_numeric_dtype` only. Pinned by a test, because
the symptom is invisible to every check except looking at the screen.

### The reach is a distance, not a multiplier
"For Step 5, what does it mean 'How far a region may reach from its cell'? what is the limit for
the voronoi projection? this needs better explaining (not longer)."
He is right that it needed explaining, and right that the answer is not more words. The control
was a dimensionless multiple of cell spacing, which is a unit nobody thinks in. It is now
**"Maximum reach from each cell (µm)"**, defaulting to three times `median_cell_spacing` — so the
label states what the limit is and the caption below states its consequence: *gaps wider than
twice this are left uncollected*. The one genuinely unintuitive thing about a Voronoi projection
is now the first thing the control says.
`RegionParams` keeps `radius_factor` for callers that prefer to scale it; the UI passes
`max_radius_px`.

### A picture beside the numbers, in steps 4 and 5 too
"the preview for Step 5 is rather large, consider the 1/3 tabular info and 2/3 preview idea.
Same with Step 4, it should preview the segmented classified cells (in that way ensuring they
know what their input was)."
Both now `st.columns([1, 2])`. Step 4's picture is the more valuable of the two and it is new in
kind: it is the **only place the app shows a user what they actually uploaded**. Every other
check is a number, and a number cannot tell you the export was the wrong slide. Classes the user
keeps are coloured and the rest are greyed, so an accidental exclusion is visible as well.
**It lives in `class_selection_step`, so the cell workflow gets it too** — which is the first
instalment of the generalisation Jose asked for in `070`, done here because the step is already
shared and `ui_cells.overview_step` drew the same picture a screen further down. That function is
gone.
Step 6 keeps its picture *below* rather than beside: five per-class columns do not fit in a third
of the page, which `070` already settled.

## 072 — one way to show a number, and the defaults a DVP experiment actually uses
**Date:** 2026-09-17 · **Status:** active · **extends 068**
Pre-merge pass, from Jose: "please check the units and thousands separator issue across the
entire app, for consistency. Also, please change the default number of replicates to be 3, and
the default area per replicate to be 25000."

### Defaults
`packing.DEFAULT_REPLICATES = 3`, `DEFAULT_AREA_PER_REPLICATE_UM2 = 25_000.0`. Three replicates
is the smallest number that supports a variance estimate, so it is what a DVP experiment is
normally designed around; 25 000 µm² is Jose's own per-well amount. Worth knowing what that does
on his core: `Immune cells--Tumor` has about 76 000 µm² of packable area against 3 × 25 000, so
its third replicate falls short out of the box and the warning fires. Correct behaviour — the
class genuinely cannot supply it — and better seen immediately than discovered at the mass spec.
**Deliberately not changed: the cell workflow still defaults to 1 replicate.** Its per-replicate
amount defaults to *the whole surviving class*, so 3 replicates would ask for three times what
exists and raise a shortfall on every single load. Two workflows, two defaults, because the
amount they start from means different things.

### One helper renders every table of amounts
`ui_shared.show_amounts`, used by all three workflows. Rounds numeric columns to whole numbers,
casts to `Int64`, and applies `NumberColumn(format="localized")` to those columns only.
Audited by rendering every amount table in all three workflows and reading back the dtypes and
the column config: six tables, every numeric column whole and separated, no text column given a
number format. Before the pass the cell workflow showed `428955.48` where the regions workflow
showed `428,955`, which is the same complaint Jose raised about step 5 — in a workflow he had not
been looking at.

### Units are in the header, not inferred
The cell workflow's feasibility table had **Available**, **Total requested** and **Short by** with
no unit at all, while only **Per replicate** carried one — and its achieved table showed the
library's own identifiers, including `area_um2` and `neighbour_also_collected`. Both are renamed
at render time with `mode.unit` so the header says what it counts. The library keeps its column
names; `budget.DISPLAY_COLUMNS` and `selection.WITH_NEIGHBOUR` are untouched, so
`tests/test_budget.py` and `tests/test_stats.py` still pin the library contract rather than the
rendering.
One label was inconsistent app-wide: "Smoothing tolerance (pixels)" against `px` everywhere
else. Now `px`. Display spellings are `µm²`, `px²`, `µm/px`, `px`; `um2`/`px2` survive only as
identifiers.
**Left alone on purpose:** µm/px keeps 4 decimals and percentages keep 1. Those are small
numbers where a thousands separator means nothing and the decimal carries information — the rule
is "no meaningless precision", not "no decimals anywhere".

## 073 — both workflows start from a real experiment, and there is a changelog
**Date:** 2026-09-17 · **Status:** active · **supersedes the default in 039, extends 072**
Jose: "cell workflow should still have 3 replicates per class included, and each should have
25000 in terms of area, for number of cells it should have 150 cells."

### The cell workflow starts at 3 x 25 000 µm², or 3 x 150 cells
`072` had left it at 1 replicate of *the whole surviving class*, on the reasoning that 3
replicates of the whole class would ask for three times what exists and warn on every load.
Jose overruled that, and he is right for a reason I had missed: **defaulting to the whole class
silently disabled the feasibility check**. A class can always supply all of itself, so the one
figure that tells a user whether their plan is possible read "every class can supply its budget"
on every single load, whatever the file. A default that guarantees a green light is worse than a
default that sometimes warns — the warning is the feature.
On `Single_cells.geojson` the new default asks 450 cells of a 121-cell class and says so, which
is exactly the information the step exists to give.
**One definition, in `budget.py`**: `DEFAULT_REPLICATES`, `DEFAULT_AREA_PER_REPLICATE_UM2`,
`DEFAULT_CELLS_PER_REPLICATE`, and `BudgetMode.default_per_replicate` which picks between the
last two. `packing.py` imports them rather than keeping its own copies, so the two workflows
cannot offer different amounts for the same experiment — `tests/test_budget.py` asserts they are
the same objects.
**Supersedes** the "default to the whole class in a single replicate" choice from Phase 3.

### A changelog, written for users
`CHANGELOG.md`, newest first, linked from `README.md`. Written in the voice of the v4.0.0 release
notes — what changed *for you*, not what changed in the code. No commit hashes, no module names
except where a user would type them.
Starts at the current release with the earlier tags pointed at rather than reconstructed: v3 and
before predate the file and inventing their contents from commit subjects would produce something
confidently wrong. The top section is left as **Unreleased** because the version number is Jose's
to pick when he bumps `pyproject.toml`.

## 074 — close cuts share a well, the projection skips what it can, and the map is legible
**Date:** 2026-09-17 · **Status:** active · **refines 067, 070 and 071**
Three notes from Jose after running the merged version on the community cloud.

### "cells of the same color touching each other in different groups, this is not intuitive"
He was right, and it was not the bug it looked like. The gap **is** enforced exactly — measured
over 984 circles on his core, zero pairs closer than the 5 µm asked for, closest 5.02 µm. Two
things were true at once:
- A 5 µm gap is about **3 screen pixels** on a whole-core view, and the replicate ring is 1.5 pt.
  Circles a gap apart genuinely look touching.
- **46 pairs of same-class circles a gap apart were going into different wells.** Two cuts that
  close can shed the strip between them; into the same well that is harmless because the
  material pools there anyway, into different wells it is cross-replicate contamination. The look
  was the symptom, the well assignment was the problem.
**Fix:** `_deal_circles` deals **clusters** of near-touching circles rather than individual
circles — union-find over an `STRtree` `dwithin` query. `CLUSTER_GAP_FACTOR = 1.5` chosen by
measurement, not taste: 1.0 still left 46 such pairs, 1.5 leaves none, and 5.0 leaves none but
spreads the achieved areas 8.1% instead of 1.7% because clusters grow to 15 circles and a cluster
is dealt whole. Same-class cross-well proximity: **46 to 0**.
**What it cannot fix, so it is reported:** a class and its replicates own their own wells by
definition, so two cuts either side of a *class* boundary always land in different wells.
`n_near_another_class` counts them and step 6 says so — 68 circles on his core. Only a wider gap
moves those apart, which is the honest answer.
**Golden re-blessed**, `packing` only, and verified before doing it rather than after: all 42
circles the old reference cut are still cut, one previously-discarded circle is now used, 9
circles changed well, and the placement geometry is identical. The 12 other artefacts are
untouched.

### "somewhat slow with the core, I can imagine a larger image failing"
Profiled rather than guessed. At 60 000 cells the cap intersection cost 0.67 s and the hull clip
0.38 s — and **only 1.6% of cells need either**, because in dense tissue a Voronoi cell is far
smaller than the cap and only the rim reaches the hull. Testing first costs 0.006 s and 0.03 s.
`_cap_and_clip` now does that. Real core **0.48 s to 0.34 s**; 150 000 cells 10.8 s to 8.4 s.
The cap test is exact, not a heuristic: the farthest point of a convex region from a fixed point
is an extreme point, a cell is contained in its bounding box, and a box is convex — so four
corner distances decide it. A test compares against doing every intersection, by area and
symmetric difference rather than `equals`, because an intersected cell comes back rebuilt by GEOS
and can lose a collinear vertex a skipped cell keeps — no difference in tissue, at most one
redundant point in a cut path.
**The floor is the merge**: 2.2 s of the 3.0 s at 60 000 cells, one GEOS union per class.
`union_all` + `get_parts` measured 2.185 s against `dissolve` + `explode` at 2.204 s, so there is
nothing to win by rewriting it. Above roughly 60 000 cells this step is simply slow, and
`_report_scale` already warns from 40 000 (`051`). Saying so is better than implying a fix
exists.

### "The visualization is too light... remove the calibration points they are distracting"
Regions are now drawn from the **full-strength** class palette at alpha 0.85 rather than the
tinted fill at 0.45 — at 0.45 the tissue map was barely there, which was the point of having it.
Circles keep the tinted fill, so a dark replicate ring still reads on them and the two layers
separate by lightness without needing a third palette. The legend swatches follow the regions,
since that is what dominates the picture.
The calibration triangle is gone from both region pictures. It served calibration QC, which step
3 already reports as a percentage, and over a dense tissue map it is noise. It stays on step 4's
input drawing, where distortion risk is the thing being judged.

## 075 — several slides into one well: pooled by class name, one engine run per slide
**Date:** 2026-10-07 · **Status:** planned · **supersedes 061 as the answer to 020** (061 stays,
for slides that go into different wells)
**Decision:** the app accepts several slides in one session — several `.geojson` uploads or one
`.zip` of them — and pools shapes of the **same class name** from every slide into the same
samples. The amount a sample asks for is split between slides by a **slide strategy**: *priority
order* (fill from the first slide, top up from the next), *proportional* (each slide supplies a
share in proportion to what it holds; **the default**), or *equal share* (capped by what a slide
holds). In every strategy a slide that cannot supply its share passes the shortfall to the next.
**The pooling assumption is told to the user.** Pooling by class name is only right when the
slides are the same biological sample — serial sections of one block, classified with the same
classes. Jose confirmed that is the case this serves. The app says so once, under Samples, when a
second slide is added, and says how to keep slides apart (distinct class names, e.g.
`P1_Tumor`). A class present on only some slides is named, because that is usually a spelling
difference rather than biology.
**Why the selection and packing engines run once per slide, unchanged:** every slide's pixel
coordinates start at (0, 0). Stacking slides into one frame would make the spread grid, the
adjacency graph, the Voronoi projection and the circle collision grid treat shapes on different
slides as neighbours. Running the existing engines per slide keeps all of that correct and keeps
Jose's requirement that the sampling strategy does not vary with the number of slides. Only the
bookkeeping — how much each slide is asked for, and what it achieved — spans slides.
**Why one `.xml` per slide:** calibration points belong to one image, and a py-lmd `Collection`
takes one triangle. Each slide also keeps its own scale, and areas are converted per slide before
they are summed.
**Why multi-upload plus a QuPath script, not the project folder:** a `.qpproj` folder holds its
shapes in `data/<n>/data.qpdata`, which is Java-serialised (`file` reports "Java serialization
data, version 5"). Only QuPath can read it — from Python that means paquo plus a QuPath install
and a JVM, which Community Cloud cannot run and which would be a second, local-only code path.
A *Run for project* Groovy script that exports one GeoJSON per image gives the same "whole project
at once" experience and leaves GeoJSON as the only input contract (CLAUDE.md §4).
**The annotations workflow is not extended** — it is frozen. Annotations up to an amount already
work through the cell workflow, because `selection.select` does not filter on `objectType`.
**Alternatives rejected:** reading `.qpproj` via paquo (above); balancing slides within each
replicate (not what a same-sample experiment needs); per-slide amounts typed by hand (three
strategies cover the cases Jose named without a table of numbers per class per slide); one
combined coordinate frame with slides offset from each other (every engine would then need to
know the offset is not tissue).

## 076 — several plates, balanced by default; the user chooses the cutting order
**Date:** 2026-10-07 · **Status:** planned
**Decision:** an experiment may need more than one plate. The app computes the minimum number of
plates from the usable wells, lets the user raise it, and distributes samples **balanced across
plates** by default: each class's replicates are dealt round-robin over the plates, so every plate
holds every class where the replicate count allows. *Sequential* fill — plate 1 full, then plate 2
— is the alternative. All plates share one plate type, margin and spacing. **Start at well**
applies to the first plate.
**Why balanced is the default:** `plate.assign_wells` sorts groups, so sequential overflow puts
`Immune_r1…r3` on plate 1 and `Tumor_r1…r3` on plate 2. Plate is then confounded with class, and a
plate or batch effect downstream reads as biology. Balanced is the design a proteomics batch
correction can work with.
**Why one `.xml` per slide × plate pair:** py-lmd writes a well as a bare `<CapID>` (`lmd/lib.py`)
with no plate identity, so one XML can only target one plate. Two slides on two plates is four
XMLs, each named for both, so it cannot be loaded against the wrong pair.
**Why the cutting order is the user's choice:** slide by slide (calibrate a slide once, swap
plates) and plate by plate (mount a plate once, recalibrate slides) trade different handling
steps, and which is cheaper depends on the lab. The folder layout of the download and
`HOW_TO_CUT.txt` follow the choice.
**How the UI stays legible**, because Jose named that as the risk: four stages in a fixed order
(Slides → Samples → Plates → Cut); slide and plate controls appear only when there are two or
more of them; one live experiment-at-a-glance line; the sample sheet as the one central table;
at most one new control per stage. A single-slide, single-plate user sees nothing new, and the
seven existing golden cases must pass without re-blessing. `ROADMAP.md` round five has the
detail.
**Alternatives rejected:** sequential as the default (the confound above); the user assigning
plates per class (a choice most users should not have to make, and still possible through the
editable sample sheet); mixed plate types in one experiment (not asked for).

## 077 — the split between slides is decided once, before selecting
**Date:** 2026-10-07 · **Status:** active · **refines 075**
**Decision:** `slides.split_budgets` decides each slide's share from what each slide holds, and
`select_across_slides` runs the selection once per slide on that share. There is no second pass
that tops up a short sample from another slide after the run. Every slide uses the same selection
params and seed.
**Why:** a top-up pass would call the engine again on a slide's leftover shapes, and that second
call cannot see the shapes the first call took — so the adjacency preference and the neighbour
count would both be wrong across the two calls. For shapes the availability is exact (counts and
areas of collectable shapes), so a split capped by it is delivered to within the last shape and a
top-up buys nothing. Circles are different: their capacity is an estimate, slightly optimistic, so
a slide can fall short of its share. That is for the regions PR, where the packer can be asked for
slack instead.
**Same seed on every slide:** one number to report in a methods section. Slides with different
shapes give different selections anyway.
**Alternatives rejected:** a runtime top-up (above); per-replicate amounts in `ClassBudget` so a
top-up could be uneven across replicates (changes the engine, which 075 keeps unchanged).

## 078 — one page of four stages; every collection method is a module that returns a SampleSet
**Date:** 2026-10-07 · **Status:** active · **implements 075–076; supersedes the frozen UI of the
annotations workflow (CLAUDE.md §5), the cell workflow's fragment (051) and its plate-before-
selection order (045, 049); does round four's reordering**
**Decision:** the app is four stages in a fixed order — Slides, Samples, Plates, Cut — one module
each (`ui_slides`, `ui_samples`, `ui_plates`, `ui_cut`). Each hands the next a plain object:
`SlidesContext`, then `model.SampleSet`, then `PlateLayout`. The three ways of collecting are
modules registered in `ui_samples.methods()` — `ui_collect_whole`, `ui_collect_select`,
`ui_collect_regions` — each with `LABEL`, `HELP` and `render(context) -> SampleSet | None`.
**Why, in Jose's words:** *"three selection workflows … and various inputs and outputs. Best case
scenario these are interoperable and modular. So that adding more features is simpler."* Before
this, each workflow owned its own plate step, export step and session keys, so a feature like
several slides had to be built three times. Now a new input format touches Stage 1 only, a new way
of choosing tissue is one new module, and a new output touches Stage 4 only. No stage imports
another stage.
**The methods are named by what is cut**, Jose's choice: *Whole shapes* (the annotations
workflow), *Selected shapes* (the cell workflow, which also serves annotations up to an amount,
because the selection never looked at `objectType`) and *Regions and circles*.
**The annotations workflow's UI is no longer frozen; its output still is.** Jose agreed to move it
into the stages. It loses the *Confirm plate layout* button and the two plate-view buttons — the
plate now updates live, as the other methods' always did — and the custom samples-and-wells upload
becomes an option of the Plates stage, open to every method (one plate). What it cuts is guarded
three ways: the golden harness (the old plan builders now delegate to `model.plan_from_groups`,
which every method uses), `tests/test_slides.py` (the sample-set route writes the same `.xml` as
the old builder), and `tests/test_app.py`, which drives the real page with `AppTest` and compares
the downloaded `.xml` and `.csv` with `tools/golden/annotations.*` byte for byte.
**Selection now runs before the plate, with no fragment.** Samples before Plates is the rule
(076), so the fragment of 051 had to go for the reason 068 gave for the regions route: a fragment
reruns only itself and would leave the plate and the cut below it stale. The selection cache
carries the cost instead.
**The sidebar holds the experiment at a glance** (*2 slides → 27 samples → 1 plate*) and a
checklist of the four stages, filled in as each completes — in view however far the page scrolls.
**A stale download is withdrawn.** The download is stamped with a hash of the assignment, the
shapes per sample, the method's parameters and the export settings; if any of them changes after
processing, the button is replaced with a note to process again. Before, a user could change the
plate and download the previous files.
**Several slides, as decided in 075–077,** appear only when there are several: tabs per slide for
calibration and pictures, a class × slide table, the pooling note, the slide strategy, and a scale
table instead of one input. A class split into one well per shape carries the slide in its name
(`T-Cell_A_001`) so cells of different slides never share a well.
**Alternatives rejected:** tabs per stage (hide earlier decisions and still recompute everything);
keeping the annotations workflow's own frozen UI beside the stages (two plate steps to maintain,
and no multi-slide pooling for whole shapes); naming methods by input type (one input type —
annotations — would have sat under two methods).

## 079 — confirm every slide's calibration, a full-width class table, and plate files that reload
**Date:** 2026-10-08 · **Status:** active · **refines 078; supersedes 071 for the classes step**
Jose's notes after clicking through 078.
- **Calibration points are confirmed, not assumed.** Defaults (the first three points, or the
  first slide's names) let a user sail past the one choice that decides where every shape is cut.
  Each slide now has a *These are the right calibration points* checkbox, keyed on the points
  chosen so that changing one withdraws the confirmation, and nothing after Stage 1 appears until
  every slide is confirmed. **A gate, not an `st.stop()`** (CLAUDE.md §3): nothing is wrong, the
  user has not looked yet, so the page simply waits and names the slides still to confirm. The
  ticks sit in a line above the slide tabs rather than in their labels, because `st.tabs` has no
  key and relabelling a tab could send the user back to the first one after each confirmation.
- **The classes table is full width, the picture below it.** Beside the picture (071) the table
  had a third of the page and needed sideways scrolling, worst with a column per slide.
- **Whole shapes drop the `class` column** of the sample sheet: a sample is its class there, so it
  repeated the first column. With replicates they differ and both stay.
- **Plate files say which plate they are**, `samples_and_wells_P2.json`, and with several plates a
  second button downloads all of them. **Both load back in**: the Plates-stage upload now reads
  `{"P1": {...}, "P2": {...}}` as well as `{sample: well}`, so any download's
  `samples_and_wells.json` reopens an experiment for changes.
- **The cut order was only ever the download's organisation.** Jose asked what it does: the `.xml`
  files are identical either way and the LMD user loads them in whatever order they like. So it
  is now *Organise the download — by slide / by plate*, with a caption saying exactly that, and
  `HOW_TO_CUT.txt` is `COLLECTION_PLAN.txt` — a plan, not orders.
- **Extras moved to a sidebar tab**, beside the experiment summary, whose stages now carry emoji.
- **demo1**, synthetic: two patients × three serial sections, classes prefixed by patient so the
  sections of one patient pool and the patients never do — the naming the app recommends.
  Generated by `tools/make_demo1.py`, seeded, so it can be regenerated byte for byte.

## 080 — classes in tab20 across every picture, and Extras on a page of its own
**Date:** 2026-10-08 · **Status:** active · **refines 070 and 079**
- **One class palette per experiment, tab20.** Jose: colour the shapes to be collected by class,
  tab20, matching the classes picture. Two things had to change. The selection preview coloured by
  replicate; it now colours by class, and the replicate stays in the table above it. And each plot
  sorted only the classes it drew, so a class changed colour whenever a picture showed a different
  set; now `ui_samples.class_palette` assigns colours once over every class of every slide and
  every plot takes that mapping. tab20's strong shades go first, so the first ten classes differ
  in hue, and its two greys are left out because grey is how the app draws what is not collected.
- **The circle fill tint went from 0.55 to 0.6.** The legibility floor of 070 — every replicate
  outline against every class fill at WCAG contrast 1.7 or more — failed with tab20 at 0.55
  (1.66). 0.6 gives 1.81 without touching the outlines, which keeps their separation floor too.
- **Extras is a page, not a sidebar tab** (Jose: "the UI in the sidebar … distracts"). Two pages
  at the top via `st.navigation(position="top")`: Collect and Extras. The sidebar is the
  experiment summary only. Session state is shared, so going to Extras and back loses nothing.
- **`COLLECTION_PLAN.txt` is no longer printed after processing**; it is in the download. The
  per-file cut path review stays.

## 081 — collectors: tube holders and strips beside plates
**Date:** 2026-10-08 · **Status:** active · **refines 076**
- **What the LMD cuts into is a collector**, Leica's own word: a 96 or 384 well plate, a holder
  of four Eppendorf tubes, or a holder of one 8-well strip. Jose: one holder plays the part of a
  plate — one tube is *not* one plate — and an experiment uses one kind of collector, never a mix.
- **CapIDs on the holders are letters only**, `A`–`D` for tubes and `A`–`H` for the strip, as
  Jose gave them. py-lmd writes the well string into `<CapID>` verbatim, so nothing changes there.
- **A registry, `plate.COLLECTORS`**, says per collector how many rows and columns it has,
  whether a position carries a column number, and the words for it. Every place that split a well
  as `well[0]` / `well[1:]` reads it through `plate.split_position` instead. Rejected: tubes as a
  one-column plate with the `1` stripped at export, which gives every position two names, `A1` in
  the app and `A` in the `.xml` — the quiet kind of mismatch that sends tissue to the wrong cap.
- **Collectors are named `Plate1`, `TubeHolder1`, `Strip1`**, explicit because most experiments
  have only a few (Jose). That renames multi-plate downloads from `P1` to `Plate1` — file and
  folder names and the keys of `samples_and_wells.json`, never the bytes of an `.xml` or a plate
  map. Files keyed `P1` from earlier downloads still load, as plates.
- **Margin and spacing are hidden, not disabled, on the holders.** On four tubes they mean
  nothing, and a disabled control asks "why can't I?".
- **The code keeps the name `plate`** (`plate.py`, `plate_type`, `n_plates`): a rename across
  modules and session keys would bury this change. The interface and the docs say collector.
- No 1536 plate: its rows run to `AF`, nobody has needed one, and Jose dropped it.

## 082 — replicate rings in colours no class has, over a dark edge; a stronger circle fill
**Date:** 2026-10-08 · **Status:** active · **supersedes the ring colours and fill tint of 070 and 080**
- **The problem.** Jose: "the replicate outline color matches the classes making a mess out of the
  colors." The rings were tab10 shaded 0.25 toward black, and tab10 is exactly tab20's strong
  half, so replicate *n*'s ring was a darker copy of class *n*'s colour.
- **Measured before choosing.** Keeping the old rule (a ring must contrast with every fill on its
  own) and asking for rings ΔE ≥ 35 from all 18 class colours at a fill tint of 0.25 leaves only
  black, blue, indigo, dark violet and dark slate, which look alike on a small circle. Jose saw
  the three candidates rendered on demo1 and chose the third.
- **Chosen:** rings white, yellow, magenta, cyan, black, each over a thin black edge
  (`patheffects.withStroke`), in the picture and in its legend. Every ring is at least ΔE 26 from
  every class colour and ΔE 51 from every other ring. The edge, not the ring, carries the contrast
  against the fill: at least 5.9:1 on every class fill.
- **Circle fill tint 0.6 → 0.25** (Jose: less transparent). A circle on its own region now nearly
  matches it, so the ring is what marks the circle out. Accepted, because the ring is where the
  information is.
- Five ring colours, cycling after that. Most designs collect fewer replicates, and a sixth colour
  outside tab20's hues and apart from these five could not be found.

## 083 — what hiding the plate controls costs, and the rest of what the rename touches
**Date:** 2026-10-08 · **Status:** active · **refines 081**
- **Hidden controls lose their values in Streamlit.** A keyed widget that is not drawn in a run
  has its state dropped, so with margin and spacing hidden for the holders (081), a look at the
  tube holder reset a plate's margin 2 to 1 and its spacing to 1 — the layout would then use the
  edge wells the user chose to avoid, with nothing to say so. While a holder is chosen,
  `ui_plates.settings_step` writes `plate_margin`, `plate_step_row` and `plate_step_col` back to
  session state each run (`PLATE_ONLY_KEYS`), which keeps them. Found in the branch review;
  `tests/test_app.py` switches to tubes and back and checks both values survive.
- **The cutting-runs warning counts collectors that receive samples**, not the number box:
  sequential filling or an uploaded file can leave collectors empty, and an empty one gets no
  `.xml`. With several slides the runs are per slide, so the warning says so.
- **The rename to `Plate1` reaches more than file names** in a download of several plates: the
  `plate` column of `samples.csv`, the wording of `COLLECTION_PLAN.txt` ("Load Plate1." instead of
  "Put plate P1 in the collector."), and `provenance.json` (`plate: Plate1` per cut, plus
  `experiment.collector`). A script that matched `P1` in `samples.csv` needs updating. The single
  slide, single plate download is unchanged.

## 084 — cache keys include a hash of each file's content
**Date:** 2026-10-09 · **Status:** active · **refines 050**
- **What was wrong:** the selection, projection, spacing and class-statistics caches keyed on
  `(slide name, file name, row count, class names)` (050). A QuPath export reclassified and
  exported again keeps all four, so a re-upload in the same session — or another user's upload of
  a same-named file, since `st.cache_data` is shared across sessions — got results computed from
  the old classes. The selection is by row, so a cell now in another class could have been cut
  into the old class's well. Found while profiling; on demo1's `P1_S1` with 877 of 1 658 cells
  reclassified, the fingerprint was identical.
- **Decision:** `slides.read_slides` hashes each file's bytes (blake2b, 128 bits) into
  `Slide.content_digest`, and the fingerprint includes it. Taken once at read, so a rerun pays
  nothing: 0.06 s measured on the 84 MB real export, against the seconds the read itself takes.
- **Rejected:** hashing the frame on every rerun — the cost 050 avoided; and keying on the upload's
  name and size, which a re-export with the same digit counts in its coordinates also keeps.

## 085 — stages 3 and 4 as one fragment, and pictures drawn once
**Date:** 2026-10-09 · **Status:** active · **refines 050, 078**
- **The problem:** Jose — changing the margin with demo1's six slides takes about 5 s. Measured in
  Chrome, 2.8 s for any widget change, of which the plate code was 50 ms. The rest was drawing:
  every rerun redrew 12 pictures (six class pictures, six selection previews), and `st.pyplot`
  saved each at 200 dpi, 2 020 px wide, which Streamlit then decoded, shrank to its 1 460 px
  display limit and encoded again.
- **Decision 1, a fragment around stages 3 and 4** (`streamlit_app.collector_and_cut`). A
  collector or cut setting reruns only those two stages: margin change 2.8 s → 0.2 s. 051's
  fragment went in 078 because it sat *above* the plate and the export, which a fragment rerun
  leaves stale. This one has nothing below it, and stages 1 and 2 only ever clear what 3 and 4
  write, on a new upload, which is a full rerun anyway. The sidebar summary is outside it, so
  `Summary.forget` withdraws the Collector and Cut lines first: without it, hitting the
  no-usable-wells stop left the sidebar saying the plate was set.
- **Decision 2, each picture drawn once per thing it shows** (`ui_shared.show_picture`), kept as
  a PNG under a hash of the slide's fingerprint (084), the labels, the calibration, the palette
  and, for regions, their geometry. That covers the full reruns a fragment cannot avoid: a seed
  change in stage 2, 2.85 s → 0.1–0.4 s, since the six class pictures stay and only the six
  previews redraw.
- **Decision 3, rendered to fit 1 460 px.** Caching alone gave 1.07 s, not 0.1 s: Streamlit
  re-encodes a too-wide image on every rerun even from cached bytes. The dpi is set from the
  figure's width (143 for the 10-inch shapes pictures, 130 for the 11-inch regions), capped at
  200. What the browser receives went from a 2 020 px render shrunk to 1 460 to a 1 444 px render
  drawn directly; side by side the two look the same.
- **Rejected:** `st.form` for the collector settings — an Apply button, and a plate that no longer
  follows the controls, for the speed the fragment gives without either. Upgrading Streamlit for
  per-session caches or fragment keys — nothing here needs them. A one-slide-at-a-time picker
  instead of tabs would make the first draw after confirming calibration (1.4 s, twelve pictures)
  about six times cheaper; left for Jose, since it changes the layout.
- **Costs accepted:** up to 64 pictures of about 0.5 MB in memory, shared by every session, which
  is safe because the key is the content. A change to `plot.py` reaches the app only after a
  server restart, since the cache key is what a picture shows, not how it is drawn.
