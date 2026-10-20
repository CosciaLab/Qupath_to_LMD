# Collectors (tube holders, 8-well strips) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Stage 3 collect into an Eppendorf tube holder (4 tubes, `A`–`D`) or an 8-well strip holder (`A`–`H`) as well as 96/384 plates, and stop replicate rings repeating the class colours.

**Architecture:** A `Collector` registry in `plate.py` replaces `PLATE_SHAPES`; every place that splits a well into row and column goes through one parser. Collector *names* (`Plate1`, `TubeHolder1`, `Strip1`) come from the registry, and the multi-plate machinery stays as it is. Plate output bytes do not change.

**Tech Stack:** Python 3.11+, Streamlit, pandas, py-lmd, matplotlib, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-08-collectors-design.md`

## Global Constraints

- Branch `feat/collectors` (already exists, holds the spec commit). Never push, never open a PR: stop at "ready to push".
- Commit with `git commit --only <paths>`. Never `git add -A`. Every message ends with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Before every commit, run `uv run ruff check <touched files>` and `uv run pytest`, and for Tasks 1–4 also `uv run python tools/golden_harness.py check`. The nine existing cases must stay `match`. Never re-bless them, and never hand-edit `tools/golden/`.
- The library layer (`plate.py`, `qc.py`, `export.py`, `slides.py`, `plot.py`) never touches `st.*`.
- 4-space indentation. Comments explain domain reasons only.
- Collector keys are exactly `"384"`, `"96"`, `"tubes"`, `"strip"`. Library parameters keep the name `plate: str = "384"`, because the rename is out of scope.
- Collector names: `Plate1…`, `TubeHolder1…`, `Strip1…`. The legacy name `P<n>` is read as `Plate<n>` only when a plate is chosen.
- On screen: the stage is "3 · Collector"; collector nouns are "plate" / "tube holder" / "strip holder"; position nouns are "well" / "tube" / "well".
- Every test's assertion message says what goes wrong at the microscope.
- `facts.md` changes in the same commit as whatever it describes. `decisions.md` is append-only.

## Review Focus

1. **A legacy `P1`/`P2` file uploaded while tubes are chosen** must be refused, not loaded as tube holders (wells `C3` would then be invalid anyway, but the message must name the collector mismatch). Test in Task 2.
2. **The hand editor on a single-column collector** must read `A`, not `Atube`, back from the edited table. Test in Task 1.
3. **More than 200 samples on tubes** need more than the 50 collectors `st.number_input(max_value=50)` allows, and Streamlit raises when `value > max_value`. Test in Task 5.
4. **A start position typed in lower case or in plate form on tubes** (`c`, `C3`): `c` must start at `C`, and `C3` must fall back to the beginning with the existing warning. Test in Task 1.
5. **A custom file whose positions exist on a plate but not on the strip** (`I`, `A1`) is an error before download, and nothing gets cut into a nonexistent cap. Test in Task 1 (`validate_saw`).

---

### Task 1: Collector registry and positions

**Files:**
- Modify: `src/qupath_to_lmd/plate.py` (replace `PLATE_SHAPES`; `plate_dimensions`, `acceptable_wells`, `default_layout`, `sample_layout`, `layout_to_saw`, `placement_dataframe`)
- Test: `tests/test_collectors.py` (create)
- Docs: `decisions.md` (append 081), `facts.md` (layout line for `plate.py`, "Domain constants and conventions")

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Collector` with fields `key, label, rows, columns, numbered, name_prefix, noun, position, spacing`, exactly as in the spec.
  - `COLLECTORS: dict[str, Collector]` in the order `"384"`, `"96"`, `"tubes"`, `"strip"`, with labels `"384 well plate"`, `"96 well plate"`, `"Eppendorf tube holder (4 tubes)"`, `"8-well strip holder"`.
  - `collector(plate: str) -> Collector`, which raises `ValueError` for an unknown key.
  - `split_position(label: str) -> tuple[str, int | None]`: `"C3"` gives `("C", 3)` and `"C"` gives `("C", None)`.
  - `layout_to_saw(layout, plate: str = "384")` gains the `plate` parameter.
  - The other signatures are unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_collectors.py`:
  - `test_tube_holder_positions_are_letters_a_to_d`: `plate.acceptable_wells("tubes") == ["A", "B", "C", "D"]`.
  - `test_strip_positions_are_letters_a_to_h`: `plate.acceptable_wells("strip") == list("ABCDEFGH")`.
  - `test_margin_and_spacing_do_not_apply_to_tubes_or_strips`: `acceptable_wells(key, margins=2, step_row=2, step_col=2) == acceptable_wells(key)` for both keys.
  - `test_plates_keep_their_wells`: for `"384"` and `"96"`, margins 0–3 and steps 1–3, the result equals a hard-coded copy of today's comprehension. Copy the old body into the test as `_old_acceptable_wells`.
  - `test_a_position_splits_into_row_and_column`: `split_position("C3") == ("C", 3)`, `split_position("P24") == ("P", 24)`, `split_position("C") == ("C", None)`.
  - `test_a_tube_holder_table_is_one_column`: `placement_dataframe({"Tumor": "C"}, plate="tubes")` has index `A..D`, one column `"tube"`, and `.at["C", "tube"] == "Tumor"`.
  - `test_the_hand_editor_reads_tubes_back_as_letters`: `layout_to_saw(placement_dataframe(s, "tubes"), "tubes") == s` for `s = {"Tumor": "A", "Stroma": "D"}`. The message names the `Atube` failure.
  - `test_a_start_tube_is_case_insensitive_and_a_plate_well_is_ignored`: `wells_from(acceptable_wells("tubes"), "c") == ["C", "D"]` and `wells_from(acceptable_wells("tubes"), "C3") == ["A", "B", "C", "D"]`.
  - `test_positions_that_do_not_exist_on_the_collector_are_invalid`: `qc.validate_saw({"a": "C3", "b": "E"}, [], plate="tubes").invalid_wells == {"C3", "E"}` and `qc.validate_saw({"a": "I", "b": "A1"}, [], plate="strip").invalid_wells == {"I", "A1"}`.
  - `test_an_unknown_collector_is_refused`: `pytest.raises(ValueError)` on `plate.collector("1536")`.

- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_collectors.py -v`. Expected: FAIL (`acceptable_wells("tubes")` raises `ValueError`).

- [ ] **Step 3: Implement the registry in `plate.py`.**
  - `plate_dimensions` returns `(c.rows, c.columns)` and keeps its docstring.
  - `acceptable_wells` returns `[row for row in letters]` when `not c.spacing`. Otherwise it is the current body.
  - Unnumbered tables (`default_layout`, `placement_dataframe`, `sample_layout`) have a single column named `c.position`. Numbered tables are unchanged: `placement_dataframe` keeps its string column labels `"1".."24"`, so the plate CSV bytes stay the same.
  - `sample_layout` and `layout_to_saw` use `split_position`.
  - `qc.validate_saw` already calls `acceptable_wells(plate=plate, margins=0)`, so it should need no change. Confirm that.

- [ ] **Step 4: Run the tests and confirm they pass.** Run `uv run pytest tests/test_collectors.py tests/test_plate.py -v`, expecting PASS. Then run `uv run python tools/golden_harness.py check`, expecting all 21 artefacts identical.

- [ ] **Step 5: Docs.**
  - Append `decisions.md` **081**, "collectors: tube holders and strips beside plates". Cover:
    - the registry
    - the rejection of one-column plates with `1` stripped at export (two names per position)
    - the names `Plate1`/`TubeHolder1`/`Strip1` and legacy `P<n>`
    - hiding instead of disabling margin and spacing
    - the deferred `plate` → `collector` rename
    - the multi-plate file-name change
    - CapIDs are letters only, as Jose stated
  - `facts.md`: add the tube holder and strip lines to "Domain constants and conventions", and the registry to the `plate.py` layout entry.

- [ ] **Step 6: Commit.**
  ```bash
  git commit --only src/qupath_to_lmd/plate.py tests/test_collectors.py decisions.md facts.md -m "Add collector registry with tube holder and strip positions"
  ```
  The `git add` of the new test file comes first.

---

### Task 2: Collector names and reading samples-and-wells files back

**Files:**
- Modify: `src/qupath_to_lmd/plate.py` (`plate_names`, `assign_to_plates`, `assignment_from_scheme`, `per_plate`)
- Modify: `tests/test_experiment.py:36,46,52,58,105,168,171` (`P1` → `Plate1` where a name is asserted)
- Modify: `src/qupath_to_lmd/ui_plates.py:260`, `tools/golden_harness.py:256-257`
- Test: `tests/test_collectors.py`
- Docs: `facts.md` ("Several plates" section: names)

**Interfaces:**
- Consumes: `collector(plate)` from Task 1.
- Produces:
  - `plate_names(n_plates: int, plate: str = "384") -> list[str]`, giving `["Plate1", ...]`, `["TubeHolder1", ...]` or `["Strip1", ...]`.
  - `assign_to_plates(groups, wells, n_plates=1, distribution=..., randomize=False, seed=0, start_well=None, plate: str = "384")`, which names collectors with `plate_names(..., plate)`.
  - `assignment_from_scheme(parsed: dict, plate: str = "384") -> dict[str, tuple[str, str]]`.
  - `per_plate(...)`, sorted on the trailing number of each name.

- [ ] **Step 1: Write the failing tests** in `tests/test_collectors.py`:
  - `test_collectors_are_named_explicitly`: `plate_names(2, "384") == ["Plate1", "Plate2"]`, `plate_names(1, "tubes") == ["TubeHolder1"]`, `plate_names(2, "strip") == ["Strip1", "Strip2"]`.
  - `test_thirteen_samples_fill_four_balanced_tube_holders`:
    - 13 groups (`Tumor_r1..5`, `Stroma_r1..4`, `Immune_r1..4`) with `assign_to_plates(groups, acceptable_wells("tubes"), plates_needed(13, 4), plate="tubes")`
    - every group placed
    - holders `{"TubeHolder1", ..., "TubeHolder4"}`
    - every position in `"ABCD"`
    - no holder over 4
  - `test_collector_names_round_trip_through_a_file`: for each key, `assignment_from_scheme(per_plate(a), plate=key) == a`. Here `a` is a two-collector assignment built with `assign_to_plates`.
  - `test_ten_plates_sort_after_two`: `list(per_plate({"x": ("Plate10", "A1"), "y": ("Plate2", "A1")})) == ["Plate2", "Plate10"]`.
  - `test_an_old_p1_file_loads_as_plate1`: `assignment_from_scheme({"P1": {"T": "C3"}, "P2": {"S": "C3"}}, plate="384") == {"T": ("Plate1", "C3"), "S": ("Plate2", "C3")}`.
  - `test_a_flat_file_goes_to_the_first_collector`: `assignment_from_scheme({"T": "C"}, plate="tubes") == {"T": ("TubeHolder1", "C")}`.
  - `test_a_file_for_another_collector_is_refused_by_name` (Review Focus 1), parametrized over:
    - `({"TubeHolder1": {"T": "A"}}, "384")`
    - `({"P1": {"T": "C3"}}, "tubes")`
    - `({"Plate1": {"T": "C3"}}, "strip")`

    Each raises `SawParseError`. The message contains the chosen collector's label, and contains `"tube holder"` when the file is for tube holders.
- [ ] **Step 2: Update `tests/test_experiment.py`.** Asserted plate names become `Plate1`/`Plate2`. `test_a_one_plate_file_still_loads_as_plate_one` expects `("Plate1", "C3")`. Keep the malformed-file parametrization.
- [ ] **Step 3: Run the tests and confirm they fail.** Run `uv run pytest tests/test_collectors.py tests/test_experiment.py -v`. Expected: the new name tests FAIL.
- [ ] **Step 4: Implement the naming in `plate.py`.**
  - `assignment_from_scheme` accepts `^{prefix}\d+$` for the chosen collector, and also `^P\d+$` when `plate` is `"384"` or `"96"`, which it renames to `Plate<n>`.
  - If a name matches *another* collector's prefix, raise `SawParseError(f"This file is for {other.noun}s, but a {chosen.label} is chosen. Change the collector, or use a file made for it.")`.
  - Any other name raises `SawParseError(f"{chosen.noun.capitalize()}s must be named {prefix}1, {prefix}2, …; this file has {names}.")`.
  - The mixed flat/nested check is unchanged.
- [ ] **Step 5: Fix the two places that hard-code the old names.**
  - `ui_plates.py:260`: `name == "P1"` becomes `name == names[0]`, so the first collector still gets the start-well list.
  - `tools/golden_harness.py:256-257`: key artefacts by `_golden_label(cut.plate)`, which maps `Plate<n>` to `P<n>` and leaves other names alone. Add a one-line comment: the reference files predate the names, and renaming them by hand is forbidden.
- [ ] **Step 6: Verify.** Run `uv run pytest` (PASS) and `uv run python tools/golden_harness.py check` (all 21 identical).
- [ ] **Step 7: Commit** with `facts.md`: "Name collectors Plate1, TubeHolder1, Strip1; read legacy P1 files".

---

### Task 3: Export file names, instructions and the harness label

**Files:**
- Modify: `src/qupath_to_lmd/export.py` (`build_bundle`, `Cut.path`, `cutting_instructions`, `build_experiment_bundle`)
- Modify: `src/qupath_to_lmd/slides.py:378` (no logic change; confirm `per_plate` names flow through)
- (harness label mapping moved to Task 2)
- Test: `tests/test_collectors.py`, `tests/test_export.py` if it asserts old names (grep `plate_P`)
- Docs: `facts.md` (download contents: names)

**Interfaces:**
- Consumes: `collector(plate)`, the names from Task 2.
- Produces:
  - `cutting_instructions(cuts, order, plate: str = "384") -> str`
  - `build_experiment_bundle(...)` passes `plate` to it
  - `Cut.path(order)` returns `f"{plate}/{plate}__{slide}.xml"` (BY_PLATE) or `f"slide_{slide}/{slide}__{plate}.xml"`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_tube_collection_writes_letter_cap_ids`:
    - read `TD_01_verysmall_mIF.geojson` the way `tools/golden_harness.run_case` does
    - use `samples_and_wells = dict(zip(sorted classes, acceptable_wells("tubes")))`
    - run `export.build_collection(..., plate="tubes")`
    - assert `<CapID>A</CapID>` through `<CapID>D</CapID>` are in `result.xml`, and no `<CapID>A1</CapID>`
  - `test_a_tube_bundle_names_its_csv_for_tubes`: the `build_bundle(..., plate="tubes")` zip namelist contains `TD_01_verysmall_mIF_tubes.csv`, and with `plate="384"` it still contains `TD_01_verysmall_mIF_384_wellplate.csv`.
  - `test_experiment_files_are_named_after_the_collector`:
    - `Cut("S1", "TubeHolder2", plan, result).path(CutOrder.BY_PLATE) == "TubeHolder2/TubeHolder2__S1.xml"`
    - `.path(CutOrder.BY_SLIDE) == "slide_S1/S1__TubeHolder2.xml"`
    - `cutting_instructions([...], CutOrder.BY_PLATE, plate="tubes")` contains `"Load TubeHolder2"`, `"tubes A–D"` and `"tube holder"`

    Build the `Cut` with lightweight stand-ins: a `SimpleNamespace` plan with `calibration_names` and `wells_used`, and a result with `n_shapes`.
- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_collectors.py -v`.
- [ ] **Step 3: Implement the export changes.**
  - `build_bundle` CSV name: `f"{stem}_{plate}_wellplate.csv"` when `collector(plate).numbered`, else `f"{stem}_{plate}.csv"`.
  - `build_experiment_bundle` writes `f"{name}.csv"` and adds `"collector": collector(plate).label` to `provenance["experiment"]`.
  - `cutting_instructions`:
    - header `count(len(plates), c.noun)`
    - collector steps say `f"Load {name}"`, followed by ` (tubes A–D)` / ` (wells A–H)` for unnumbered collectors
    - the detail line counts `c.position` instead of "well"
- [ ] **Step 4: Verify.** Run `uv run pytest`, expecting PASS, and `uv run python tools/golden_harness.py check`, expecting all 21 identical.
- [ ] **Step 5: Commit** with `facts.md`: "Name experiment files after the collector".

---

### Task 4: Golden cases for tubes and strips

**Files:**
- Modify: `tools/golden_harness.py` (`CASES`; `run_plates_case(source, replicates=1, per_replicate=1, plate_type="96")`)
- Create: `tools/golden/tubes.xml`, `tubes.csv`, `strips.Strip1.xml`, `strips.Strip1.csv`, `strips.Strip2.xml`, `strips.Strip2.csv` (all via `capture`)
- Docs: `facts.md` (golden section: 11 cases, 27 artefacts, counted after capture)

The spec suggested `Single_cells` exploded for strips. That path (`run_case`) only ever builds one collector. The several-collector path is `run_plates_case`, so the strips case uses it with `multiclass_cells`: nine samples over 8-well strips gives two strips.

- [ ] **Step 1: Add the cases.**
  - `"tubes": {"source": DEMO / "TD_01_verysmall_mIF.geojson", "plate_type": "tubes"}`
  - `"strips": {"kind": "plates", "source": DEMO / "multiclass_cells.geojson", "replicates": 3, "per_replicate": 2, "plate_type": "strip"}`

  In `run_plates_case`, keep `acceptable_wells("96", margins=3, step_col=2)` for `plate_type == "96"`; any other type uses `acceptable_wells(plate_type)`. Pass `plate=plate_type` to `assign_to_plates` and `cuts_for_experiment`.
- [ ] **Step 2: Capture.** Run `uv run python tools/golden_harness.py capture`, then `git status --short tools/golden`. Expected: only the six new files, untracked, and **no modified files**. If any existing file shows as modified, stop. A plate output changed, which is a regression and must not be blessed.
- [ ] **Step 3: Inspect.**
  - `grep -o "<CapID>[^<]*</CapID>" tools/golden/tubes.xml | sort -u` should give exactly A, B, C, D.
  - The same on `strips.Strip*.xml` should give letters within A–H.
  - Strip1 + Strip2 should hold all 9 samples (from the CSVs).
- [ ] **Step 4: Verify.** `uv run python tools/golden_harness.py check` passes, and so does `uv run pytest` (the `test_golden.py` slow gate included).
- [ ] **Step 5: Commit.** "Add golden cases for tube holders and strips". The body says these are **new references, blessed deliberately**, that no existing reference changed, and why the bytes are right: letter CapIDs, all samples placed.

---

### Task 5: Stage 3 · Collector in the UI

**Files:**
- Modify: `src/qupath_to_lmd/ui_plates.py` (all of `settings_step`, `_plates_control`, `_custom_assignment`, `render`, `DISTRIBUTION_LABELS`)
- Modify: `src/qupath_to_lmd/ui_shared.py:158-260` (`plate_preview`, `editable_plate`)
- Modify: `src/qupath_to_lmd/ui_summary.py`, `src/qupath_to_lmd/ui_cut.py` (user-facing "plate" wording only), `streamlit_app.py` (stage comments)
- Modify: `tests/test_ui_behaviour.py`, `tests/test_nomenclature.py:66` (add `"collector"` and `"tube"`)
- Docs: `GLOSSARY.md`, `README.md`, `facts.md` (pipeline step 2, session-key table: widget `plate_type` values)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `settings_step()` returns the same dict keys as today, with `"plate_type"` holding a collector key. `_plates_control(samples, settings)` reads `settings.get("plate_type", "384")`. `PlateLayout` is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_ui_behaviour.py`:
  - `test_margin_and_spacing_are_not_offered_for_tubes`:
    - stub `selectbox` to return `"tubes"`
    - make `number_input` raise `AssertionError("margin/spacing offered for tubes")` when its label contains "Margin" or "Space"
    - stub `text_input` → `""`, `toggle` → `False`
    - `settings_step()["usable"] == ["A", "B", "C", "D"]`
  - `test_several_collectors_warn_about_separate_cutting_runs`: `render` path. Call the capacity block through `ui_plates.render` with a `SampleSet` stand-in holding 13 samples and tubes chosen. Stub `tabs`, `container`, `checkbox` → `False`, `download_button`, `expander`, `file_uploader` → `None`. Assert `"4 separate cutting runs"` is in `fake_streamlit.shown("warnings")`. If `render` needs too much stubbing, extract the capacity message into `_capacity(samples, settings, n_plates, layout)` and test that.
  - `test_one_collector_does_not_warn_about_cutting_runs`: the same, with 3 samples, gives no "separate cutting runs" warning.
  - `test_hundreds_of_samples_on_tubes_do_not_exceed_the_collector_limit` (Review Focus 3): record the `number_input` kwargs; with 300 samples on tubes, `kwargs["max_value"] >= kwargs["value"] == 75`.
  - `test_a_file_for_plates_is_refused_when_tubes_are_chosen`:
    - stub `expander`
    - make `file_uploader` return `io.BytesIO(b"{'P1': {'Tumor_r1': 'C3'}}")`
    - `_custom_assignment(["Tumor_r1"], "tubes") is None`
    - an error mentions "tube holder"
  - `test_the_tube_caption_counts_tubes_on_the_named_holder`: `plate_preview({"a": "A", "b": "C"}, "tubes", wells=["A", "B", "C", "D"], plate_name="TubeHolder2")`. The caption contains `"2 of 4 tubes in use on TubeHolder2"` and `"start at **B**"`.
- [ ] **Step 2: Run the tests and confirm they fail.** Run `uv run pytest tests/test_ui_behaviour.py -v`.
- [ ] **Step 3: Implement `ui_plates.py`.**
  - Title `"## 3 · Collector"`.
  - The selectbox is labelled `"Collector"`, with `options=list(plate.COLLECTORS)`, `format_func=lambda key: plate.COLLECTORS[key].label` and `key="plate_type"`.
  - Margin and step widgets appear only when `c.spacing`; otherwise `margin, step_row, step_col = 0, 1, 1`. The intro sentence about margins is shown for plates only.
  - Start label `f"Start at {c.position}"`. Randomize label `f"Randomize {c.position}s"`.
  - `_plates_control`:
    - label `f"{c.noun.capitalize()}s"`
    - `max_value=max(50, needed)`
    - help text and distribution labels use `c.noun`
  - `render`:
    - capacity line `f"These {n} samples need **{n} {c.position}s**. "` followed by `f"This {c.noun} offers **{offered}**."` or `f"{n_plates} {c.noun}s offer **{offered}**."`
    - when `n_plates > 1`, `st.warning(f"That is **{n_plates} separate cutting runs**, one `.xml` each, with the {c.noun} changed between them.")`
    - the unplaced warning says `f"Add a {c.noun}"` and mentions margin/spacing only when `c.spacing`
    - the first-collector check becomes `name == names[0]`
    - the all-collectors download is labelled `f"all {c.noun}s"`, with file name `f"samples_and_wells_all_{c.noun.replace(' ', '_')}s.json"` (unchanged for plates)
  - `_custom_assignment`:
    - passes `plate=plate_type`
    - examples use `C3` or `C` and `f"{c.name_prefix}1"`
    - the invalid-labels error reads `f"these {c.position}s do not exist on a {c.label}: …. Fix the file or change the collector."`
- [ ] **Step 4: Implement `ui_shared.py`.**
  - `plate_preview`:
    - sorts used positions with `split_position` (`(row, column or 0)`)
    - caption `f"{len(saw)} of {len(wells) if wells else capacity} {c.position}s in use on {plate_name or 'a ' + c.label}"`
    - "into this {c.noun}" / "This {c.noun} is now full"
    - the usable-wells expander appears only when `c.spacing`
    - download label `f"current {c.noun}"`
  - `editable_plate`:
    - checkbox `f"Move samples between {c.position}s by hand"`
    - `layout_to_saw(edited, plate_type)`
    - noun in messages
  - Keep the existing tests `test_the_plate_caption_names_a_well_that_is_actually_free` and `test_a_full_plate_says_so_rather_than_naming_a_well` green: they look for `"start at"` and `"full"`.
- [ ] **Step 5: Implement the remaining UI wording.**
  - `ui_summary`: stage `"Collector"`, emoji 🧫 kept, figure `f"**{n} {c.noun}{'s' if n != 1 else ''}**"`, line `f"{placed} of {n_samples} samples placed on {n} × {c.label}"`.
  - `ui_cut`: user-facing "plate" becomes `c.noun` where it means the collector (intro text, the "cutting files" line, the `_cut_order` labels). Check every hit of `grep -n -i plate src/qupath_to_lmd/ui_cut.py`. The `samples.csv` columns `plate`/`well` stay, because that is the data format.
  - `streamlit_app.py`: stage comments only.
- [ ] **Step 6: Docs.**
  - `GLOSSARY.md`:
    - **collector**: the holder the LMD cuts into, which is a plate, a tube holder (4 tubes, `A`–`D`) or a strip holder (one 8-well strip, `A`–`H`); one per experiment; named `Plate1`/`TubeHolder1`/`Strip1`; in code it is still called `plate`/`plate_type` for historical reasons
    - **plate** becomes one kind of collector
    - **tube** is new
    - **well**: "a position on a plate or strip"
    - add a collector row to the source table (`Leica: collector`)
  - `tests/test_nomenclature.py:66`: add `"collector"` and `"tube"`.
  - `README.md`: stage 3 is "Collector", list the four collectors, file names `Plate1/`, `TubeHolder1.csv`, and the line "Works for both 384-well plates and 96-well plates" (README:155) extended.
  - `facts.md`: pipeline step 2, the stage list, the key-table note that `plate_type` holds `384|96|tubes|strip`.
- [ ] **Step 7: Verify.**
  - `uv run pytest` passes.
  - `uv run ruff check` on the touched files is clean.
  - The golden check passes.
  - Smoke boot with `uv run streamlit run streamlit_app.py --server.headless true --server.port 8599`. The log shows no exception.
  - Exercise the AppTest path: if `tests/test_app.py` drives Stage 3 by `plate_type` label, update it to the key.
- [ ] **Step 8: Commit** "Make Stage 3 a collector choice: plates, tube holders, strips".

---

### Task 6: Replicate rings that do not repeat the class colours

**Files:**
- Modify: `src/qupath_to_lmd/plot.py:41-105` (`CLASS_FILL_TINT`, `REPLICATE_SHADE`, `REPLICATE_COLORMAP` → `REPLICATE_PALETTE`, `replicate_colors`)
- Modify: `tests/test_plot.py:54-120`
- Docs: `decisions.md` (append 082, superseding 070/080 on ring colours and the fill tint), `facts.md` (plot section, if it states the tint or shade)

**Interfaces:**
- Produces: `REPLICATE_PALETTE: list[str]` of hex colours. `replicate_colors(replicates) -> dict[int, tuple]` cycles over it, keyed by replicate number as today.

- [ ] **Step 1: Run the scan in the scratchpad** (not committed). Candidates are matplotlib's CSS4 named colours plus black and white. Fills are `_tint(PALETTE[i], t)` for `t` in `0.0, 0.05, …, 0.6`. For each `t`, greedily build the largest outline set (up to 10) where:
  - every outline × every fill has WCAG contrast ≥ 1.7
  - every outline × every *strong* class colour has CIE76 ΔE ≥ 25 (sRGB → Lab by the standard D65 formula, written in the script)
  - outlines are ≥ 0.18 apart in RGB

  Prefer the lowest `t` that yields at least 6 outlines. Record `t`, the colours, and the three measured minima.
- [ ] **Step 2: Checkpoint with Jose.** If no `t ≤ 0.35` yields 6 outlines, stop and show the scan's best options, plus the alternative of a thin white under-stroke behind each ring (matplotlib `patheffects`). Do not lower a floor on your own. Otherwise, render the regions-and-circles picture of `demo_Qupath_project/demo1/P1_S1.geojson` (2 replicates, as `tools/golden_harness.run_packing_case` does, without writing an XML) at the old and new constants. Save both PNGs to the scratchpad and show them to Jose. Continue only after his OK.
- [ ] **Step 3: Write the failing tests** in `tests/test_plot.py`:
  - Keep `test_a_replicate_keeps_its_colour_when_another_class_changes`.
  - Change `test_replicate_colours_cycle_rather_than_run_out` to use `len(plot.REPLICATE_PALETTE)`.
  - `test_an_outline_is_always_visible_against_every_fill`: keep it with floor 1.7, over `range(1, len(REPLICATE_PALETTE) + 1)`.
  - `test_replicates_stay_distinguishable_from_each_other`: keep it at 0.18.
  - Add `test_a_ring_never_repeats_its_class_colour`: min ΔE between every outline and every `plot.PALETTE` colour ≥ 25. Message: "a replicate ring would be hard to tell from the class fill under it — it would read as part of the class, not as a replicate".
  - Add `test_circles_are_filled_close_to_their_class_colour`: `plot.CLASS_FILL_TINT <= 0.35`. Message: "circle fills go back to washed out, which Jose asked to fix".

  Put the shared `_delta_e` / `_lab` helpers next to `_contrast`.
- [ ] **Step 4: Run the tests and confirm they fail.** Run `uv run pytest tests/test_plot.py -v`. Expected: the new ΔE test FAILS against tab10-shaded rings.
- [ ] **Step 5: Implement the palette in `plot.py`.** `REPLICATE_PALETTE` is the literal list from the scan. `CLASS_FILL_TINT` is the scan's `t`. Remove `REPLICATE_SHADE` and `REPLICATE_COLORMAP` if nothing else uses them (`grep`). Rewrite the comment block at `plot.py:41-59` with the measured minima and `decisions.md` 082.
- [ ] **Step 6: Verify.** Run `uv run pytest` (passes) and `uv run ruff check src/qupath_to_lmd/plot.py tests/test_plot.py` (clean). No golden impact.
- [ ] **Step 7: Commit** "Give replicate rings colours apart from every class; stronger circle fill", with `decisions.md` 082 and `facts.md`.

---

### Task 7: Whole-branch verification and handover

- [ ] **Step 1: Run the full verification.** `uv sync`; `uv run pytest`; `uv run python tools/golden_harness.py check` (27 artefacts identical); `uv run ruff check` on every file the branch touched (`git diff --name-only origin/dev -- '*.py'`).
- [ ] **Step 2: Smoke boot** on port 8599 and confirm no exception in the log.
- [ ] **Step 3: Whole-branch review.** Use `superpowers:requesting-code-review` against the spec. Fix what it confirms, with one commit per fix.
- [ ] **Step 4: Hand over a numbered manual test script.** It covers:
  - `TD_01_verysmall_mIF.geojson` into a tube holder: margin and spacing hidden, 4 tubes A–D, `<CapID>A</CapID>` in the `.xml`, `_tubes.csv`
  - `Single_cells.geojson` with selected shapes into strip holders: several strips, the "separate cutting runs" warning, `Strip1/`, `Strip2/` folders, `COLLECTION_PLAN.txt` saying "Load Strip2 (wells A–H)"
  - uploading `demo_Qupath_project/demo_samples_and_wells.txt` with tubes chosen: an error naming the invalid positions
  - reloading a two-plate `samples_and_wells.json` from an old download (`P1`/`P2`) with a plate chosen: loads as Plate1/Plate2
  - the regions-and-circles picture on demo1: rings distinct from fills
- [ ] **Step 5: Say "ready to push" and stop.**
