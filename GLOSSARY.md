# Glossary

One word per thing. The app, its messages and its code use these terms consistently; where
QuPath or py-lmd use a different word for the same thing, that is noted.

Written because the same object was being called a *shape*, a *polygon*, an *object* and a
*contour* in different parts of the app — sometimes within a few lines of each other.

---

## The thing being cut

**shape** — one outline that the laser will cut. This is the app's word for it, from upload
through to the XML, and it is what py-lmd calls them too (`new_shape`, `Collection.shapes`).
Counts in the interface — "8537 shapes available", "100 shapes per replicate" — always mean
this.

**object** — QuPath's word. Used in **code and documentation** where the distinction matters —
QuPath's interface says annotation objects, cell objects and detection objects, and its GeoJSON
carries an `objectType` field — but **never in a message to the user**. A user reading one screen
should not meet three words for the same thing, and the object-versus-shape distinction is real in
the code yet invisible and unhelpful in an interface. So every count the app shows says *shapes*,
including counts of things that were dropped during reading.

Some shapes in a file never make it into a collection: unclassified ones, ones whose classification
carries no usable class name, and ones made of several separate outlines.

**polygon** — the **geometry type**, alongside `MultiPolygon` and `LineString`. Used only in that
sense, and only in a message when it explains why something cannot be cut — "several separate
outlines (MultiPolygon geometry)". A count of geometry types tells the user about shapely rather
than about their tissue, so the app reports shapes instead.

**contour** — not used. It appeared in an image caption and the README; both now say *shape*.

---

## Grouping and collection

**class** — a QuPath classification name, which in practice is a biological category:
`Tumor`, `Immune cells`. The app never invents one. An object classified with several classes
becomes a single combined class joined with `--` and sorted, e.g. `Immune cells--Tumor`.

**group** — the unit that maps to exactly one **well**, held in the `group_key` column. This is
the seam the two workflows share:

| workflow | a group is |
| --- | --- |
| annotations | one class |
| annotations, with a class exploded | one shape |
| cell segmentation | one class and replicate, e.g. `Tumor_r2` |
| cellular neighbourhoods | one class and replicate, e.g. `Tumor_r2` |

**region** — one contiguous area of tissue belonging to a single class, built by giving each
cell the space closest to it and merging touching cells of the same class. A region is a
**shape**, so it is cut like any other; the word exists because it is not a cell and not
something anyone drew in QuPath. A class usually holds several separate regions, and each is
counted, assigned and cut on its own.

A region **reaches past the cell outlines QuPath drew** — it covers the space between cells as
well, because that is the tissue belonging to the class. Two regions of different classes never
overlap. How far a region may reach is capped, so a lone cell at the edge of the tissue cannot
claim the blank slide around it.

**circle** — one of the many small discs packed inside a region to collect a set amount of it.
A circle is a **shape**, so it is cut like any other. Sizes are given as areas in µm², because
that is what an experiment is specified in, and the **gap** is the least tissue left between two
cuts — enforced between circles of different classes as well, since the laser does not care which
class a neighbouring cut belongs to.

**replicate** — one repeat of a class, collected into its own well. Replicates of a class are
drawn from across the whole tissue and interleaved with each other, so they are statistical
repeats rather than samples of different regions.

**sample** — what one well receives: one group as the experiment sees it, e.g. `Tumor_r2`.
With several slides a sample is filled from all of them — shapes of the same class are pooled by
class name, which assumes the slides are the same biological sample. *Group* stays the word in
code; *sample* is the word in the interface and in the samples-and-wells scheme.

**collector** — what the LMD collects into: a plate, a tube holder or a strip holder. Leica's own
word. One kind per experiment. An experiment with more samples than one collector holds uses
several, named `Plate1`, `Plate2`, …, `TubeHolder1`, …, or `Strip1`, …; each `.xml` targets one
collector, because the LMD's file names a position but not a collector. In code it is still called
`plate` (`plate.py`, `plate_type`, `n_plates`) for historical reasons (`decisions.md` 081).

**well** — a position on a plate or a strip. On a plate it is written row-then-column with no
padding: `C3`, `B12`. A 384 plate is rows A–P by columns 1–24; a 96 plate is A–H by 1–12. On the
strip it is the letter alone, `A`–`H`.

**tube** — a position on the tube holder: one of four Eppendorf tubes, `A`–`D`.

**plate** — a 96- or 384-well plate: one kind of collector.

**margin** — how many wells to leave unused around the edge of the plate. It exists because the
LMD7 collects unreliably into the outermost wells of a 384 plate.

**collection** — everything that will be cut in one run: the shapes, their groups, their wells,
and the parameters that produced them. Written out as an `.xml` the LMD software executes.

**plan** (`CollectionPlan`) — the object holding a decided collection. Both workflows produce
one, and everything downstream — QC, smoothing, cut order, export — reads only from it.

---

## Geometry and scale

**slide** — one QuPath image and its export: its own shapes, its own calibration points and its
own pixel size. Each slide gets its own `.xml`. The interface says *slide*, never *file*, *image*
or *section*, even where the upload is technically a file.

**calibration point** — one of three named point annotations that let the LMD map image
coordinates onto its stage. Three are required, they must not be collinear, and they should sit
close to the tissue being cut: a wide triangle distorts small shapes.

**pixel size** (µm/px) — how many micrometres one image pixel covers. QuPath coordinates are in
pixels while its area measurements are in µm², so the app can often derive this from the file
itself. It is only needed to express amounts as areas.

**smoothing tolerance** — how far a shape's outline may move when redundant points are removed,
in pixels. Higher values mean fewer points and a faster cut, at the cost of following the
annotation less exactly.

**cutting order** — the order shapes are written to the XML, which is the order the LMD cuts
them. Stage movement between shapes is a leading cause of cutting misalignment, so the default
groups each well together and shortens the path within it.

**neighbour** — two shapes closer than a set distance, judged on the original QuPath geometry.
Not the same as *intersecting*: QuPath's cell segmentation leaves a sub-pixel gap between
adjacent cells, so a strict intersection test finds almost none of the real neighbours.

---

## Where the terms come from

| this app | QuPath | py-lmd |
| --- | --- | --- |
| shape | object (annotation / cell / detection) | shape |
| class | classification | — |
| collector | — | — (Leica: collector) |
| well | — | well / cap |
| collection | — | Collection |
| calibration point | point annotation | calibration point |
| slide | image (in a project) | — |
| sample | — | — |
| plate | — | — |
