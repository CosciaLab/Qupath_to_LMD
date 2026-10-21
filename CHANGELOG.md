# Changelog

What changed for you, release by release. Newest first.

Terms are defined in [GLOSSARY.md](GLOSSARY.md); the reasoning behind each choice is in
`decisions.md`.

---

## v5.1.0

- **Expand shapes before cutting.** A new option in the Cut stage moves every outline outward by a
  distance you choose — 1 µm by default — so the laser does not burn the edge of the cell you are
  collecting. Off unless you tick it. Before you download, the app shows how much area it adds,
  whether expanded shapes now overlap shapes going to another well, and one shape before and after.
- **Spaces in class names just work.** `Immune cells` stays `Immune cells`. Spaces nobody can see —
  at the start or end of a name, doubled, or pasted in from a spreadsheet — are tidied when the file
  is read, so `Tumor ` and `Tumor` are one class; the reading table says when that happened. Names
  that differ only in capitals, spaces, `_` or `-` (`Tumor` and `tumor`) are pointed out, since they
  go into separate wells, and a samples-and-wells file that misspells a sample says which one.
- **A warning about long or unusual file names.** The file name is reused for the `.xml` and the
  folders in your download, and Windows will not unzip very long paths. Names over 60 characters,
  or with characters such as `%`, `#`, `&` or accents, get a warning and a suggested name to rename
  the file to. Nothing is blocked or renamed.

## v5.0.0

### Polish after the first click-through

- **Confirm each slide's calibration points.** The suggested points are only a suggestion; tick
  *These are the right calibration points* on every slide to continue. Confirmed slides get a ✅.
- The **classes table** is full width, with the picture below it — no more sideways scrolling.
- **Plate files carry the plate's name** (`samples_and_wells_P1.json`), there is a button for all
  plates at once, and any of them — or the `samples_and_wells.json` from a download — loads back
  into the Plates stage to change an experiment later.
- *Order to cut in* is now **Organise the download**: it only arranges folders and names; the
  `.xml` files are the same either way. `HOW_TO_CUT.txt` is now `COLLECTION_PLAN.txt`.
- **Extras** have their own page — *Extras* at the top, beside *Collect*.
- **Every picture colours a class the same way** (tab20), including the preview of what will be
  cut, which is now coloured by class.
- New demo data, `demo_Qupath_project/demo1/`: two patients, three serial sections each.

### Several slides, several plates, one page

- **Upload several slides at once** — several `.geojson` files, or a `.zip` of them. Each slide
  keeps its own calibration points and image scale, and gets its own `.xml`.
- **Shapes of the same class on different slides go into the same well**, so a well can hold more
  tissue than one slide has. The app says so as soon as you add a second slide, and names any class
  that is missing from a slide (usually a spelling difference).
- **Choose where an amount comes from**: in proportion to what each slide holds (the default), an
  equal share from each, or the slides in an order you set.
- **More samples than one plate holds?** The app asks for as many plates as you need and spreads
  the samples so that every plate holds every class — so a difference between plates can never
  look like a difference between classes. Filling plate 1 first is still an option.
- **One page, four stages**: Slides → Samples → Plates → Cut. All three ways to collect use the same
  stages, and the sidebar shows your experiment at a glance.
- **The download explains itself**: `samples.csv` (what each well gets from each slide),
  `COLLECTION_PLAN.txt` (numbered steps in the order you chose — slide by slide or plate by plate), and
  every `.xml` named for its slide and plate.
- The annotations workflow is now **Whole shapes**, and no longer needs a *Confirm* click — the
  plate updates as you change it. What it cuts is unchanged, byte for byte. Your own
  samples-and-wells file is now an option of the plate stage.
- If you change anything after processing, the old download is withdrawn until you process again,
  so you cannot download files that no longer match the screen.

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

### Fixes after the first run on real data

- **Cuts that sit close together now go into the same well.** Two cuts a hair apart can shed the
  tissue between them; into one well that is harmless, into two it is contamination between
  replicates. Where that is unavoidable — across a boundary between two classes, which always go
  to separate wells — the app now tells you how many cuts are affected.
- **The tissue map is readable.** Regions are drawn at full strength instead of washed out, and
  the calibration triangle no longer sits on top of them.
- **Projecting regions is about a third faster**, and the app skips work it can prove it does not
  need. A whole-slide file is still slow; you are warned before you wait.

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
