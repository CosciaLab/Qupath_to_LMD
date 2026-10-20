"""Several slides in one experiment: reading them, splitting an amount between them, collecting.

A slide is one QuPath image and its export — its own shapes, calibration points and scale. Shapes
of the same class on different slides are pooled into the same samples, which assumes the slides
are the same biological sample (`decisions.md` 075).

Every slide's pixel coordinates start at (0, 0), so slides are never stacked into one frame: the
spread grid, the adjacency graph and every other spatial step would read shapes on different
slides as neighbours. The selection engine runs once per slide, unchanged, and only the
bookkeeping — how much each slide is asked for and what it delivered — spans slides.
"""

import dataclasses
import hashlib
import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import geopandas
import numpy
import pandas
from loguru import logger

from qupath_to_lmd import budget, export, geojson, packing, selection, stats
from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.model import (
    CLASS_NAME,
    GROUP_KEY,
    REPLICATE,
    CollectionPlan,
    SampleSet,
    groups_from_replicates,
    plan_from_selection,
)
from qupath_to_lmd.plate import per_plate

SLIDE = "slide"

# A zip is expanded in memory, so its uncompressed size is capped well below the hosted app's
# memory ceiling (`facts.md`, Scale).
MAX_ZIP_UNCOMPRESSED_BYTES = 1_000_000_000


class SlideError(ValueError):
    """The uploads cannot be turned into slides."""


class SlideStrategy(str, Enum):
    """How a sample's amount is split between slides."""

    PRIORITY = "priority"
    PROPORTIONAL = "proportional"
    EQUAL = "equal"


@dataclass
class Slide:
    """One slide: its shapes, its calibration-point pool and what reading it found."""

    name: str
    gdf: geopandas.GeoDataFrame
    calibration_points: dict[str, list[float]]
    report: geojson.GeojsonReport
    source_file: str | None = None
    content_digest: str | None = None


def _unique(name: str, taken: set[str]) -> str:
    candidate, number = name, 2
    while candidate in taken:
        candidate, number = f"{name}_{number}", number + 1
    return candidate


def _geojson_sources(source) -> list[tuple[str, Any]]:
    """`(file name, readable)` for one upload: itself, or every GeoJSON inside a zip."""
    name = Path(getattr(source, "name", str(source))).name
    if not name.lower().endswith(".zip"):
        return [(name, source)]

    with zipfile.ZipFile(source) as archive:
        members = [
            info for info in archive.infolist()
            if not info.is_dir()
            and info.filename.lower().endswith(".geojson")
            # macOS zips carry a resource-fork twin of every file, which is not GeoJSON.
            and not Path(info.filename).name.startswith("._")
            and "__MACOSX" not in info.filename
        ]
        if sum(info.file_size for info in members) > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise SlideError(f"{name} unpacks to more than {MAX_ZIP_UNCOMPRESSED_BYTES // 10**6} MB.")
        found = [
            (Path(info.filename).name, io.BytesIO(archive.read(info)))
            for info in sorted(members, key=lambda info: info.filename)
        ]
    if not found:
        raise SlideError(f"{name} holds no .geojson files.")
    logger.info(f"{name}: {len(found)} GeoJSON files")
    return found


def _content_digest(readable) -> str:
    """A hash of the file's bytes, which is what tells a cache two exports of one image apart.

    They share a name, a cell count and the class names: a classifier run again changes only
    which cell is in which class.
    """
    data = readable.getvalue() if hasattr(readable, "getvalue") else Path(readable).read_bytes()
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def read_slides(sources) -> list[Slide]:
    """Read every upload into a slide, expanding zips. Names come from file names, made unique.

    Raises:
        SlideError: a zip is empty or too large.
        geojson.GeojsonError: a file cannot be read, naming which.
    """
    slides: list[Slide] = []
    taken: set[str] = set()
    for source in sources:
        for file_name, readable in _geojson_sources(source):
            try:
                gdf, calibration_points, report = geojson.read_and_qc(readable)
            except geojson.GeojsonError as error:
                raise geojson.GeojsonError(f"{file_name}: {error}") from error
            name = _unique(Path(file_name).stem, taken)
            taken.add(name)
            slides.append(
                Slide(
                    name, gdf, calibration_points, report,
                    source_file=file_name, content_digest=_content_digest(readable),
                )
            )
    logger.info(f"Read {len(slides)} slides: {[slide.name for slide in slides]}")
    return slides


def availability(
    pools: dict[str, geopandas.GeoDataFrame],
    mode: BudgetMode,
    pixel_sizes: Mapping[str, float | None],
) -> pandas.DataFrame:
    """What each class holds on each slide, in the budget's unit: class × slide."""
    columns = {}
    for name, pool in pools.items():
        table = stats.class_statistics(pool, pixel_sizes.get(name))
        columns[name] = table[mode.stats_column] if mode.stats_column in table.columns else pandas.Series(dtype=float)
    return pandas.DataFrame(columns).fillna(0.0).astype(float)


def _capped_shares(amount: float, caps: numpy.ndarray, strategy: SlideStrategy) -> numpy.ndarray:
    """Per-replicate shares that respect what each slide can supply, in slide order."""
    shares = numpy.zeros_like(caps)
    if strategy is SlideStrategy.PRIORITY:
        remaining = amount
        for position, cap in enumerate(caps):
            shares[position] = min(remaining, cap)
            remaining -= shares[position]
    elif strategy is SlideStrategy.PROPORTIONAL:
        if caps.sum() > 0:
            shares = numpy.minimum(caps, amount * caps / caps.sum())
    else:
        # Water-filling: an equal level for every slide, a slide that cannot reach it gives all
        # it has and the others rise to cover it.
        remaining, open_slides = amount, list(numpy.argsort(caps))
        while open_slides and remaining > 1e-12:
            level = remaining / len(open_slides)
            smallest = open_slides[0]
            if caps[smallest] - shares[smallest] <= level:
                remaining -= caps[smallest] - shares[smallest]
                shares[smallest] = caps[smallest]
                open_slides.pop(0)
            else:
                for position in open_slides:
                    shares[position] += level
                remaining = 0.0
    return shares


def _whole_numbers(shares: numpy.ndarray, total: int) -> numpy.ndarray:
    """Round shares to whole shapes that still add up to `total`, largest remainders first."""
    floors = numpy.floor(shares + 1e-9)
    missing = int(round(total - floors.sum()))
    order = numpy.argsort(-(shares - floors), kind="stable")
    for position in order[:max(0, missing)]:
        floors[position] += 1
    return floors


def split_budgets(
    budgets: list[ClassBudget],
    available: pandas.DataFrame,
    strategy: SlideStrategy,
    order: list[str],
    mode: BudgetMode,
) -> dict[str, list[ClassBudget]]:
    """Split every class's per-replicate amount between slides.

    The split is per replicate, so every replicate draws on the slides the same way. A slide is
    never asked for more than it holds while other slides still have tissue — except when the
    slides together cannot supply the request: then the remainder is spread in proportion to
    what each holds, so every slide is asked for at least everything it has and the shortfall
    is reported by the engine, exactly as it is for a single slide today. With one slide every
    strategy returns the budgets unchanged.

    Args:
        budgets: one per class, as the user set them.
        available: class × slide, in the budget's unit (`availability`).
        strategy: priority order, proportional, or equal share.
        order: slide names; priority fills them in this order.
        mode: shape counts are split into whole shapes.

    Returns:
        Each slide's budgets, one per class, same replicate counts.
    """
    shares = {name: [] for name in order}
    for item in budgets:
        held = numpy.array(
            [float(available.at[item.class_name, name]) if item.class_name in available.index
             and name in available.columns else 0.0 for name in order]
        )
        caps = held / item.replicates if item.replicates else held
        amount = item.per_replicate
        split = _capped_shares(amount, caps, strategy)

        residual = amount - split.sum()
        if residual > 1e-9:
            weights = held / held.sum() if held.sum() > 0 else numpy.eye(len(order))[0]
            split = split + residual * weights

        if mode is BudgetMode.CELLS:
            split = _whole_numbers(split, int(round(amount)))

        for name, share in zip(order, split, strict=True):
            shares[name].append(ClassBudget(item.class_name, item.replicates, float(share)))
        logger.info(
            f"{item.class_name}: {amount} per replicate split {strategy.value} as "
            + ", ".join(f"{name}={share:.1f}" for name, share in zip(order, split, strict=True))
        )
    return shares


@dataclass
class PooledSelection:
    """A selection made slide by slide, and what every sample received from every slide."""

    per_slide: dict[str, selection.SelectionResult]
    shares: dict[str, list[ClassBudget]]
    requested: dict[str, float] = field(default_factory=dict)

    @property
    def achieved(self) -> pandas.DataFrame:
        """One row per class, replicate and slide."""
        frames = [
            result.achieved.assign(**{SLIDE: name})
            for name, result in self.per_slide.items()
            if not result.achieved.empty
        ]
        return pandas.concat(frames, ignore_index=True) if frames else pandas.DataFrame()

    def by_sample(self) -> pandas.DataFrame:
        """One row per sample: what it asked for, what each slide gave, and the total."""
        rows = self.achieved
        if rows.empty:
            return rows
        table = rows.pivot_table(
            index=[CLASS_NAME, "replicate"], columns=SLIDE, values="achieved", aggfunc="sum", fill_value=0
        )
        table = table[[name for name in self.per_slide if name in table.columns]]
        table.columns = [str(column) for column in table.columns]
        table["achieved"] = table.sum(axis=1)
        classes = table.index.get_level_values(CLASS_NAME)
        table["requested"] = [self.requested.get(name, numpy.nan) for name in classes]
        return table.reset_index()


def selected_samples(
    frames: dict[str, geopandas.GeoDataFrame],
    pooled: PooledSelection,
    budgets: list[ClassBudget],
    mode: BudgetMode,
    pixel_sizes: Mapping[str, float | None],
    params: dict | None = None,
) -> SampleSet:
    """The sample set a pooled selection makes, over each slide's whole frame.

    The whole frame rather than the pool, so shapes the size filter removed stay reportable.
    """
    return samples_from_replicates(
        frames,
        {name: result.replicate_of for name, result in pooled.per_slide.items()},
        budgets,
        workflow="cells",
        pixel_sizes=pixel_sizes,
        unit=mode.unit,
        params=params,
    )


def select_across_slides(
    pools: dict[str, geopandas.GeoDataFrame],
    budgets: list[ClassBudget],
    mode: BudgetMode,
    params: selection.SelectionParams,
    pixel_sizes: Mapping[str, float | None],
    strategy: SlideStrategy = SlideStrategy.PROPORTIONAL,
    order: list[str] | None = None,
) -> PooledSelection:
    """Split the budgets between slides and run the unchanged selection on each slide.

    Args:
        pools: per slide, the shapes still collectable after any size filter.
        budgets: per class, as the user set them for the whole experiment.
        mode: whether budgets count shapes or µm².
        params: selection mode, adjacency preference and seed — the same for every slide.
        pixel_sizes: per slide; each slide's areas are measured at its own scale.
        strategy: how to split each amount between slides.
        order: slide order for priority; defaults to the order of `pools`.
    """
    order = list(order or pools)
    available = availability(pools, mode, pixel_sizes)
    shares = split_budgets(budgets, available, strategy, order, mode)
    per_slide = {
        name: selection.select(pools[name], shares[name], mode, params, pixel_sizes.get(name))
        for name in order
    }
    return PooledSelection(
        per_slide=per_slide,
        shares=shares,
        requested={item.class_name: item.per_replicate for item in budgets},
    )


def plans_for_slides(
    slides: list[Slide],
    pooled: PooledSelection,
    samples_and_wells: dict[str, str],
    calibration: dict[str, tuple[list[str], numpy.ndarray]],
    pixel_sizes: Mapping[str, float | None],
    *,
    session_id: str | None = None,
    params: dict | None = None,
) -> dict[str, CollectionPlan]:
    """One plan per slide, all sharing one samples-and-wells scheme.

    `Tumor_r2` on every slide maps to the same well, which is what pools them; each plan keeps
    its own slide's calibration, because each becomes its own `.xml`.
    """
    plans = {}
    for slide in slides:
        if slide.name not in pooled.per_slide:
            continue
        names, array = calibration[slide.name]
        plan, _ = plan_from_selection(
            gdf=slide.gdf,
            replicate_of=pooled.per_slide[slide.name].replicate_of,
            wells=[],
            calibration_names=names,
            calibration_array=array,
            samples_and_wells=samples_and_wells,
            source_file=slide.source_file,
            session_id=session_id,
            pixel_size_um=pixel_sizes.get(slide.name),
            params={**(params or {}), SLIDE: slide.name},
        )
        plans[slide.name] = plan
    return plans


def cuts_for_experiment(
    sample_set: SampleSet,
    slides: list[Slide],
    assignment: dict[str, tuple[str, str]],
    calibration: dict[str, tuple[list[str], numpy.ndarray]],
    *,
    plate: str = "384",
    simplify_tolerance: float = export.DEFAULT_SIMPLIFY_TOLERANCE,
    path_order: export.PathOrder = export.DEFAULT_PATH_ORDER,
    session_id: str | None = None,
    params: dict | None = None,
) -> list[export.Cut]:
    """Build every `.xml` of an experiment: one per slide and plate that has something to cut.

    Works from a `SampleSet`, so it does not matter which method chose the shapes. A slide that
    sends nothing to a plate gets no file for it, so the instructions never ask the user to mount
    a slide only to cut nothing.
    """
    source_files = {slide.name: slide.source_file for slide in slides}
    cuts = []
    for plate_name, scheme in per_plate(assignment).items():
        for slide_name in sample_set.slide_names:
            names, array = calibration[slide_name]
            slide_plan = sample_set.plan(
                slide_name, scheme, names, array,
                source_file=source_files.get(slide_name), session_id=session_id,
                params={**(params or {}), SLIDE: slide_name, "plate": plate_name},
            )
            if slide_plan.selected.empty:
                continue
            result = export.build_collection(
                slide_plan, samples_and_wells=scheme, simplify_tolerance=simplify_tolerance,
                plate=plate, path_order=path_order,
            )
            cuts.append(export.Cut(slide_name, plate_name, slide_plan, result))
    logger.info(f"{len(cuts)} cuts: {[(cut.slide, cut.plate) for cut in cuts]}")
    return cuts


@dataclass
class PooledPacking:
    """Circles packed slide by slide, and how each class's amount was split between slides."""

    per_slide: dict[str, packing.PackingResult]
    shares: dict[str, list[packing.ClassPacking]]

    @property
    def n_circles(self) -> int:
        """How many circles will be cut, over every slide."""
        return sum(result.n_circles for result in self.per_slide.values())


def pack_across_slides(
    patches: dict[str, geopandas.GeoDataFrame],
    requests: list[packing.ClassPacking],
    params: packing.PackingParams,
    pixel_sizes: Mapping[str, float | None],
    strategy: SlideStrategy = SlideStrategy.PROPORTIONAL,
    order: list[str] | None = None,
) -> PooledPacking:
    """Split each class's µm² between slides by what their regions can hold, then pack each slide.

    What a slide can hold is `packing.capacity`'s estimate, which already allows for the circle
    sizes and the gap — the raw region area would promise up to twice what fits. Each slide is
    packed on its own, so circles on one slide never collide with circles on another.

    Raises:
        packing.PackingError: a slide has no image scale; every amount here is an area.
    """
    order = list(order or patches)
    missing = [name for name in order if not pixel_sizes.get(name)]
    if missing:
        raise packing.PackingError(f"Packing circles needs an image scale for every slide; missing: {missing}.")

    available = pandas.DataFrame(
        {
            name: packing.capacity(patches[name], requests, pixel_sizes[name])["packable_estimate_um2"]
            for name in order
        }
    ).fillna(0.0)
    budgets = [item.as_budget() for item in requests]
    split = split_budgets(budgets, available, strategy, order, BudgetMode.AREA)

    shares = {
        name: [
            dataclasses.replace(item, area_per_replicate_um2=share.per_replicate)
            for item, share in zip(requests, split[name], strict=True)
        ]
        for name in order
    }
    per_slide = {name: packing.pack(patches[name], shares[name], params, pixel_sizes[name]) for name in order}
    return PooledPacking(per_slide=per_slide, shares=shares)


def samples_from_replicates(
    frames: dict[str, geopandas.GeoDataFrame],
    replicates: dict[str, pandas.Series],
    budgets: list[ClassBudget],
    *,
    workflow: str,
    pixel_sizes: Mapping[str, float | None],
    unit: str | None = None,
    params: dict | None = None,
) -> SampleSet:
    """A sample set from shapes that know their replicate: `Tumor_r2` on every slide is one sample.

    Serves selected shapes, packed circles and whole regions alike. Every replicate the budgets
    ask for is a sample, even one that ended up empty, so it keeps its well.

    Args:
        frames: per slide, every candidate shape.
        replicates: per slide, the replicate of each shape, NA where it is not collected.
        budgets: what was asked for. `unit` set means the amount per replicate is a request
            worth reporting; leave it None where a method takes everything it is given.
        workflow: recorded in provenance.
        pixel_sizes: per slide.
        unit: `shapes` or `µm²`, or None.
        params: everything else that determined the samples.
    """
    shapes = {}
    for name, frame in frames.items():
        replicate = replicates[name].reindex(frame.index)
        shapes[name] = frame.assign(**{REPLICATE: replicate, GROUP_KEY: groups_from_replicates(frame, replicate)})
    requested = (
        {f"{item.class_name}_r{n}": item.per_replicate for item in budgets for n in range(1, item.replicates + 1)}
        if unit
        else {}
    )
    return SampleSet(
        workflow=workflow,
        shapes=shapes,
        samples=budget.group_keys(budgets),
        pixel_sizes=dict(pixel_sizes),
        requested=requested,
        unit=unit or "shapes",
        params=params or {},
    )


def whole_shape_samples(
    frames: dict[str, geopandas.GeoDataFrame],
    classes: list[str],
    pixel_sizes: Mapping[str, float | None],
    params: dict | None = None,
) -> SampleSet:
    """Every shape of the chosen classes, one class per sample — the annotations route.

    Pooled by class name: `Tumor` on every slide is one sample. Samples are sorted, which is the
    order the plate has always filled them in.
    """
    shapes = {
        name: frame.assign(**{REPLICATE: None, GROUP_KEY: frame[CLASS_NAME].where(frame[CLASS_NAME].isin(classes))})
        for name, frame in frames.items()
    }
    present = set().union(*(set(frame[CLASS_NAME]) for frame in frames.values())) if frames else set()
    return SampleSet(
        workflow="legacy",
        shapes=shapes,
        samples=sorted(name for name in classes if name in present),
        pixel_sizes=dict(pixel_sizes),
        params=params or {},
    )
