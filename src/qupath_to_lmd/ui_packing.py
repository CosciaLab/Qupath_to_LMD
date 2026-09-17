"""The cellular-neighbourhood workflow: project cells into regions, then collect them.

A single cell is too little tissue to collect as mini-bulk, and outlining a neighbourhood by
hand is slow and unrepeatable. This workflow derives the tissue that belongs to each class from
the cells themselves, merges it into contiguous regions, and collects from those — either as
circles packed inside them, which is the usual thing to want, or as the whole regions.

Step order matters here. How much tissue to collect and how many replicates to take are decided
**before** the plate, because those two answers are what size the plate, and a user tuning the
circle settings is not yet thinking about wells. Everything expensive upstream of the loop is
cached, so a rerun costs the packing and the drawing rather than the tessellation.
"""

from dataclasses import dataclass
from enum import Enum

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import budget, export, geojson, packing, plate, plot, regions, ui_shared
from qupath_to_lmd.model import CLASS_NAME, REPLICATE, plan_from_selection

REPLICATES_COLUMN = "Replicates"

# Areas here run from tens to millions of µm² and no decimal in them is meaningful, so tables
# show whole numbers with a thousands separator. Integer columns cannot render a decimal, and
# `localized` is what adds the grouping.
WHOLE_NUMBER = st.column_config.NumberColumn(format="localized")


class CollectMode(str, Enum):
    """What is cut out of each region."""

    CIRCLES = "circles"
    WHOLE = "whole"


COLLECT_LABELS = {
    CollectMode.CIRCLES: "Circles packed inside the regions (recommended)",
    CollectMode.WHOLE: "The whole regions",
}


@dataclass
class Collected:
    """What the collection step decided, and what the plate and export steps need from it."""

    shapes: object
    replicate_of: object
    replicates: dict
    mode: CollectMode
    pixel_size_um: float | None
    params: packing.PackingParams | None = None


@st.cache_data(show_spinner="Projecting cells into regions...")
def _cached_projection(_gdf, cache_key: tuple, params: regions.RegionParams, include: tuple):
    """The projection, cached on everything that determines it.

    Streamlit reruns the whole script on every widget change and the tessellation costs about
    0.5 s at 8 400 cells, so without this every unrelated control would pay for it again
    (`decisions.md` 050). This cache is also what makes the settings loop in step 6 affordable
    without a fragment, which the step order rules out.
    """
    patches, report = regions.project(_gdf, params, include=list(include))
    return geojson.synthesize_qupath_columns(patches, "region", source=_gdf), report


@st.cache_data(show_spinner="Packing circles...")
def _cached_packing(_patches, _replicates, _params, cache_key: tuple, pixel_size_um: float):
    """The circles, cached on every parameter that determines them."""
    return packing.pack(_patches, _replicates, _params, pixel_size_um)


def _as_area(area_px2, pixel_size_um: float | None):
    """Pixel areas converted to µm² where a scale is known."""
    return area_px2 * pixel_size_um**2 if pixel_size_um else area_px2


def _as_distance(px: float, pixel_size_um: float | None) -> str:
    """A distance in µm where a scale is known, in pixels where it is not."""
    return f"{px * pixel_size_um:,.0f} µm" if pixel_size_um else f"{px:,.0f} px"


def _whole_numbers(table: pandas.DataFrame) -> pandas.DataFrame:
    """Round every float column to a whole number, so no column can render a decimal."""
    rounded = table.copy()
    for column in rounded.columns:
        if pandas.api.types.is_numeric_dtype(rounded[column]):
            rounded[column] = rounded[column].round(0).astype("Int64")
    return rounded


def _show_table(table: pandas.DataFrame) -> None:
    """Show a table of amounts: whole numbers, thousands separated."""
    if table.empty:
        return
    rounded = _whole_numbers(table)
    st.dataframe(
        rounded,
        width="stretch",
        column_config=dict.fromkeys(rounded.columns, WHOLE_NUMBER),
    )


def regions_step(
    selected: list[str], pixel_size_um: float | None, step: str = "5"
) -> tuple[object, object]:
    """Project the cells into regions and show what they cover.

    Returns `(None, None)` when no regions can be built, so the caller stops rendering this
    workflow without halting the page — the Extras below it are still usable.
    """
    gdf = st.session_state.gdf

    st.markdown(f"## Step {step}: Project cells into regions")
    st.markdown(
        "Each cell is given the tissue closest to it, and touching cells of the same class are "
        "merged into one **region**. Regions are what you collect from, so a whole "
        "neighbourhood can go into a well rather than one cell.\n\n"
        "**A region is larger than the cells it came from.** It covers the space between them "
        "as well, because that is the tissue that belongs to this class. Two regions of "
        "different classes never overlap, but a region does reach past the cell outlines QuPath "
        "drew."
    )

    factor_column, radius_column = st.columns([2, 3])
    with factor_column:
        radius_factor = st.number_input(
            "How far a region may reach from its cell",
            min_value=0.5,
            max_value=20.0,
            value=regions.DEFAULT_RADIUS_FACTOR,
            step=0.5,
            key=f"radius_factor_{step}",
            help=(
                "As a multiple of the typical distance between neighbouring cells, so the same "
                "number behaves sensibly on dense and sparse tissue alike. In dense tissue this "
                "limit is never reached — neighbouring cells meet first. It matters where cells "
                "are sparse: without it, a lone cell at the edge of the tissue would claim all "
                "the blank slide around it and the laser would cut glass.\n\n"
                "A smaller number gives more, smaller regions; a larger one merges them into "
                "fewer, bigger ones."
            ),
        )

    params = regions.RegionParams(radius_factor=float(radius_factor))
    try:
        patches, report = _cached_projection(
            gdf, ui_shared.shape_fingerprint(gdf), params, tuple(selected)
        )
    except regions.RegionError as error:
        st.error(str(error))
        logger.error(f"Region projection failed: {error}")
        return None, None

    with radius_column:
        st.markdown(
            "Neighbouring cells sit about "
            f"**{_as_distance(report.median_nn_distance_px, pixel_size_um)}** apart, so a region "
            f"reaches at most **{_as_distance(report.max_radius_px, pixel_size_um)}** from its cell."
        )

    if st.session_state.region_params != vars(params):
        st.session_state.region_params = vars(params)
        logger.info(f"Region parameters: {vars(params)}")

    _show_table(report.summary(pixel_size_um))
    st.write(
        f"**{report.n_patches:,} regions** from {report.n_cells_kept:,} cells across "
        f"{len(report.per_class)} classes."
    )

    if report.n_duplicate_centroids:
        st.warning(
            f"{report.n_duplicate_centroids:,} cells sit at exactly the same position as another "
            "cell. Only one of each pair can own the tissue around it, so the others are left "
            "out. This usually means the same cells were exported twice."
        )

    with st.spinner("Drawing regions..."):
        figure = plot.plot_shapes(
            patches,
            calibration_array=st.session_state.calib_array,
            title=f"{report.n_patches} regions, coloured by class",
        )
    st.pyplot(figure, width="content")
    st.caption(
        "Dashed triangle and crosses are your calibration points. Regions far outside the "
        "triangle are the ones at risk of distortion."
    )

    return patches, report


def _circle_controls(step: str) -> tuple[float, float, float, int]:
    """Circle sizes, the gap between cuts, and the seed.

    Whole µm² throughout: a tenth of a square micrometre is far below anything the laser can
    place or the balance can weigh, so a decimal here is noise in a number the user has to read.
    """
    min_column, max_column, gap_column, seed_column = st.columns([2, 2, 2, 1])

    with min_column:
        min_area = st.number_input(
            "Smallest circle (µm²)",
            min_value=1,
            max_value=1_000_000,
            value=int(packing.DEFAULT_MIN_CIRCLE_AREA_UM2),
            step=10,
            format="%d",
            key=f"min_circle_{step}",
            help=(
                "Microdissection cannot reliably collect below about 100 µm², and small circles "
                "also lose more of their area to smoothing. Smaller circles fit into narrower "
                "regions, so lowering this uses more of the tissue."
            ),
        )
    with max_column:
        max_area = st.number_input(
            "Largest circle (µm²)",
            min_value=1,
            max_value=1_000_000,
            value=int(packing.DEFAULT_MAX_CIRCLE_AREA_UM2),
            step=50,
            format="%d",
            key=f"max_circle_{step}",
            help=(
                "Bigger circles reach the target with fewer cuts, so the collection runs "
                "faster, but they only fit in the wider parts of a region."
            ),
        )
    with gap_column:
        gap = st.number_input(
            "Gap between circles (µm)",
            min_value=0,
            max_value=200,
            value=int(packing.DEFAULT_SPACING_UM),
            step=1,
            format="%d",
            key=f"gap_{step}",
            help=(
                "The least tissue left between two cuts. Cuts closer than this leave a strip "
                "too thin to hold, which detaches and falls into whichever well is cut first — "
                "so this is enforced between circles of different classes too. It costs more "
                "tissue than it looks: at these circle sizes a 5 µm gap roughly halves how much "
                "of a region can be filled, and 20 µm quarters it."
            ),
        )
    with seed_column:
        seed = st.number_input(
            "Seed",
            min_value=0,
            max_value=10_000,
            value=0,
            step=1,
            key=f"packing_seed_{step}",
            help=(
                "Same seed and settings, same circles. Change it to draw a different random "
                "arrangement from the same tissue. Recorded in provenance.json so a collection "
                "can be repeated in a later session."
            ),
        )

    return float(min_area), float(max_area), float(gap), int(seed)


def _amount_controls(step: str) -> tuple[float, float | None]:
    """How much tissue per replicate, with the image scale beside it.

    The scale sits here because this is the only thing it feeds — an amount in µm²
    (`decisions.md` 057).
    """
    amount_column, scale_column = st.columns([3, 2])
    with amount_column:
        area_per_replicate = st.number_input(
            "Tissue per replicate (µm²)",
            min_value=1,
            max_value=100_000_000,
            value=int(packing.DEFAULT_AREA_PER_REPLICATE_UM2),
            step=1_000,
            format="%d",
            key=f"area_per_replicate_{step}",
            help=(
                "How much tissue goes into each well. Circles are added until this is reached, "
                "so this is the amount the experiment is specified in."
            ),
        )
    with scale_column:
        pixel_size_um = ui_shared.pixel_size_control()
    return float(area_per_replicate), pixel_size_um


def _replicates_control(report, step: str) -> dict:
    """How many replicates of each class, which is what sizes the plate."""
    available = report.per_class["patches"]
    edited = st.data_editor(
        pandas.DataFrame({REPLICATES_COLUMN: 1}, index=available.index),
        width="stretch",
        key=f"replicates_editor_{step}_{len(available)}",
        column_config={
            REPLICATES_COLUMN: st.column_config.NumberColumn(
                REPLICATES_COLUMN,
                min_value=1,
                step=1,
                format="%d",
                help="Each replicate of each class is collected into its own well.",
            )
        },
    )
    replicates = {str(name): int(row[REPLICATES_COLUMN] or 1) for name, row in edited.iterrows()}
    if st.session_state.replicates != replicates:
        st.session_state.replicates = replicates
        logger.info(f"Replicates per class: {replicates}")
    return replicates


def collect_step(patches, report, pixel_size_um, step: str = "6") -> Collected | None:
    """Decide what to cut, how much of it, and into how many replicates.

    This is the step the user loops on, which is why it comes before the plate: the amount and
    the replicate count are what the plate has to accommodate, so deciding them first means the
    plate is shown once, already correct.
    """
    st.markdown(f"## Step {step}: What to collect, and how much")

    options = list(CollectMode) if pixel_size_um else [CollectMode.WHOLE]
    mode = st.radio(
        "What to collect from each region",
        options=options,
        format_func=lambda option: COLLECT_LABELS[option],
        key=f"collect_mode_{step}",
        horizontal=True,
        help=(
            "Circles are the usual choice: a region is one large irregular outline that takes a "
            "long time to cut and gives no way to ask for a set amount, whereas circles cut "
            "quickly and add up to the amount you set. Collect whole regions when you want all "
            "of the tissue rather than a measured amount of it."
        ),
    )
    if not pixel_size_um:
        st.caption(
            "Packing circles needs the image scale, because circle sizes and the amount per "
            "replicate are areas in µm². Enter one below to pack circles."
        )

    if mode is CollectMode.WHOLE:
        return _collect_whole(patches, report, pixel_size_um, step)
    return _collect_circles(patches, report, pixel_size_um, step)


def _collect_circles(patches, report, pixel_size_um: float, step: str) -> Collected | None:
    """Set the circle settings and the amount, pack, and show what came out."""
    min_area, max_area, gap, seed = _circle_controls(step)
    area_per_replicate, pixel_size_um = _amount_controls(step)
    replicates = _replicates_control(report, step)

    if not pixel_size_um:
        st.warning(
            "The image scale was cleared, so circles cannot be sized. Enter one above, or "
            "collect whole regions instead."
        )
        return None

    params = packing.PackingParams(
        area_per_replicate_um2=area_per_replicate,
        min_circle_area_um2=min_area,
        max_circle_area_um2=max_area,
        spacing_um=gap,
        seed=seed,
    )
    try:
        params.validate()
    except packing.PackingError as error:
        st.error(str(error))
        return None

    st.markdown("**Before packing** — what each class holds against what you have asked for:")
    _show_table(_capacity_for_display(packing.capacity(patches, replicates, params, pixel_size_um)))
    st.caption(
        "Randomly placed circles cover about 55% of an area at best, and the gap between them "
        "cuts that down further, so what a region can hold is well below its area. That figure "
        "is an estimate — what was actually achieved is below."
    )

    cache_key = (
        ui_shared.shape_fingerprint(st.session_state.gdf),
        tuple(sorted((st.session_state.region_params or {}).items())),
        tuple(sorted(replicates.items())),
        tuple(sorted(vars(params).items())),
        pixel_size_um,
    )
    try:
        result = _cached_packing(patches, replicates, params, cache_key, pixel_size_um)
    except packing.PackingError as error:
        st.error(str(error))
        logger.error(f"Packing failed: {error}")
        return None

    if result.n_circles == 0:
        st.warning(
            "No circles could be placed. The regions may all be narrower than the smallest "
            "circle — lower the smallest circle area, or reduce how far a region may reach in "
            "step 5 so the regions are less fragmented."
        )
        return None

    _report_packing(result, params, patches, pixel_size_um)

    circles = geojson.synthesize_qupath_columns(
        result.circles, "circle", source=st.session_state.gdf
    )
    _preview(circles, circles[REPLICATE])
    return Collected(
        shapes=circles,
        replicate_of=circles[REPLICATE],
        replicates=replicates,
        mode=CollectMode.CIRCLES,
        pixel_size_um=pixel_size_um,
        params=params,
    )


def _capacity_for_display(estimate: pandas.DataFrame) -> pandas.DataFrame:
    """Rename the capacity table for showing to a user."""
    return estimate.rename(
        columns={
            "region_area_um2": "Region area (µm²)",
            "packable_estimate_um2": "Can hold about (µm²)",
            "requested_um2": "You asked for (µm²)",
            "shortfall_um2": "Short by (µm²)",
            "fillable_replicates": "Replicates fillable",
        }
    )


def _report_packing(result, params: packing.PackingParams, patches, pixel_size_um) -> None:
    """What was collected, and everything that quietly changes the amount."""
    collected = float(result.circles[packing.CIRCLE_AREA].sum())
    available = float(_as_area(patches.geometry.area.sum(), pixel_size_um))
    mean_diameter = (
        2 * (result.circles[packing.CIRCLE_AREA].mean() / 3.141592653589793) ** 0.5
        if result.n_circles
        else 0.0
    )

    tissue, packed, count, diameter = st.columns(4)
    tissue.metric("Tissue in the regions", f"{available:,.0f} µm²")
    packed.metric(
        "Being collected",
        f"{collected:,.0f} µm²",
        f"{collected / available:.1%} of the tissue" if available else None,
        # Neutral: this is a share of the whole, not a change, so it must not read as one.
        delta_color="off",
    )
    count.metric("Circles to cut", f"{result.n_circles:,}")
    diameter.metric("Mean circle across", f"{mean_diameter:,.0f} µm")

    _show_table(
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
        st.warning(
            f"{len(short)} replicate(s) could not be filled:\n\n{lines}\n\n"
            "They will still be collected, with less tissue than you asked for. To fit more: "
            "narrow the gap between circles, lower the smallest circle area, or include more of "
            "the class by lowering how far a region may reach in step 5."
        )
    else:
        st.success("Every replicate reached the amount you asked for.")

    if result.n_regions_too_small:
        st.warning(
            f"{result.n_regions_too_small:,} region(s) are too narrow to hold even one circle of "
            f"{params.min_circle_area_um2:,.0f} µm², so they contribute nothing. Lower the "
            "smallest circle area to use them, or accept that this tissue is too fragmented to "
            "collect at that size."
        )

    lost, fraction = packing.smoothing_loss(result.circles, export.DEFAULT_SIMPLIFY_TOLERANCE)
    lost_um2 = lost * (pixel_size_um**2)
    if fraction > 0.05:
        st.warning(
            f"**Smoothing will take {fraction:.0%} of the circle area back off** "
            f"({lost_um2:,.0f} µm² in total), because the default "
            f"{export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance cuts the corners off a small "
            "circle. The amounts above are before smoothing, so each well will hold that much "
            "less than it says. Raise the smallest circle area, or lower the smoothing "
            "tolerance at the export step."
        )
    elif fraction:
        st.caption(
            f"Smoothing at the default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance takes "
            f"{fraction:.1%} ({lost_um2:,.0f} µm²) off the amounts above."
        )


def _collect_whole(patches, report, pixel_size_um, step: str) -> Collected:
    """Deal whole regions across replicates and report what each one holds."""
    replicates = _replicates_control(report, step)
    replicate_of = regions.deal_patches(patches, replicates)

    frame = pandas.DataFrame(
        {
            "Class": patches[CLASS_NAME],
            "Replicate": replicate_of,
            "Regions": 1,
            "Cells": patches[regions.N_CELLS],
            "Area (µm²)" if pixel_size_um else "Area (px²)": _as_area(
                patches.geometry.area, pixel_size_um
            ),
        }
    ).dropna(subset=["Replicate"])

    if not frame.empty:
        _show_table(frame.groupby(["Class", "Replicate"]).sum(numeric_only=True))

    _report_starved_replicates(patches, replicates, replicate_of)
    _preview(patches, replicate_of)
    return Collected(
        shapes=patches,
        replicate_of=replicate_of,
        replicates=replicates,
        mode=CollectMode.WHOLE,
        pixel_size_um=pixel_size_um,
    )


def _report_starved_replicates(patches, replicates: dict, replicate_of) -> None:
    """Warn where a class has fewer regions than the replicates asked of it."""
    starved = {}
    for name, wanted in replicates.items():
        filled = int(replicate_of[patches[CLASS_NAME] == name].dropna().nunique())
        if filled < wanted:
            starved[name] = (filled, wanted)

    if starved:
        lines = "\n".join(
            f"- **{name}**: {wanted} replicates asked for, only {filled} can be filled — it has "
            f"{int((patches[CLASS_NAME] == name).sum()):,} region(s)"
            for name, (filled, wanted) in starved.items()
        )
        st.warning(
            f"{len(starved)} class(es) have fewer regions than replicates:\n\n{lines}\n\n"
            "You can continue — the empty replicates keep their wells so the plate still "
            "matches what you asked for — or reduce the number of replicates. To get more "
            "regions from a class, lower how far a region may reach in step 5, which splits "
            "the class into more separate areas."
        )
    else:
        st.success("Every replicate of every class has at least one region.")


def _preview(shapes, replicate_of) -> None:
    """Draw what will be cut, coloured by replicate."""
    labels = replicate_of.map(
        lambda value: f"replicate {int(value)}" if pandas.notna(value) else None
    )
    with st.spinner("Drawing the collection..."):
        figure = plot.plot_shapes(
            shapes,
            labels=labels,
            calibration_array=st.session_state.calib_array,
            title="What will be cut, coloured by replicate",
        )
    st.pyplot(figure, width="content")
    st.caption(
        "Classes are merged here so you can judge whether the replicates are spread and "
        "comparable across the tissue."
    )


def plate_step(replicates: dict, step: str = "7") -> dict:
    """Plate settings, the well assignment they produce, and whether it fits.

    Comes after the collection is decided, so the well count it has to accommodate is already
    settled and the plate is shown once rather than redrawn on every parameter change.
    """
    settings = ui_shared.plate_settings_step(step=step)

    groups = budget.group_keys(
        [budget.ClassBudget(name, count, 0.0) for name, count in replicates.items()]
    )
    usable = settings["wells"]
    st.write(
        f"This plan needs **{len(groups)} wells**, one per replicate per class. "
        f"This plate offers **{len(usable)}**."
    )
    if len(groups) > len(usable):
        st.warning(
            f"{len(groups) - len(usable)} more wells are needed than the plate offers, so that "
            "many groups will not be collected. Reduce the replicates in step 6, lower the "
            "margin or spacing, or use a 384 well plate."
        )

    samples_and_wells = plate.assign_wells(groups, usable, randomize=settings["randomize"])
    samples_and_wells = ui_shared.editable_plate(
        samples_and_wells, settings["plate_type"], key_suffix="regions"
    )
    st.session_state.saw = samples_and_wells
    ui_shared.plate_preview(
        samples_and_wells, settings["plate_type"], wells=usable, key_suffix="regions"
    )

    settings["samples_and_wells"] = samples_and_wells
    return settings


def export_step(collected: Collected, settings: dict, step: str = "8") -> None:
    """Build the plan from whatever was collected, and hand off to the shared export."""
    recorded = {
        "plate": settings["plate_type"],
        "margins": settings["margins"],
        "step_row": settings["step_row"],
        "step_col": settings["step_col"],
        "randomize_wells": settings["randomize"],
        "classes": st.session_state.selected_classes,
        "replicates": collected.replicates,
        "collect_mode": collected.mode.value,
    }
    recorded.update(st.session_state.region_params or {})
    if collected.params is not None:
        recorded.update(vars(collected.params))
        st.session_state.packing_params = vars(collected.params)

    plan, samples_and_wells = plan_from_selection(
        gdf=collected.shapes,
        replicate_of=collected.replicate_of,
        wells=settings["wells"],
        samples_and_wells=settings.get("samples_and_wells"),
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        pixel_size_um=collected.pixel_size_um,
        workflow="regions",
        params=recorded,
    )

    st.session_state.saw = samples_and_wells
    ui_shared.export_step(settings, lambda _settings: plan, step=step)


def render(uploaded_file) -> None:
    """The cellular-neighbourhood workflow."""
    if st.session_state.gdf is None:
        st.info("Upload a GeoJSON to continue.")
        return

    pixel_size, _source = ui_shared.resolve_pixel_size()

    selected = ui_shared.class_selection_step(pixel_size, step="4")
    if not selected:
        return
    st.divider()

    patches, report = regions_step(selected, pixel_size, step="5")
    if patches is None:
        return
    st.divider()

    collected = collect_step(patches, report, pixel_size, step="6")
    if collected is None:
        return
    st.divider()

    settings = plate_step(collected.replicates, step="7")
    st.divider()

    export_step(collected, settings, step="8")
