# Collectors: tube holders and 8-well strips beside plates — design

**Date:** 2026-10-08 · **Branch:** `feat/collectors` (one branch, one PR into `dev`)
**Also in this branch:** replicate outline colours that no longer repeat the class colours (part B).

---

## Part A — collectors

### Intent

Scientists collect into more than plates. The Leica LMD7 has a **tube holder** for four Eppendorf
tubes and a **strip holder** for one 8-well PCR strip. The app should cut into either, with
the same safety the plate path has: the user can see, before downloading, what goes where and how
many cutting runs that takes.

What Jose said:

- Stage 3 is the collection setup, and "Plates" is no longer the right name for it. The general
  word is **collector**, Leica's own term.
- A **holder plays the part of a plate**: one tube holder (4 tubes) or one strip holder (1 strip,
  8 wells) is one collector. One tube is *not* the same as one plate.
- CapIDs are letters only: `A`–`D` for the tube holder, `A`–`H` for the strip.
- **One kind of collector per experiment.** No mixing plates and tubes.
- Collectors are named **`Plate1`, `Plate2`, …; `TubeHolder1`, …; `Strip1`, …**. They are
  explicit on purpose, since most experiments have only a few.
- No 1536-well plate. Nobody has needed one.
- No new data format.

Assumed and agreed during brainstorming:

- Stage 3 is where the collector is chosen, and nothing upstream changes. Samples stay
  `Tumor_r2`, and each sample still goes to one position.
- More samples than one collector holds works the way several plates already do: the user picks
  balanced or sequential, and there is one `.xml` per slide × collector.

### Success criteria

1. A user can pick *Eppendorf tube holder (4 tubes)* or *8-well strip holder* in Stage 3 and get
   a download whose `.xml` files carry `<CapID>A</CapID>` … `<CapID>D</CapID>` (or `…H`).
2. Every existing plate output stays byte-identical: `tools/golden_harness.py check` passes on all
   nine existing cases without re-blessing.
3. The number of collectors, and so the number of separate cutting runs, is on screen before
   download.
4. A samples-and-wells file with labels that do not exist on the chosen collector is refused with
   a message that names the bad labels.
5. Every `samples_and_wells.json` downloaded before this change still loads.

### Out of scope

- Mixing collector types within one experiment.
- The 1536-well plate.
- Renaming `plate` to `collector` in code identifiers, module names and session-state keys
  (`plate.py`, `plate_type`, `n_plates`, `plate_distribution`). That would be a separate
  `refactor/` PR if Jose wants it. Meanwhile **collector** is the word on screen and in docs, and
  `GLOSSARY.md` records that `plate` in code identifiers means the collector for historical
  reasons.

### Approach

A **collector registry** in the library layer. Two alternatives were rejected:

- *Tubes as a one-column plate, with the `1` stripped at export.* It gives each position two
  names, `A1` in the app and `A` in the XML, a quiet mismatch of exactly the kind that sends
  tissue to the wrong cap.
- *A full `plate` → `collector` rename now.* It is a drive-by refactor that would bury the real
  change (CLAUDE.md §9).

### Library layer — `plate.py`

```python
@dataclass(frozen=True)
class Collector:
    key: str            # "384" | "96" | "tubes" | "strip" — the value of plate_type
    label: str          # "384 well plate", "Eppendorf tube holder (4 tubes)", "8-well strip holder"
    rows: int
    columns: int        # 1 for tubes and strips
    numbered: bool      # True: positions are "C3"; False: positions are "C"
    name_prefix: str    # "Plate" | "TubeHolder" | "Strip"
    noun: str           # "plate" | "tube holder" | "strip holder"   (for messages)
    position: str       # "well" | "tube" | "well"                    (for messages)
    spacing: bool       # margin and row/column spacing apply (plates only)

COLLECTORS = {"384": ..., "96": ..., "tubes": ..., "strip": ...}
```

- `PLATE_SHAPES` is replaced by `COLLECTORS`, and `plate_dimensions(key)` reads from it. The
  existing keys `"384"` and `"96"` keep their meaning, so all callers and every default of
  `plate="384"` keep working.
- **One parser for a position**, `split_position(label, collector) -> (row, column | None)`,
  replaces every `well[0]` / `well[1:]` in `placement_dataframe`, `sample_layout`,
  `layout_to_saw`, `ui_shared.plate_preview` and `ui_shared.editable_plate`. Rows remain a single
  letter, because no supported collector has more than 16 rows.
- `acceptable_wells(key, margins, step_row, step_col)`: for a collector with `spacing=False`, the
  margin and steps are ignored and every position is returned (`A`–`D`, `A`–`H`). Positions are
  generated row by row, so `A, B, C, D` is the plate order of a tube holder.
- `default_layout`, `placement_dataframe`, `sample_layout`: tubes and strips are a single column,
  with rows `A`–`D` or `A`–`H` and a column header that names the position kind.
- `plate_names(n, key)` gives `Plate1…`, `TubeHolder1…` or `Strip1…`.
- `per_plate` sorts on the trailing number of the name instead of `int(name[1:])`, which would
  break on `Plate1`.
- `assignment_from_scheme(parsed, key)` reads:
  - a flat `{sample: position}`, which goes to collector 1 of the chosen type
  - `{"Plate1": {...}}`, `{"TubeHolder1": {...}}` or `{"Strip1": {...}}`, which must use the
    prefix of the chosen collector
  - **legacy `{"P1": {...}}`**, read as `Plate1…`, but only when a plate is chosen

  A prefix for a different collector raises `SawParseError`, and the message names both: "this
  file is for tube holders, but a 384 well plate is chosen".
- `assign_to_plates`, `assign_wells`, `wells_from` and `plates_needed` stay unchanged. They work
  on any list of labels. `assign_to_plates` takes the collector key so it can name collectors.
- `qc.validate_saw(scheme, classes, plate=key)` checks positions against the registry. So `C3`
  on tubes, `E` on tubes and `I` on a strip are all `invalid_wells`.

### UI layer — Stage 3 · Collector (`ui_plates.py`)

- The stage title becomes **"3 · Collector"**, and the sidebar stage list, emoji and summary line
  follow it. The module name stays `ui_plates.py`, in keeping with the rename being out of scope.
- **The collector selectbox comes first**, labelled *Collector*, with options from `COLLECTORS`.
  The widget key stays `plate_type`.
- **Margin and row/column spacing are hidden** when `spacing=False`. On a 4-tube holder they do
  nothing, and a control that is shown disabled invites the question "why can't I?". *Start at*
  and *Randomize* stay for every collector, with the label following the position kind ("Start
  at tube", e.g. `C`).
- The number of collectors is labelled per device ("Plates", "Tube holders", "Strip holders"),
  defaults to the number needed, and keeps `max_value=50`. The distribution radio is unchanged.
- **Visible before download (CLAUDE.md §3):** the capacity line reads, for example, "These 13
  samples need **13 tubes**. **4 tube holders** offer 16." With more than one collector there is
  an `st.warning`: "That is **4 separate cutting runs**, one `.xml` each, with the tube holder
  changed between them." It warns and never blocks.
- Previews and the hand editor use the same `plate_preview` / `editable_plate`. Tubes and
  strips render as one column. Captions read "3 of 4 tubes in use on TubeHolder2".
- The custom-upload expander's help text shows a flat example and a collector-keyed example for
  the chosen collector. An unreadable file, a mismatched prefix or invalid labels each give an
  `st.error` and fall back to the generated layout. That is today's behaviour for `K5` on a 96
  plate, and it adds no `st.stop()`.
- The existing `st.error` + `st.stop()` for "no usable wells" can only fire for plates, because
  tubes and strips ignore the margin. Its wording becomes collector-neutral. It is not a new stop.
- `ui_summary`, `ui_cut` and `COLLECTION_PLAN.txt` say "tube holder" / "strip holder" / "plate"
  through `Collector.noun`, e.g. "Load TubeHolder2 (tubes A–D)".

### Export — `export.py`, `slides.py`

- `<CapID>` is the position label exactly as stored (`C3`, or `C`). py-lmd writes it verbatim
  (`lmd/lib.py:451`), so py-lmd needs no change.
- **Experiment bundle** (several slides or collectors). The redundant prefix goes:

  | before | after |
  | --- | --- |
  | `plate_P1.csv` | `Plate1.csv`, `TubeHolder1.csv`, `Strip1.csv` |
  | `plate_P1/P1__<slide>.xml` | `Plate1/Plate1__<slide>.xml` |
  | `slide_<S>/<S>__P1.xml` | `slide_<S>/<S>__Plate1.xml` |
  | `samples_and_wells.json` keyed `P1` | keyed `Plate1` / `TubeHolder1` / `Strip1` |

  This changes the **file names** of multi-plate plate downloads, but not their XML or CSV
  contents. It is recorded in `decisions.md`.
- **Single-collector bundle** (`build_bundle`, the frozen whole-shapes path). Plates keep
  `<stem>_384_wellplate.csv` / `_96_wellplate.csv`, unchanged. Tubes write `<stem>_tubes.csv`,
  strips `<stem>_strip.csv`.
- Provenance records `collector: <key>` alongside the existing `plate` entry, so existing keys
  keep their meaning.

### Tests

New file `tests/test_collectors.py`. Every assertion message says what goes wrong at the
microscope.

- The registry gives `A B C D` for tubes and `A … H` for strips, and 384/96 are unchanged
  against the old `acceptable_wells` output for a grid of margins and steps.
- Tubes and strips ignore margin and spacing.
- 13 samples over tube holders: 4 holders, balanced, named `TubeHolder1…4`, labels `A`–`D` only.
- `per_plate` and `assignment_from_scheme` round-trip for each collector, legacy `P1` loads as
  `Plate1`, and a `TubeHolder1` file with a plate chosen raises.
- `validate_saw` rejects `C3` and `E` on tubes and `I` on a strip.
- A built collection for tubes writes `<CapID>A</CapID>` in the XML.

Changed files:

- `tests/test_experiment.py`: `P1`/`P2` become `Plate1`/`Plate2` where names are asserted.
- `tests/test_ui_behaviour.py`: margin and spacing are absent for tubes, the "separate cutting
  runs" warning appears with more than one collector, and a mismatched-prefix upload shows an
  error.
- `tests/test_nomenclature.py`: **collector** becomes a canonical term.

### Golden harness

- The nine existing cases must pass unchanged, with nothing re-blessed. The harness names the
  `two_plates` artefacts from the collector name (`tools/golden_harness.py:256`), so after the
  rename it would look for `two_plates.Plate1.xml`. The harness maps `Plate<n>` back to `P<n>`
  for artefact names, keeping the existing reference files (`two_plates.P1.xml`) as they are.
  Renaming files in `tools/golden/` would count as hand-editing them, and that is forbidden.
- Two new cases, captured and explicitly blessed in their commit:
  - `tubes`: `TD_01_verysmall_mIF.geojson`, 4 classes into exactly one tube holder, `A`–`D`.
  - `strips`: `Single_cells.geojson`, exploded, which takes several strip holders and so
    exercises many collectors and the collector names.

### Docs

- `decisions.md` **081**: the collector registry, the rejection of the one-column-plate
  approach, the name scheme and legacy `P1` reading, hiding instead of disabling controls, the
  deferred code rename, and the multi-plate file-name change.
- `facts.md`: layout, domain constants (tube holder and strip), the vocabulary section,
  multi-plate file names, and the widened values of `plate_type` in the key table.
- `GLOSSARY.md`: **collector** (new), **plate** (now one kind of collector), **tube** (new), and
  the `plate`-in-code note. The "where the terms come from" table gets a row for collector.
- `README.md`: Stage 3 is called *Collector*, and the device choice and file names are described.

---

## Part B — replicate outlines that do not repeat the class colours

### Problem

In `plot_regions_and_circles` (the regions-and-circles picture of the regions method), class
fills come from **tab20**, whose first ten strong shades *are* **tab10**, and replicate outlines
are **tab10** darkened by 25 % (`REPLICATE_COLORMAP`, `REPLICATE_SHADE`). So replicate 1's ring is
a darker blue on class 1's blue fill, replicate 2's a darker orange on class 2's orange, and so
on. Jose: "the replicate outline color matches the classes making a mess out of the colors."
Circle fills are also tinted 60 % toward white (`CLASS_FILL_TINT = 0.6`), which reads as washed
out.

### Change

1. **Replicate outlines come from a fixed palette of high-contrast colours chosen to be far from
   every class colour.** They are no longer a darker copy of the class palette. Candidates are
   scanned with a script in the scratchpad, which is not committed, against the 18 colours of
   `PALETTE` at the new fill tint. Selection criteria:
   - the largest smallest CIELAB ΔE between any outline and any class fill
   - outlines clearly apart from each other
   - WCAG contrast of every outline against every fill at or above the current floor of 1.7

   The resulting colours are written as a literal list in `plot.py`, with a comment giving the
   measured minima, so the numbers do not depend on a colormap's internals. Replicates beyond the
   list cycle, as they do today.
2. **The circle fill becomes less transparent.** `CLASS_FILL_TINT` drops from 0.6 to whatever
   value the same scan shows still keeps the floors (expected around 0.2–0.3), so a circle reads
   as its class colour.
3. Regions are unchanged (`REGION_FILL_ALPHA = 0.85`).

### Verification

- A test in `tests/test_plot.py` (new, or an existing plot test) computes the smallest ΔE and
  WCAG contrast over every outline × fill pair and fails if either drops below the floor chosen
  in the scan. Its message reads "a replicate ring would be hard to tell from the class fill
  under it".
- Before committing, Jose gets a before/after PNG of the demo1 regions picture to look at.
- No golden impact: the harness compares XML and CSV only.
- `decisions.md` **082** supersedes the colour choices of 070 and 080 for circle outlines and fill
  tint, with the measured numbers.

---

## Delivery

- Branch `feat/collectors` from `origin/dev`. Small commits: spec → library → UI → export and
  bundle → golden cases → colours → docs.
- Before each commit: `uv run ruff check <touched files>`, `uv run pytest`, and
  `uv run python tools/golden_harness.py check`. The app is smoke-booted on port 8599.
- A numbered manual test script for Jose covering tubes, strips, a multi-plate legacy reload,
  and the colour picture.
- I stop at **"ready to push"**. CLAUDE.md §1 forbids pushing and opening a PR, while a saved
  memory says pushing and PRs to `dev` are allowed. CLAUDE.md wins unless Jose says otherwise.
