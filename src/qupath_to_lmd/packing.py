"""Filling regions with circles, so a neighbourhood can be collected as mini-bulk.

A whole merged region is one enormous irregular outline: the stage would trace it for a long
time, and there is no way to ask for *some* of it. Many small circles instead cut quickly, sit
wholly inside the right tissue, and add up to a chosen area per replicate.

The method is rejection sampling — throw a circle of random size at a random spot, keep it if
it fits inside the region and is far enough from every circle already placed, stop when the
target area is reached or when placements keep failing. Ported from Jose's prototype, with the
differences recorded in `decisions.md` 067. The ones that change results:

- **Spacing is enforced across the whole collection, not per class.** The laser needs material
  between two cuts whether or not they belong to the same biology, and regions of different
  classes touch by construction, so two circles either side of a class boundary can otherwise
  end up a fraction of a micrometre apart. The strip between them would detach and fall into
  whichever well came first.
- **Each class draws from its own random sub-stream**, keyed by its name rather than its
  position. That is what the seed buys: the candidate positions a class tries are the same
  whatever the other classes do. Classes are still coupled through *rejections*, because the
  gap above is global and classes are packed in sorted order — so changing one class's amount
  can change the classes sorted after it, though never those sorted before it. Everything is
  reproducible from the recorded parameters either way.
- **Collision detection is a grid of buckets**, not a spatial index rebuilt on every attempt.
- **Areas are measured from the polygon**, not from `pi r**2`, because a buffered circle is a
  polygon and is a little smaller than the circle it approximates.
"""

import hashlib
from dataclasses import dataclass, field

import geopandas
import numpy
import pandas
import shapely
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME, REPLICATE

# A circle this many segments per quarter turn is a 64-sided polygon. Vertices are what the
# stage traces, so this trades cutting time against how round the cut really is.
QUAD_SEGS = 16

DEFAULT_AREA_PER_REPLICATE_UM2 = 10_000.0
# The same floor the cell workflow uses for a collectable shape (`decisions.md` 060).
DEFAULT_MIN_CIRCLE_AREA_UM2 = 100.0
DEFAULT_MAX_CIRCLE_AREA_UM2 = 500.0
DEFAULT_SPACING_UM = 5.0

# Consecutive failed placements before a region is called full. Reset on every success, so this
# counts how hard it has become rather than how much has been tried.
DEFAULT_MAX_ATTEMPTS = 2000

# The share of an area that randomly-thrown, non-overlapping circles of mixed size actually
# cover, measured on a 2000x2000 px square with a zero gap. The prototype assumed this and
# stopped there; `packable_area` corrects it for the gap, which costs far more than it looks
# (`decisions.md` 067).
RANDOM_PACKING_FILL = 0.547

CIRCLE_AREA = "area_um2"

CIRCLE_COLUMNS = ("geometry", CLASS_NAME, REPLICATE, CIRCLE_AREA)


def empty_circles() -> geopandas.GeoDataFrame:
    """An empty circles frame that still has its columns.

    So "the circles frame always carries these columns" is true even when nothing could be
    packed. The prototype returned a bare `GeoDataFrame()` here, and every caller then had to
    remember to check before touching a column or get a `KeyError` instead of an empty table.
    """
    return geopandas.GeoDataFrame(
        {name: [] for name in CIRCLE_COLUMNS}, geometry="geometry", crs=None
    )


class PackingError(Exception):
    """The circles asked for cannot be placed at all."""


@dataclass(frozen=True)
class PackingParams:
    """Everything that determines the circles, recorded so they can be reproduced.

    Areas rather than radii throughout: the amount of tissue is what a proteomics experiment
    is specified in, and it is what the user is budgeting.
    """

    area_per_replicate_um2: float = DEFAULT_AREA_PER_REPLICATE_UM2
    min_circle_area_um2: float = DEFAULT_MIN_CIRCLE_AREA_UM2
    max_circle_area_um2: float = DEFAULT_MAX_CIRCLE_AREA_UM2
    spacing_um: float = DEFAULT_SPACING_UM
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    seed: int = 0

    def validate(self) -> None:
        """Raise on a combination that cannot produce circles.

        Raises:
            PackingError: the size range is empty or the amounts are not positive.
        """
        if self.min_circle_area_um2 <= 0:
            raise PackingError("The smallest circle must have an area greater than zero.")
        if self.max_circle_area_um2 < self.min_circle_area_um2:
            raise PackingError(
                f"The largest circle ({self.max_circle_area_um2:,.0f} µm²) is smaller than the "
                f"smallest ({self.min_circle_area_um2:,.0f} µm²). Swap them, or widen the range."
            )
        if self.area_per_replicate_um2 <= 0:
            raise PackingError("The area per replicate must be greater than zero.")

    @property
    def mean_circle_area_um2(self) -> float:
        """Areas are drawn uniformly, so the mean is the midpoint of the range."""
        return (self.min_circle_area_um2 + self.max_circle_area_um2) / 2


@dataclass
class PackingResult:
    """The circles, and how they compare with what was asked for."""

    circles: geopandas.GeoDataFrame = field(default_factory=empty_circles)
    achieved: pandas.DataFrame = field(default_factory=pandas.DataFrame)
    capacity: pandas.DataFrame = field(default_factory=pandas.DataFrame)
    n_discarded: int = 0
    n_regions_too_small: int = 0

    @property
    def n_circles(self) -> int:
        """How many circles will be cut."""
        return len(self.circles)

    @property
    def shortfalls(self) -> pandas.DataFrame:
        """Replicates that could not be filled to the requested area."""
        if self.achieved.empty:
            return self.achieved
        return self.achieved[self.achieved["achieved"] < self.achieved["requested"] - 1e-9]


def class_generator(seed: int, class_name: str) -> numpy.random.Generator:
    """A random stream of its own for one class, keyed by name rather than by position.

    Keyed by name so adding a class, removing one or reordering them leaves every other class
    drawing the same candidate positions. The prototype ran one stream through all the classes
    in turn, so nudging one class's spacing re-rolled every draw in every class after it — a
    settings-and-preview loop then shows changes the user did not ask for and cannot attribute.

    `hashlib` rather than `hash()`: Python salts `hash()` per process, so the same class would
    pack differently in a new session and a collection could not be reproduced.
    """
    digest = hashlib.blake2b(class_name.encode("utf-8"), digest_size=8).digest()
    return numpy.random.default_rng([seed, int.from_bytes(digest, "big")])


def packable_area(area: float, params: PackingParams) -> float:
    """Roughly how much of `area` circles of this size and spacing can actually cover.

    Randomly thrown circles of mixed size reach about 55% coverage with no gap between them.
    A gap costs much more than it appears to: every circle of radius `r` effectively claims a
    disc of radius `r + gap/2`, so the usable fraction falls by that ratio squared.

    Measured against this formula on a 2000x2000 px square, circles 100-500 µm² at 0.3467 µm/px:
    54.7% observed at a 0 µm gap, 42.9% at 2 µm, 32.9% at 5, 21.4% at 10, 12.2% at 20 — the
    formula is within 2 percentage points at every one, and slightly optimistic throughout.

    This is an estimate shown before packing runs, so the user can see an impossible request
    before waiting for it. What was actually achieved is always reported separately.
    """
    mean_radius = numpy.sqrt(params.mean_circle_area_um2 / numpy.pi)
    inflation = (1 + params.spacing_um / (2 * mean_radius)) ** 2
    return float(RANDOM_PACKING_FILL * area / inflation)


def capacity(
    patches: geopandas.GeoDataFrame,
    replicates: dict[str, int],
    params: PackingParams,
    pixel_size_um: float,
) -> pandas.DataFrame:
    """What each class holds, what it is asked for, and whether the two are compatible.

    Cheap — no circles are placed — so it can be shown while the user is still typing.
    """
    rows = {}
    areas = patches.geometry.area * pixel_size_um**2
    for class_name, count in replicates.items():
        region_area = float(areas[patches[CLASS_NAME] == class_name].sum())
        estimated = packable_area(region_area, params)
        required = count * params.area_per_replicate_um2
        rows[class_name] = {
            "region_area_um2": region_area,
            "packable_estimate_um2": estimated,
            "requested_um2": required,
            "shortfall_um2": max(0.0, required - estimated),
            "fillable_replicates": int(estimated // params.area_per_replicate_um2),
        }
    return pandas.DataFrame.from_dict(rows, orient="index")


def _pack_one(
    patch,
    target_area_px2: float,
    radius_range_px: tuple[float, float],
    spacing_px: float,
    buckets: dict,
    bucket_size: float,
    generator: numpy.random.Generator,
    max_attempts: int,
) -> list:
    """Throw circles into one region until the target area is met or placements keep failing.

    `buckets` is shared across the whole collection and mutated here, which is what makes the
    gap hold between circles of different classes as well as within one.

    Returns:
        The circles placed, as `(polygon, area_px2)`.
    """
    shapely.prepare(patch)
    minx, miny, maxx, maxy = patch.bounds
    min_radius, max_radius = radius_range_px

    placed: list = []
    area = 0.0
    attempts = 0

    while area < target_area_px2 and attempts < max_attempts:
        attempts += 1
        x = generator.uniform(minx, maxx)
        y = generator.uniform(miny, maxy)
        # Uniform in area, not in radius, so the size distribution is the one the user set.
        radius = numpy.sqrt(generator.uniform(min_radius**2, max_radius**2))

        if _collides(buckets, bucket_size, x, y, radius, spacing_px):
            continue
        # Cheap test first. A region fills only a quarter of its own bounding box on this
        # tissue, so most darts miss it entirely, and a prepared point-in-polygon test is far
        # cheaper than buffering a circle and comparing 64 vertices against the outline.
        centre = shapely.Point(x, y)
        if not patch.contains(centre):
            continue
        circle = centre.buffer(radius, quad_segs=QUAD_SEGS)
        # Strict containment, so a circle never crosses into another class's tissue or off the
        # edge of the region. `contains` respects interior rings, so an enclosed island of
        # another class cannot be packed into either.
        if not patch.contains(circle):
            continue

        buckets.setdefault(_bucket(x, y, bucket_size), []).append((x, y, radius))
        placed.append((circle, circle.area))
        area += circle.area
        # Reset, so `max_attempts` measures how hard placement has become rather than how many
        # darts have been thrown in total.
        attempts = 0

    return placed


def _bucket(x: float, y: float, size: float) -> tuple[int, int]:
    """Which grid cell a point falls in."""
    return int(x // size), int(y // size)


def _collides(
    buckets: dict, size: float, x: float, y: float, radius: float, spacing: float
) -> bool:
    """Whether a circle here would sit closer than `spacing` to one already placed.

    An exact centre-to-centre test against a grid of buckets. The prototype rebuilt a
    `shapely.STRtree` from every placed circle on every single attempt, which is O(n² log n)
    over a run and was the dominant cost; it also queried the tree without a predicate, so it
    compared bounding boxes and rejected placements that were actually legal.

    The bucket side is `2 * max_radius + spacing`, so no circle can conflict with one more than
    one bucket away and only the 3x3 neighbourhood needs checking.
    """
    bucket_x, bucket_y = _bucket(x, y, size)
    for i in range(bucket_x - 1, bucket_x + 2):
        for j in range(bucket_y - 1, bucket_y + 2):
            for other_x, other_y, other_radius in buckets.get((i, j), ()):
                limit = radius + other_radius + spacing
                if (x - other_x) ** 2 + (y - other_y) ** 2 < limit**2:
                    return True
    return False


def pack(
    patches: geopandas.GeoDataFrame,
    replicates: dict[str, int],
    params: PackingParams,
    pixel_size_um: float,
) -> PackingResult:
    """Fill the regions of every class with circles and deal them across its replicates.

    Args:
        patches: regions from `regions.project`, carrying `classification_name`.
        replicates: how many replicates each class wants.
        params: sizes, spacing, effort and the seed.
        pixel_size_um: the image scale. Required — every amount here is an area in µm².

    Returns:
        The circles with their class and replicate, what each replicate achieved, and the
        capacity estimate the user was shown beforehand.

    Raises:
        PackingError: the parameters cannot produce circles, or there is no image scale.
    """
    params.validate()
    if not pixel_size_um:
        raise PackingError("Packing circles needs the image scale (µm per pixel).")

    logger.info(
        f"Packing: {params.area_per_replicate_um2:,.0f} µm² per replicate, circles "
        f"{params.min_circle_area_um2:,.0f}-{params.max_circle_area_um2:,.0f} µm², gap "
        f"{params.spacing_um} µm, seed {params.seed}, {params.max_attempts} attempts"
    )

    um2_per_px2 = pixel_size_um**2
    radius_range_px = (
        numpy.sqrt(params.min_circle_area_um2 / numpy.pi) / pixel_size_um,
        numpy.sqrt(params.max_circle_area_um2 / numpy.pi) / pixel_size_um,
    )
    spacing_px = params.spacing_um / pixel_size_um
    bucket_size = 2 * radius_range_px[1] + spacing_px

    # One grid for the whole collection, so the gap holds between classes too.
    buckets: dict = {}
    records: list[dict] = []
    rows: list[dict] = []
    n_discarded = 0
    n_regions_skipped = 0

    # Sorted, so the run does not depend on the order the classes happen to appear in.
    for class_name in sorted(replicates):
        wanted = int(replicates[class_name])
        if wanted < 1:
            continue
        generator = class_generator(params.seed, class_name)
        # One circle of slack per replicate. A replicate is filled until it reaches the target,
        # so it overshoots by up to one circle; packing exactly the total left the *last*
        # replicate with only the remainder and systematically short. A replicate that is
        # reliably 5% smaller than its siblings is not a comparable measurement, and the slack
        # costs only the handful of circles reported as discarded.
        target_px2 = (
            wanted
            * (params.area_per_replicate_um2 + params.mean_circle_area_um2)
            / um2_per_px2
        )

        circles, skipped = _pack_class(
            patches[patches[CLASS_NAME] == class_name],
            target_px2,
            radius_range_px,
            spacing_px,
            buckets,
            bucket_size,
            generator,
            params.max_attempts,
        )
        n_regions_skipped += skipped

        dealt, leftover = _deal_circles(
            circles, wanted, params.area_per_replicate_um2 / um2_per_px2, generator
        )
        n_discarded += leftover

        for replicate in range(1, wanted + 1):
            members = dealt.get(replicate, [])
            achieved = float(sum(area for _geometry, area in members)) * um2_per_px2
            for geometry, area in members:
                records.append(
                    {
                        "geometry": geometry,
                        CLASS_NAME: class_name,
                        REPLICATE: replicate,
                        CIRCLE_AREA: area * um2_per_px2,
                    }
                )
            rows.append(
                {
                    CLASS_NAME: class_name,
                    "replicate": replicate,
                    "circles": len(members),
                    CIRCLE_AREA: achieved,
                    "requested": params.area_per_replicate_um2,
                    "achieved": achieved,
                }
            )

    circles_gdf = (
        geopandas.GeoDataFrame(records, geometry="geometry", crs=None)
        if records
        else empty_circles()
    )
    result = PackingResult(
        circles=circles_gdf,
        achieved=pandas.DataFrame(rows),
        capacity=capacity(patches, replicates, params, pixel_size_um),
        n_discarded=n_discarded,
        n_regions_too_small=n_regions_skipped,
    )
    logger.success(
        f"Packed {result.n_circles} circles across {len(rows)} replicates"
        + (f"; {n_discarded} beyond the last replicate were discarded" if n_discarded else "")
    )
    return result


def _fits_one_circle(class_patches: geopandas.GeoDataFrame, min_radius_px: float):
    """Which regions could hold at least one smallest circle.

    Eroding a region by the smallest radius and asking whether anything is left is the exact
    test, and it costs about 0.01 s for 141 regions. Worth doing: a region too thin to hold
    anything otherwise burns the whole `max_attempts` budget discovering that, and on real
    tissue 18 of 141 regions of one class are in that state — 36 000 darts thrown to learn
    nothing (`decisions.md` 067).
    """
    return ~class_patches.geometry.buffer(-min_radius_px).is_empty


def _pack_class(
    class_patches: geopandas.GeoDataFrame,
    target_px2: float,
    radius_range_px: tuple[float, float],
    spacing_px: float,
    buckets: dict,
    bucket_size: float,
    generator: numpy.random.Generator,
    max_attempts: int,
) -> tuple[list, int]:
    """Spread one class's target area over its regions, largest first, and stop when it is met.

    Per region rather than against the class's whole outline: a region's own bounding box is a
    far tighter place to aim darts than the bounding box of every region in the class, which on
    a scattered class is mostly empty slide.

    Each region is asked for its share of what is still **outstanding**, so a region that could
    not take its share passes the remainder to the others and a region that overshot reduces
    what the rest are asked for. Tracking the outstanding amount rather than accumulating a
    deficit is what keeps the total honest: every region overshoots by up to one circle, and
    with 141 regions a one-sided deficit over-packed the class by more than twice its target.

    Returns:
        The circles placed, and how many regions were too small to hold any.
    """
    if class_patches.empty:
        return [], 0

    usable = class_patches[_fits_one_circle(class_patches, radius_range_px[0])]
    n_skipped = len(class_patches) - len(usable)
    if usable.empty:
        return [], n_skipped

    areas = usable.geometry.area
    outstanding = target_px2
    remaining_area = float(areas.sum())
    placed: list = []

    for index in areas.sort_values(ascending=False).index:
        if outstanding <= 0 or remaining_area <= 0:
            break
        share = outstanding * float(areas[index]) / remaining_area
        remaining_area -= float(areas[index])
        if share <= 0:
            continue
        got = _pack_one(
            usable.geometry[index],
            share,
            radius_range_px,
            spacing_px,
            buckets,
            bucket_size,
            generator,
            max_attempts,
        )
        outstanding -= float(sum(area for _geometry, area in got))
        placed.extend(got)

    return placed, n_skipped


def _deal_circles(
    circles: list, replicates: int, target_px2: float, generator: numpy.random.Generator
) -> tuple[dict, int]:
    """Fill each replicate in turn from a shuffled pool, and say how many were left over.

    Shuffled first so a replicate is drawn from the whole class rather than from whichever
    region happened to be packed first — otherwise replicate 1 would be one corner of the
    tissue and the replicates would not be comparable.

    Returns:
        The circles per replicate, and how many were placed but not needed.
    """
    if not circles:
        return {}, 0

    order = generator.permutation(len(circles))
    dealt: dict[int, list] = {}
    replicate = 1
    filled = 0.0
    used = 0

    for position in order:
        if replicate > replicates:
            break
        circle = circles[position]
        dealt.setdefault(replicate, []).append(circle)
        filled += circle[1]
        used += 1
        if filled >= target_px2:
            replicate += 1
            filled = 0.0

    return dealt, len(circles) - used


def smoothing_loss(circles: geopandas.GeoDataFrame, tolerance_px: float) -> tuple[float, float]:
    """How much circle area the export's smoothing removes, and the share that is.

    This matters here in a way it does not elsewhere: area per replicate is this workflow's
    entire budget, and smoothing a small circle takes a real bite out of it. At the default
    1 px tolerance a 64-sided circle of radius 10 px or less is reduced to 9 vertices and loses
    10% of its area; by radius 20 px the loss is 2.6%, and by 100 px it is 0.6%.

    Returns:
        The area lost in the same units as the circles' own areas, and the fraction lost.
    """
    if circles.empty or tolerance_px <= 0:
        return 0.0, 0.0
    before = float(circles.geometry.area.sum())
    after = float(circles.geometry.simplify(tolerance_px).area.sum())
    lost = max(0.0, before - after)
    return lost, (lost / before if before else 0.0)
