# Rules for developing Qupath_to_LMD

These are the rules I (Claude) follow when working on this repo. They are binding, not
suggestions. If a rule blocks me, I say so and stop rather than route around it.

Companion documents:
- `facts.md` — what is true about this app (living document, I keep it current)
- `decisions.md` — why things are the way they are (append-only log)
- `GLOSSARY.md` — one word per thing. **shape** is what the laser cuts, **object** is QuPath's
  word for what is in the input file, **polygon** is a geometry type. I use these consistently
  in code, in messages and in docs, and I add a term here rather than inventing a synonym.

---

## 1. Git workflow — I branch and commit, Jose pushes and PRs

- **Never** `git push`. **Never** `gh pr create`. No exceptions, not even when asked to
  "finish up" or when a push seems obviously wanted. If a push is needed, I say
  "ready to push" and stop.
- Every change starts with a branch cut from an up-to-date `dev`:
  `git fetch origin && git checkout -b <type>/<slug> origin/dev`
- Branch naming: `feat/`, `fix/`, `refactor/`, `docs/`, `chore/` + short kebab slug.
- Never commit directly to `master` or `dev`. PRs target `dev`; `dev` → `master` is Jose's call.
- Commits are small and self-describing. Message: imperative subject ≤ 72 chars, then a
  body explaining *why* when it isn't obvious.
- Every commit ends with the trailer:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- I only stage files I actually touched — `git add <paths>`, never `git add -A`.
- I never rewrite published history, never force-push, never `git checkout --` over
  uncommitted work that isn't mine.

## 2. Nothing is "done" until Jose has clicked through it

- Every feature or behaviour change ends with me handing over an explicit **manual test
  script**: what to run, which file to upload, which buttons to press in order, and what
  Jose should expect to see. Written as numbered steps, not prose.
- Before handing over, I smoke-test locally myself:
  ```
  uv sync
  uv run streamlit run streamlit_app.py --server.headless true --server.port 8599
  ```
  and confirm the app boots with no exception in the log. Booting is the floor, not the
  ceiling — where I can, I also exercise the changed code path directly (see rule 6).
- I use `demo_Qupath_project/TD_01_verysmall_mIF.geojson` (annotations, 4 classes) and
  `demo_Qupath_project/Single_cells.geojson` (many small shapes) as the standard fixtures,
  and `demo_Qupath_project/demo_samples_and_wells.txt` for the custom-upload path.
- I report test results honestly. If I only booted the app and did not exercise the
  feature, I say exactly that.

## 3. Scientist-facing behaviour: warn, do not block

The users are scientists cutting real, irreplaceable tissue. The app's job is to make
what will happen legible, then let them decide.

- Default to `st.warning` + a clear explanation of the consequence. Reserve `st.error` for
  states where continuing would silently produce a wrong `.xml`.
- **`st.stop()` is a last resort.** Only when continuing cannot produce a meaningful
  result at all (no geometry, no calibration points, wells that do not exist on the plate).
  Every new `st.stop()` gets a line in `decisions.md` saying why blocking beat warning.
- Never silently drop data. If shapes are excluded (MultiPolygons, unclassified objects,
  classes missing from samples-and-wells), the count and the reason go on screen, not just
  into the log.
- Quantities that affect the cut — well assignments, calibration triangle coverage,
  simplification, how many shapes made it into the collection — must be visible before the
  user downloads, not discoverable only afterwards in the log.
- Wording is plain and specific: what happened, what it means for their collection, what
  to do about it. No jargon-only messages.

## 4. GeoJSON from QuPath is the only input contract

- The input is a QuPath-exported **FeatureCollection** `.geojson`. Everything downstream
  derives from it. I do not add parallel input formats (CSV of coordinates, raw XML,
  images) without an explicit decision logged in `decisions.md`.
- Anything the app hands back that is meant to re-open in QuPath must survive a QuPath
  round-trip: no NaN-bearing columns, `classification` kept as a dict-shaped value,
  `id` / `objectType` / `classification` / `geometry` preserved (see `utils.sanitize_gdf`).
- Assume QuPath exports are messy: missing `classification`, `MultiPolygon`s, unnamed
  points, duplicate class names, `LineString`s. New code handles these explicitly rather
  than assuming the demo file's shape.
- Coordinate handling is load-bearing. I do not touch `orientation_transform`,
  the calibration-point ordering, or `geometry.simplify()` without a logged decision —
  a silent change here mis-cuts tissue and nobody notices until the mass spec.

## 5. Where code goes

There are two layers, and the boundary is the point of the whole structure.

**Library layer** — pure, no Streamlit. Takes explicit arguments, returns values or report
objects, raises domain exceptions. This is the layer that can be exercised outside the app.

- `model.py` — `CollectionPlan`, canonical column names, provenance
- `geojson.py` — reading, QC, explode, measurements, sanitising for QuPath
- `plate.py` — plate geometry, wells, layouts, samples-and-wells parsing
- `qc.py` — checks that return reports (`TriangleReport`, `SawReport`, `PixelSizeReport`)
- `export.py` — collection building, XML, the download bundle
- `extras.py` — QuPath `classes.json` generation

**UI layer** — Streamlit. May read and write `st.session_state`; that is its job.

- `streamlit_app.py` — session init, logging, and the router through the four stages. Stays thin.
- `ui_slides.py` → `ui_samples.py` → `ui_plates.py` → `ui_cut.py` — the four stages, each
  handing the next a plain object (`SlidesContext`, `SampleSet`, `PlateLayout`)
- `ui_collect_*.py` — one module per collection method, registered in `ui_samples.methods()`
- `ui_shared.py` — helpers several stages use; `ui_summary.py` — the sidebar

Rules that follow from that split:

- New computation goes in the library layer, never in a `ui_*` module and never in
  `streamlit_app.py`. If I cannot call it from a plain script, it is in the wrong place.
- Library functions never touch `st.*` or `st.session_state`. No exceptions — a report
  object or an exception is always the answer.
- A `ui_*` function renders one step and returns what the next step needs.
- Every new `st.session_state` key is initialised in `DEFAULTS` at the top of
  `streamlit_app.py` and added to the key table in `facts.md`.
- What the whole-shapes method (the old annotations workflow) **produces is frozen**: later
  changes must not alter it, and `tools/golden_harness.py` and `tests/test_app.py` enforce that.
  Its UI lives in the shared stages since `decisions.md` 078.

## 6. Verification

- `uv run ruff check <files-I-touched>` must be clean for those files before I commit.
  The repo has a pre-existing backlog of ruff findings (see `facts.md`) — I do not
  bulk-fix it as a side effect of a feature commit; that is its own `chore/` branch.
- **`uv run pytest` must pass before I commit.** The suite lives in `tests/` and needs no
  environment setup — `conftest.py` forces the Agg matplotlib backend, without which py-lmd's
  `plt.show()` hangs. `uv run pytest -m "not slow"` skips the golden gate for a quick loop.
- When I add behaviour, I add a test for it, and the test's assertion message says **what
  breaks in the app** when it fails — not just which value differed. A failing test that does
  not explain the consequence is a puzzle, not a warning.
- **`uv run python tools/golden_harness.py check` must pass before I commit any change
  that touches geometry, calibration, well assignment or export.** It compares the XML and
  CSV byte-for-byte against `tools/golden/`. A coordinate shifted by a pixel or an inverted
  Y flip is invisible in the running app and this is the only thing that catches it.
  `tests/test_golden.py` runs it too, so CI enforces it.
  - If output is *meant* to change, I re-bless with `capture`, say so explicitly in the
    commit message, and explain why the new bytes are correct. I never re-bless to make a
    red check go green, and I never hand-edit files in `tools/golden/`.
  - When I add a code path the four cases do not cover, I add a case.
- Tests use only files committed to the repo, or fixtures that generate what they need. The
  real 83.7 MB export is not a dependency, because CI has to run without it.
- For UI behaviour, `tests/test_ui_behaviour.py` stubs Streamlit via monkeypatch and records
  what was shown, so the hard stops and the warning-versus-note distinction are covered without
  a browser. `src/qupath_to_lmd/mock_streamlit.py` remains for notebook use.

## 7. Dependencies

- `requirements.txt` is generated, and it is what Streamlit Community Cloud installs from.
  Adding or bumping a dependency means **both**: edit `pyproject.toml`, then
  `uv pip compile pyproject.toml -o requirements.txt`. A dep added to only one of the two
  breaks the deployed app while working locally.
- I propose new dependencies before adding them, with a one-line reason. The bar is high;
  this app is deployed on a free tier with a memory ceiling.

## 8. Documentation duties, every time

- **`facts.md`**: I update it in the same commit whenever I change something it describes —
  session-state keys, data contracts, pipeline behaviour, known quirks. It is memory, so I
  correct stale entries rather than appending contradictions.
- **`decisions.md`**: append-only. A new entry for every non-obvious choice: blocking vs
  warning, a data-format change, a dependency, an architectural direction, a rejected
  alternative. Never edit or delete an old entry — a later entry supersedes an earlier one
  and says so explicitly by number.
- `README.md` is user-facing: I update it when a change alters what a scientist does or
  sees, not for internal refactors.
- I do not add code comments that restate the code. Comments explain domain reasoning
  (why Y is flipped, why margin 2 on a 384 plate, why simplify tolerance 1).

## 9. Scope

- I do the task asked. I do not refactor adjacent code, rename things, reformat files, or
  "clean up while I'm here" — drive-by changes inflate the diff Jose has to review and
  hide the real change.
- If I spot a genuine bug outside scope, I finish the task, then report the bug with
  file:line and let Jose decide. Candidates already found are listed in `facts.md`.
- Style follows the surrounding file, including its indentation, even where that differs
  from my preference. `streamlit_app.py`, `core.py` and `utils.py` are largely
  **3-space indented** — I match the file I am editing rather than normalising it.
