# Changelog

What changed for you, release by release. Newest first.

Terms are defined in [GLOSSARY.md](GLOSSARY.md); the reasoning behind each choice is in
`decisions.md`.

---

## Unreleased

### New workflow: cellular neighbourhoods

A third option on the workflow step, for when a single cell is too little tissue but you still
want to collect by cell type.

Each cell is given the tissue nearest to it — up to a distance you set — and touching cells of
the same class are merged into one **region**. Then, in one table with a row per class, you set
how many replicates you want, how much tissue goes into each, the size range of the circles and
the gap to leave between cuts. The app fills the regions with circles until it reaches your
amount.

- **Settings and picture side by side.** The table sits above a live drawing of what you are
  about to cut: a pale fill is the class, a dark outline is the replicate. Change a number and
  see what it did.
- **Per class, not global.** A sparse, stringy class needs smaller circles than a solid one
  before anything fits at all.
- **Reproducible.** The same seed and settings give the same circles, so a collection can be
  repeated in a later session and reported in a methods section. Everything you set is saved in
  the download.
- **It tells you what will not fit, before you wait for it.** Randomly placed circles cover
  about 55% of a region at best, and the gap between them cuts that down a lot more — a 5 µm gap
  roughly halves it and 20 µm quarters it. You see each class's real capacity up front, and what
  each replicate actually got afterwards.
- **Or collect the whole regions**, if you want all of a neighbourhood rather than a measured
  amount of it.

Two things the app now says out loud, because neither is recoverable from the `.xml`:

- A region reaches past the cell outlines QuPath drew, because it covers the space between the
  cells too. One setting controls how far, and gaps wider than twice that are left uncollected.
- Smoothing shaves a little area off every circle. If that adds up to more than a few percent
  the app says so, and how to avoid it.

### Everywhere

- **You can see your input.** Step 4 now draws every shape in the file beside the class table,
  coloured where you are keeping the class and grey where you are not — so you can tell at a
  glance that you uploaded the file you meant to. Both the cell and neighbourhood workflows.
- **Amounts read like amounts.** `428,955 µm²`, not `428955.48`. Every table across every
  workflow, with the unit in the column header.
- **Sensible starting numbers.** Both workflows now start at 3 replicates of 25,000 µm² — or
  150 cells. The old default offered you the entire class, which meant the "can this class
  supply what you asked for?" check could never tell you anything.

---

## 4.0.0

Two workflows on one backend. The annotations workflow is unchanged; a second one is for cell
segmentation.

### Cell segmentation

For QuPath files with thousands of classified cells. Choose your classes, set replicates and how
much goes into each — a number of cells, or µm² — and the app picks the cells:

- spread across the tissue, so a replicate is not one corner of the slide
- optionally avoiding cells that touch another cell you are collecting
- cells below a minimum area (100 µm² by default, per class) left out, so the amounts offered are
  amounts you can actually collect
- a preview of exactly which cells were chosen, before you export

### Easier setup

- **The image scale is worked out from your file** instead of being asked for, and where it is
  still needed it sits next to the control that uses it.
- **Start at well** fills the plate from a well you choose, for collecting several slides into
  one plate. Well assignment can also be randomised, or edited by hand.
- **Loading a file gives you a table**, not a stack of warnings: what is in it, what will be
  ignored and why, and how many shapes are ready to collect.

### Shorter, straighter cut paths

Shapes are cut grouped by well with the path shortened inside each well, rather than in whatever
order QuPath exported them. On 900 shapes across 9 wells that is **759 collector movements down
to 8**, and 38% less stage travel — stage movement between shapes is a leading cause of cutting
misalignment. Smoothing tolerance and cutting order are both yours to set, and the previous order
is still available as *"As loaded — no reordering"*.

### Fixes

- A file with no calibration points explains itself instead of crashing; missing or degenerate
  calibration points stop the run, because everything downstream would be meaningless.
- Cells carrying more than one QuPath class no longer break the plate layout; each combination
  becomes its own class, joined by `--`.
- Calibration points you forgot to name are reported, instead of being dropped while the app
  claimed there were none.

### For collections made with v3

Coordinates, well assignments and the calibration transform are identical to v3 — verified
shape-for-shape. The only difference is the order shapes are written in, and the v3 order remains
selectable.

---

## Earlier versions

v3 and before predate this file. See the
[tags](https://github.com/CosciaLab/Qupath_to_LMD/tags) for what shipped when.
