"""Collect regions and circles: merge cells into regions of a class, then cut circles from them.

A single cell is too little tissue for mini-bulk, and outlining a neighbourhood by hand is slow
and unrepeatable. This route derives the tissue belonging to each class from the cells, merges
it into regions, and collects from those — as circles packed inside them, or as whole regions.

Each slide is projected and packed on its own: its cells never claim tissue on another slide
and its circles never collide with another slide's. Each class's µm² is split between slides by
what their regions can hold (`decisions.md` 075, 077).
"""

from enum import Enum

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import budget, export, geojson, packing, plot, regions, slides, ui_samples, ui_shared, ui_slides
from qupath_to_lmd.model import CLASS_NAME, REPLICATE, SampleSet
from qupath_to_lmd.ui_slides import SlidesContext

LABEL = "Regions and circles — merge cells into regions of a class, then cut circles from them"
HELP = (
    "For segmented cells collected as mini-bulk: the tissue around each class's cells is merged "
    "into regions, and circles packed inside them add up to the amount you set per replicate."
)


class CollectMode(str, Enum):
    """What is cut out of each region."""

    CIRCLES = "circles"
    WHOLE = "whole"


COLLECT_LABELS = {
    CollectMode.CIRCLES: "Circles packed inside the regions (recommended)",
    CollectMode.WHOLE: "The whole regions",
}


@st.cache_data(show_spinner="Projecting cells into regions...")
def _cached_projection(_gdf, cache_key: tuple, params: regions.RegionParams, include: tuple):
    """The projection, cached on everything that determines it (`decisions.md` 050)."""
    patches, report = regions.project(_gdf, params, include=list(include))
    return geojson.synthesize_qupath_columns(patches, "region", source=_gdf), report


@st.cache_data(show_spinner="Packing circles...")
def _cached_packing(_patches, _requests, _params, cache_key: tuple, scales: tuple, _strategy, order: tuple):
    """The circles on every slide, cached on every parameter that determines them."""
    return slides.pack_across_slides(_patches, _requests, _params, dict(scales), _strategy, list(order))


@st.cache_data(show_spinner=False)
def _cached_spacing(_gdf, cache_key: tuple) -> float:
    """How far apart neighbouring cells are, for the default reach."""
    return regions.median_cell_spacing(_gdf)


def regions_step(context: SlidesContext, selected: list[str], scales: dict):
    """Project every slide's cells into regions and show what they cover.

    Returns `(None, None)` when a slide cannot be projected, so the page stops this route
    without halting — the extras below it are still usable.
    """
    st.markdown("### Regions")
    st.markdown(
        "Each cell is given the tissue nearest to it, and touching cells of the same class are "
        "merged into one **region**. Regions are what you collect from, so a whole "
        "neighbourhood can go into a well rather than one cell. A region covers the space "
        "*between* its cells too, so it reaches past the outlines QuPath drew."
    )

    in_um = all(scales.values())
    unit = "µm" if in_um else "px"
    first = context.slides[0]
    first_scale = scales[first.name] if in_um else 1.0
    spacing_px = _cached_spacing(first.gdf, context.fingerprint(first.name))
    default_reach = round(regions.DEFAULT_RADIUS_FACTOR * spacing_px * first_scale)

    reach = st.number_input(
        f"Maximum reach from each cell ({unit})",
        min_value=1,
        max_value=100_000,
        value=max(1, int(default_reach)),
        step=1,
        format="%d",
        key="max_reach",
        help=(
            "A region is the tissue nearest to its cell and never further away than this. "
            "It is the only thing bounding the projection: without it the outermost cells "
            "would claim the empty slide around them, and empty space inside the tissue "
            "would be handed to whichever cell happened to be nearest.\n\n"
            f"Neighbouring cells{' on ' + first.name if context.several else ''} sit about "
            f"{spacing_px * first_scale:,.0f} {unit} apart, so the default is three times that."
        ),
    )
    st.caption(
        f"Gaps wider than {2 * reach:,.0f} {unit} are left uncollected. A smaller reach "
        "splits the tissue into more, smaller regions; a larger one merges them into fewer, "
        "bigger ones."
    )

    patches, reports = {}, {}
    for slide in context.slides:
        scale = scales[slide.name] if in_um else 1.0
        params = regions.RegionParams(max_radius_px=float(reach) / scale)
        try:
            patches[slide.name], reports[slide.name] = _cached_projection(
                slide.gdf, context.fingerprint(slide.name), params, tuple(selected)
            )
        except regions.RegionError as error:
            st.error(f"{slide.name}: {error}" if context.several else str(error))
            logger.error(f"Region projection failed on {slide.name}: {error}")
            return None, None
        if slide.name == first.name and st.session_state.region_params != vars(params):
            st.session_state.region_params = vars(params)
            logger.info(f"Region parameters: {vars(params)}")

    def show(slide) -> None:
        report = reports[slide.name]
        numbers, picture = st.columns([1, 2], gap="medium")
        with numbers:
            ui_shared.show_amounts(report.summary(scales.get(slide.name)))
            st.write(
                f"**{report.n_patches:,} regions** from {report.n_cells_kept:,} cells across "
                f"{len(report.per_class)} classes."
            )
            if report.n_duplicate_centroids:
                st.warning(
                    f"{report.n_duplicate_centroids:,} cells sit at exactly the same position as "
                    "another cell. Only one of each pair can own the tissue around it, so the "
                    "others are left out. This usually means the same cells were exported twice."
                )
        with picture:
            shown, palette = patches[slide.name], ui_samples.class_palette(context)
            with st.spinner("Drawing regions..."):
                ui_shared.show_picture(
                    lambda: plot.plot_regions_and_circles(shown, colors=palette),
                    ui_shared.picture_key("regions", shown[CLASS_NAME], shown.geometry, palette),
                )
            st.caption("Fill colour is the class.")

    if context.several:
        for tab, slide in zip(st.tabs(context.names), context.slides, strict=True):
            with tab:
                show(slide)
    else:
        show(first)
    return patches, reports


# The per-class table. Every column is something the user sets for one class (`decisions.md` 070).
REPLICATES_COLUMN = "Replicates"
AMOUNT_COLUMN = "µm² per replicate"
MIN_CIRCLE_COLUMN = "Smallest circle (µm²)"
MAX_CIRCLE_COLUMN = "Largest circle (µm²)"
GAP_COLUMN = "Gap (µm)"

COLUMN_HELP = {
    REPLICATES_COLUMN: "Each replicate of each class is collected into its own well.",
    AMOUNT_COLUMN: (
        "How much tissue goes into each well of this class. Circles are added until this is "
        "reached. Zero collects nothing for the class."
    ),
    MIN_CIRCLE_COLUMN: (
        "Microdissection cannot reliably collect below about 100 µm², and small circles lose "
        "more of their area to smoothing. Smaller circles fit into narrower regions, so "
        "lowering this uses more of a fragmented class."
    ),
    MAX_CIRCLE_COLUMN: (
        "Bigger circles reach the target with fewer cuts, so the collection runs faster, but "
        "they only fit in the wider parts of a region."
    ),
    GAP_COLUMN: (
        "The least tissue left between two cuts. Cuts closer than this leave a strip too thin "
        "to hold, which detaches and falls into whichever well is cut first — so it is enforced "
        "between circles of different classes too, at the wider of the two gaps. It costs more "
        "tissue than it looks: a 5 µm gap roughly halves how much of a region can be filled, "
        "and 20 µm quarters it."
    ),
}


def _seed() -> packing.PackingParams:
    seed = st.number_input(
        "Seed",
        min_value=0,
        max_value=10_000,
        value=0,
        step=1,
        key="packing_seed",
        help=(
            "Same seed and settings, same circles. Change it to draw a different random "
            "arrangement from the same tissue. Recorded in provenance.json so a collection "
            "can be repeated in a later session."
        ),
    )
    return packing.PackingParams(seed=int(seed))


def _request_table(classes: list[str], with_circles: bool) -> list[packing.ClassPacking]:
    """One row per class: replicates, amount, circle sizes and the gap."""
    columns = {REPLICATES_COLUMN: packing.DEFAULT_REPLICATES}
    if with_circles:
        columns[AMOUNT_COLUMN] = int(packing.DEFAULT_AREA_PER_REPLICATE_UM2)
        columns[MIN_CIRCLE_COLUMN] = int(packing.DEFAULT_MIN_CIRCLE_AREA_UM2)
        columns[MAX_CIRCLE_COLUMN] = int(packing.DEFAULT_MAX_CIRCLE_AREA_UM2)
        columns[GAP_COLUMN] = int(packing.DEFAULT_SPACING_UM)

    steps = {REPLICATES_COLUMN: 1, AMOUNT_COLUMN: 1_000, MIN_CIRCLE_COLUMN: 10, MAX_CIRCLE_COLUMN: 50, GAP_COLUMN: 1}
    configuration = {
        name: st.column_config.NumberColumn(
            name,
            min_value=1 if name == REPLICATES_COLUMN else 0,
            step=steps[name],
            format="%d" if name in (REPLICATES_COLUMN, GAP_COLUMN) else "localized",
            help=COLUMN_HELP[name],
        )
        for name in columns
    }
    edited = st.data_editor(
        pandas.DataFrame(columns, index=pandas.Index(classes)),
        width="stretch",
        key=f"request_editor_{len(classes)}_{int(with_circles)}",
        column_config=configuration,
    )
    requests = [
        packing.ClassPacking(
            class_name=str(name),
            replicates=int(row[REPLICATES_COLUMN] or 1),
            area_per_replicate_um2=float(row.get(AMOUNT_COLUMN, 0) or 0),
            min_circle_area_um2=float(row.get(MIN_CIRCLE_COLUMN, packing.DEFAULT_MIN_CIRCLE_AREA_UM2) or 1),
            max_circle_area_um2=float(row.get(MAX_CIRCLE_COLUMN, packing.DEFAULT_MAX_CIRCLE_AREA_UM2) or 1),
            spacing_um=float(row.get(GAP_COLUMN, 0) or 0),
        )
        for name, row in edited.iterrows()
    ]
    recorded = [vars(item) for item in requests]
    if st.session_state.region_budgets != recorded:
        st.session_state.region_budgets = recorded
        logger.info(f"Per-class requests: {recorded}")
    return requests


def _draw(context: SlidesContext, patches: dict, circles: dict | None) -> None:
    """The tissue map with what will be cut on top: class by fill, replicate by outline."""
    palette = ui_samples.class_palette(context)

    def draw(name: str) -> None:
        shown = circles.get(name) if circles else None
        replicate_of = shown[REPLICATE] if shown is not None and len(shown) else None
        tissue = patches[name]
        key = [tissue[CLASS_NAME], tissue.geometry, palette]
        if shown is not None:
            key += [shown[CLASS_NAME], shown.geometry, replicate_of]
        with st.spinner("Drawing..."):
            ui_shared.show_picture(
                lambda: plot.plot_regions_and_circles(tissue, shown, replicate_of=replicate_of, colors=palette),
                ui_shared.picture_key("regions and circles", *key),
            )

    if context.several:
        for tab, name in zip(st.tabs(context.names), context.names, strict=True):
            with tab:
                draw(name)
    else:
        draw(context.names[0])
    st.caption(
        "Fill colour is the class, for the regions and the circles alike. The outline of a "
        "circle is its replicate. Circles of one class that sit close together always go into "
        "the same well."
    )


def collect_step(context: SlidesContext, patches: dict, reports: dict, scales: dict) -> SampleSet | None:
    """What to cut from the regions, how much, and into how many replicates (`decisions.md` 070)."""
    st.markdown("### What to collect, and how much")

    can_pack = all(scales.values())
    options = list(CollectMode) if can_pack else [CollectMode.WHOLE]
    mode = st.radio(
        "What to collect from each region",
        options=options,
        format_func=lambda option: COLLECT_LABELS[option],
        key="collect_mode",
        horizontal=True,
        help=(
            "Circles are the usual choice: a region is one large irregular outline that takes a "
            "long time to cut and gives no way to ask for a set amount, whereas circles cut "
            "quickly and add up to the amount you set. Collect whole regions when you want all "
            "of the tissue rather than a measured amount of it."
        ),
    )
    if not can_pack:
        st.caption(
            "Packing circles needs the image scale"
            + (" of every slide" if context.several else "")
            + ", because circle sizes and the amount per replicate are areas in µm². Enter one "
            "below to pack circles."
        )

    scale_column, seed_column, _spacer = st.columns([2, 1, 3])
    with scale_column:
        scales = ui_slides.scale_control(context)
    with seed_column:
        params = _seed()

    classes = sorted(set().union(*(set(report.per_class.index) for report in reports.values())))
    packing_wanted = mode is CollectMode.CIRCLES
    requests = _request_table(classes, with_circles=packing_wanted)
    strategy, order = ui_samples.strategy_control(context, key="regions")

    recorded = {
        "classes": st.session_state.selected_classes,
        "requests": [vars(item) for item in requests],
        "collect_mode": mode.value,
        **(st.session_state.region_params or {}),
    }
    if context.several:
        recorded.update({"slide_strategy": strategy.value, "slide_order": order})

    if not packing_wanted:
        return _collect_whole(context, patches, requests, scales, recorded)
    if not all(scales.values()):
        st.warning(
            "The image scale was cleared, so circles cannot be sized. Enter one above, or "
            "collect whole regions instead."
        )
        return None
    return _collect_circles(context, patches, requests, params, scales, strategy, order, recorded)


def _collect_circles(context, patches, requests, params, scales, strategy, order, recorded) -> SampleSet | None:
    try:
        for item in requests:
            item.validate()
    except packing.PackingError as error:
        st.error(str(error))
        return None

    cache_key = (
        context.fingerprint(),
        tuple(sorted((st.session_state.region_params or {}).items())),
        tuple(tuple(sorted(vars(item).items())) for item in requests),
        tuple(sorted(vars(params).items())),
        strategy.value,
    )
    try:
        pooled = _cached_packing(patches, requests, params, cache_key, tuple(sorted(scales.items())), strategy, tuple(order))
    except packing.PackingError as error:
        st.error(str(error))
        logger.error(f"Packing failed: {error}")
        return None

    circles = {
        name: geojson.synthesize_qupath_columns(result.circles, "circle", source=context.slide(name).gdf)
        for name, result in pooled.per_slide.items()
    }
    _draw(context, patches, circles)

    if pooled.n_circles == 0:
        st.warning(
            "No circles could be placed. The regions may all be narrower than the smallest "
            "circle — lower the smallest circle area in the table, or reduce how far a region "
            "may reach above so the regions are less fragmented."
        )
        return None

    recorded.update(vars(params))
    st.session_state.packing_params = vars(params)
    sample_set = slides.samples_from_replicates(
        circles,
        {name: frame[REPLICATE] for name, frame in circles.items()},
        [item.as_budget() for item in requests],
        workflow="regions",
        pixel_sizes=scales,
        unit="µm²",
        params=recorded,
    )
    if context.several:
        _report_pooled_packing(pooled, sample_set, requests)
    else:
        report_packing(next(iter(pooled.per_slide.values())), requests)
    return sample_set


def _capacity_for_display(estimate: pandas.DataFrame) -> pandas.DataFrame:
    return estimate.rename(
        columns={
            "region_area_um2": "Region area (µm²)",
            "packable_estimate_um2": "Can hold about (µm²)",
            "requested_um2": "You asked for (µm²)",
            "shortfall_um2": "Short by (µm²)",
            "fillable_replicates": "Replicates fillable",
        }
    )


def report_packing(result, requests) -> None:
    """One slide: per replicate, what was achieved, and everything that changes the amount."""
    ui_shared.show_amounts(
        result.achieved.rename(
            columns={
                CLASS_NAME: "Class",
                "replicate": "Replicate",
                "circles": "Circles",
                packing.CIRCLE_AREA: "Collected (µm²)",
                "requested": "Asked for (µm²)",
            }
        ).drop(columns=["achieved"])
    )
    short = result.shortfalls
    if not short.empty:
        lines = "\n".join(
            f"- **{row[CLASS_NAME]} replicate {int(row['replicate'])}**: got "
            f"{row['achieved']:,.0f} µm² of {row['requested']:,.0f} µm²"
            for _, row in short.iterrows()
        )
        _warn_short(len(short), lines)
    else:
        st.success("Every replicate reached the amount you asked for.")

    with st.expander("How much each class could hold, before packing"):
        ui_shared.show_amounts(_capacity_for_display(result.capacity))
        st.caption(
            "Randomly placed circles cover about 55% of an area at best, and the gap between "
            "them cuts that down further, so what a region can hold is well below its area."
        )
    _report_side_effects(result.n_near_another_class, result.n_regions_too_small, result.circles, requests)


def _report_pooled_packing(pooled: slides.PooledPacking, sample_set: SampleSet, requests) -> None:
    """Several slides: each replicate's µm² from every slide, against what was asked for."""
    sheet = sample_set.sheet()
    ui_shared.show_amounts(sheet.set_index("sample"))
    asked = sheet["asked for (µm²)"]
    short = sheet[sheet["µm²"] < asked - 1e-9]
    if not short.empty:
        lines = "\n".join(
            f"- **{row['sample']}**: got {row['µm²']:,.0f} µm² of {row['asked for (µm²)']:,.0f} µm²"
            for _, row in short.iterrows()
        )
        _warn_short(len(short), lines)
    else:
        st.success("Every replicate reached the amount you asked for, over all slides.")

    with st.expander("How much each class could hold on each slide, before packing"):
        for name, result in pooled.per_slide.items():
            st.markdown(f"**{name}**")
            ui_shared.show_amounts(_capacity_for_display(result.capacity))
        st.caption(
            "Each class's amount was split between slides by these estimates. Randomly placed "
            "circles cover about 55% of an area at best, and the gap cuts that down further."
        )
    results = list(pooled.per_slide.values())
    all_circles = pandas.concat([result.circles for result in results]) if results else None
    _report_side_effects(
        sum(result.n_near_another_class for result in results),
        sum(result.n_regions_too_small for result in results),
        all_circles,
        requests,
    )


def _warn_short(count: int, lines: str) -> None:
    st.warning(
        f"{count} replicate(s) could not be filled:\n\n{lines}\n\n"
        "They will still be collected, with less tissue than you asked for. To fit more, for "
        "that class alone: narrow its gap, lower its smallest circle area, or include more "
        "of it by lowering how far a region may reach above."
    )


def _report_side_effects(n_near_another_class, n_regions_too_small, circles, requests) -> None:
    if n_near_another_class:
        st.caption(
            f"{n_near_another_class:,} circles sit within the gap of a circle from a different "
            "class. Those always go to different wells, because a class owns its own, so only a "
            "wider gap moves them apart. Circles of the *same* class that close together are "
            "always collected into the same well."
        )
    if n_regions_too_small:
        st.warning(
            f"{n_regions_too_small:,} region(s) are too narrow to hold even one circle of the size "
            "asked for, so they contribute nothing. Lower the smallest circle area for that class "
            "to use them, or accept that this tissue is too fragmented to collect at that size."
        )
    zero = [item.class_name for item in requests if item.area_per_replicate_um2 <= 0]
    if zero:
        st.warning(f"Nothing will be collected for: {', '.join(zero)} — the amount is zero.")

    if circles is None or circles.empty:
        return
    _lost, fraction = packing.smoothing_loss(circles, export.DEFAULT_SIMPLIFY_TOLERANCE)
    if fraction > 0.05:
        st.warning(
            f"**Smoothing will take {fraction:.0%} of the circle area back off**, because the "
            f"default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance cuts the corners off a "
            "small circle. The amounts above are before smoothing, so each well will hold that "
            "much less than it says. Raise the smallest circle area, or lower the smoothing "
            "tolerance at the cut stage."
        )
    elif fraction:
        st.caption(
            f"Smoothing at the default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance takes "
            f"{fraction:.1%} off the amounts above."
        )


def _collect_whole(context, patches, requests, scales, recorded) -> SampleSet:
    """Deal each slide's whole regions across replicates and report what each one holds."""
    replicates = {item.class_name: item.replicates for item in requests}
    replicate_of = {name: regions.deal_patches(frame, replicates) for name, frame in patches.items()}
    _draw(context, patches, None)

    sample_set = slides.samples_from_replicates(
        patches,
        replicate_of,
        [budget.ClassBudget(item.class_name, item.replicates, 0.0) for item in requests],
        workflow="regions",
        pixel_sizes=scales,
        params=recorded,
    )
    ui_shared.show_amounts(sample_set.sheet().set_index("sample"))
    report_starved_replicates(patches, replicates, replicate_of)
    return sample_set


def report_starved_replicates(patches: dict, replicates: dict, replicate_of: dict) -> None:
    """Warn where a class has fewer regions than the replicates asked of it, over every slide."""
    starved = {}
    for name, wanted in replicates.items():
        filled = set()
        n_regions = 0
        for slide, frame in patches.items():
            in_class = frame[CLASS_NAME] == name
            filled |= set(replicate_of[slide][in_class].dropna())
            n_regions += int(in_class.sum())
        if len(filled) < wanted:
            starved[name] = (len(filled), wanted, n_regions)

    if starved:
        lines = "\n".join(
            f"- **{name}**: {wanted} replicates asked for, only {filled} can be filled — it has "
            f"{n_regions:,} region(s)"
            for name, (filled, wanted, n_regions) in starved.items()
        )
        st.warning(
            f"{len(starved)} class(es) have fewer regions than replicates:\n\n{lines}\n\n"
            "You can continue — the empty replicates keep their wells so the plate still "
            "matches what you asked for — or reduce the number of replicates. To get more "
            "regions from a class, lower how far a region may reach above, which splits "
            "the class into more separate areas."
        )
    else:
        st.success("Every replicate of every class has at least one region.")


def render(context: SlidesContext) -> SampleSet | None:
    """Classes, regions, and what to cut from them."""
    selected = ui_samples.class_step(context)
    if not selected:
        return None
    scales = context.pixel_sizes()
    patches, reports = regions_step(context, selected, scales)
    if patches is None:
        return None
    return collect_step(context, patches, reports, scales)
