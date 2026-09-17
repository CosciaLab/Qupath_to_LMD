"""The cellular-neighbourhood workflow: project cells into regions, then collect them.

A single cell is too little tissue to collect as mini-bulk, and outlining a neighbourhood by
hand is slow and unrepeatable. This workflow derives the tissue that belongs to each class from
the cells themselves, merges it into contiguous regions, and collects from those — either as
circles packed inside them, which is the usual thing to want, or as the whole regions.
"""

from enum import Enum

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import budget, export, geojson, packing, plate, plot, regions, ui_shared
from qupath_to_lmd.model import CLASS_NAME, REPLICATE, plan_from_selection

REPLICATES_COLUMN = "Replicates"


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
    """The projection, cached on everything that determines it.

    Streamlit reruns the whole script on every widget change and the tessellation costs about
    0.5 s at 8 400 cells, so without this every unrelated control would pay for it again
    (`decisions.md` 050).

    The QuPath fields are added here rather than at export, so the frame every later step
    handles is already one the download bundle can write back out.
    """
    patches, report = regions.project(_gdf, params, include=list(include))
    return geojson.synthesize_qupath_columns(patches, "region", source=_gdf), report


@st.cache_data(show_spinner="Packing circles...")
def _cached_packing(_patches, _replicates, _params, cache_key: tuple, pixel_size_um: float):
    """The circles, cached on every parameter that determines them.

    The whole point of this step is a settings-and-preview loop, so it reruns on every
    keystroke; packing 8 400 cells' worth of regions costs about 0.7 s.
    """
    return packing.pack(_patches, _replicates, _params, pixel_size_um)


def _area_column(pixel_size_um: float | None) -> str:
    """Areas are shown in µm² where a scale is known, and in px² where it is not."""
    return "Area (µm²)" if pixel_size_um else "Area (px²)"


def _as_area(area_px2, pixel_size_um: float | None):
    """Pixel areas converted to µm² where a scale is known."""
    return area_px2 * pixel_size_um**2 if pixel_size_um else area_px2


def _as_distance(px: float, pixel_size_um: float | None) -> str:
    """A distance in µm where a scale is known, in pixels where it is not."""
    return f"{px * pixel_size_um:.1f} µm" if pixel_size_um else f"{px:.0f} px"


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

    st.dataframe(report.summary(pixel_size_um), width="stretch")
    st.write(
        f"**{report.n_patches} regions** from {report.n_cells_kept:,} cells across "
        f"{len(report.per_class)} classes."
    )

    _report_projection(report, pixel_size_um)

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


def _report_projection(report: regions.RegionReport, pixel_size_um: float | None) -> None:
    """Say what the projection had to leave out or could not represent faithfully."""
    if report.n_duplicate_centroids:
        st.warning(
            f"{report.n_duplicate_centroids} cells sit at exactly the same position as another "
            "cell. Only one of each pair can own the tissue around it, so the others are left "
            "out. This usually means the same cells were exported twice."
        )

    if report.n_patches_with_holes:
        enclosed = _as_area(report.hole_area_px2, pixel_size_um)
        unit = "µm²" if pixel_size_um else "px²"
        st.warning(
            f"**{report.n_patches_with_holes} region(s) completely surround tissue of another "
            f"class**, totalling {enclosed:,.0f} {unit}. Circles are never packed into the "
            "enclosed tissue. But if you collect whole regions, the laser follows a region's "
            "outer outline only, so that tissue would be cut and collected along with the "
            "region around it and the well would hold a mixture."
        )


def replicates_step(
    report, pixel_size_um: float | None, step: str = "6"
) -> tuple[dict, float | None]:
    """How many replicates per class, and the image scale the amounts are measured in."""
    st.markdown(f"## Step {step}: Replicates")
    st.markdown(
        "Each replicate of each class is collected into its own well. This decides how many "
        "wells the plate needs; how much goes into each one is set in the last step."
    )

    editor_column, scale_column = st.columns([3, 2])

    with scale_column:
        pixel_size_um = ui_shared.pixel_size_control()

    available = report.per_class["patches"]
    with editor_column:
        edited = st.data_editor(
            pandas.DataFrame({REPLICATES_COLUMN: 1}, index=available.index),
            width="stretch",
            key=f"replicates_editor_{step}_{len(available)}",
            column_config={
                REPLICATES_COLUMN: st.column_config.NumberColumn(
                    REPLICATES_COLUMN, min_value=1, step=1, format="%d"
                )
            },
        )

    replicates = {str(name): int(row[REPLICATES_COLUMN] or 1) for name, row in edited.iterrows()}
    if st.session_state.replicates != replicates:
        st.session_state.replicates = replicates
        logger.info(f"Replicates per class: {replicates}")
    return replicates, pixel_size_um


def capacity_step(replicates: dict, step: str = "7") -> dict:
    """Plate settings, the well assignment they produce, and whether it fits.

    The same step the cell workflow uses, for the same reason: the assignment depends only on
    the classes and their replicates, so the whole plate can be shown before anything is cut.
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
            "many groups will not be collected. Reduce the replicates, lower the margin or "
            "spacing, or use a 384 well plate."
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


def _packing_controls(step: str) -> packing.PackingParams:
    """Amount, circle sizes, the gap between circles, and the seed.

    All of them live inside the fragment, because none changes how many wells the plan needs —
    so the plate the user approved in step 7 stays valid while they tune these.
    """
    amount_column, min_column, max_column, gap_column, seed_column = st.columns([3, 2, 2, 2, 1])

    with amount_column:
        area_per_replicate = st.number_input(
            "Tissue per replicate (µm²)",
            min_value=1.0,
            max_value=100_000_000.0,
            value=packing.DEFAULT_AREA_PER_REPLICATE_UM2,
            step=1_000.0,
            key=f"area_per_replicate_{step}",
            help=(
                "How much tissue goes into each well. Circles are added until this is reached, "
                "so this is the amount the experiment is specified in."
            ),
        )
    with min_column:
        min_area = st.number_input(
            "Smallest circle (µm²)",
            min_value=1.0,
            max_value=1_000_000.0,
            value=packing.DEFAULT_MIN_CIRCLE_AREA_UM2,
            step=10.0,
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
            min_value=1.0,
            max_value=1_000_000.0,
            value=packing.DEFAULT_MAX_CIRCLE_AREA_UM2,
            step=50.0,
            key=f"max_circle_{step}",
            help=(
                "Bigger circles reach the target with fewer cuts, so the collection runs "
                "faster, but they only fit in the wider parts of a region."
            ),
        )
    with gap_column:
        gap = st.number_input(
            "Gap between circles (µm)",
            min_value=0.0,
            max_value=200.0,
            value=packing.DEFAULT_SPACING_UM,
            step=1.0,
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
            help="Same seed and settings, same circles. Recorded in provenance.json.",
        )

    with st.expander("Effort"):
        max_attempts = st.number_input(
            "Attempts before a region is called full",
            min_value=50,
            max_value=20_000,
            value=packing.DEFAULT_MAX_ATTEMPTS,
            step=100,
            key=f"max_attempts_{step}",
            help=(
                "How many failed placements in a row before the app stops trying to fit more "
                "into a region. Higher fills a crowded region a little better and takes longer."
            ),
        )

    return packing.PackingParams(
        area_per_replicate_um2=float(area_per_replicate),
        min_circle_area_um2=float(min_area),
        max_circle_area_um2=float(max_area),
        spacing_um=float(gap),
        max_attempts=int(max_attempts),
        seed=int(seed),
    )


@st.fragment
def collect_step(patches, replicates: dict, settings: dict, pixel_size_um, step: str = "8") -> None:
    """Choose what to cut out of each region, preview it, and offer the export.

    A fragment, so tuning the circle settings reruns only this step and the export below it
    instead of re-projecting the regions and re-drawing the plate (`decisions.md` 051).
    """
    st.markdown(f"## Step {step}: What to collect, and export")

    options = list(CollectMode) if pixel_size_um else [CollectMode.WHOLE]
    mode = st.radio(
        "What to collect from each region",
        options=options,
        format_func=lambda option: COLLECT_LABELS[option],
        key=f"collect_mode_{step}",
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
            "replicate are areas in µm². Enter one in step 6 to pack circles."
        )

    if mode is CollectMode.CIRCLES:
        shapes, replicate_of, params = _collect_circles(patches, replicates, pixel_size_um, step)
        if shapes is None:
            return
    else:
        shapes, replicate_of, params = _collect_whole(patches, replicates, pixel_size_um)

    _preview(shapes, replicate_of)
    _export(shapes, replicate_of, settings, params, pixel_size_um, mode, step)


def _collect_circles(patches, replicates: dict, pixel_size_um: float, step: str):
    """Pack circles, report what they achieved, and hand back the shapes to cut."""
    params = _packing_controls(step)

    try:
        params.validate()
    except packing.PackingError as error:
        st.error(str(error))
        return None, None, None

    st.markdown("**Before packing** — what each class holds against what you have asked for:")
    estimate = packing.capacity(patches, replicates, params, pixel_size_um)
    st.dataframe(_capacity_for_display(estimate), width="stretch")
    st.caption(
        "Randomly placed circles cover about 55% of an area at best, and the gap between them "
        "cuts that down further, so the packable estimate is well below the region area. It is "
        "an estimate — what was actually achieved is below."
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
        return None, None, None

    if result.n_circles == 0:
        st.warning(
            "No circles could be placed. The regions may all be narrower than the smallest "
            "circle — lower the smallest circle area, or reduce how far a region may reach in "
            "step 5 so the regions are less fragmented."
        )
        return None, None, None

    _report_packing(result, params, pixel_size_um)

    circles = geojson.synthesize_qupath_columns(
        result.circles, "circle", source=st.session_state.gdf
    )
    return circles, circles[REPLICATE], params


def _capacity_for_display(estimate: pandas.DataFrame) -> pandas.DataFrame:
    """Rename and round the capacity table for showing to a user."""
    names = {
        "region_area_um2": "Region area (µm²)",
        "packable_estimate_um2": "Can hold about (µm²)",
        "requested_um2": "You asked for (µm²)",
        "shortfall_um2": "Short by (µm²)",
        "fillable_replicates": "Replicates fillable",
    }
    return estimate.rename(columns=names).round(0)


def _report_packing(
    result: packing.PackingResult, params: packing.PackingParams, pixel_size_um
) -> None:
    """Achieved against requested, and everything that silently changes the amount."""
    st.write(
        f"**{result.n_circles:,} circles** across {len(result.achieved)} replicates, "
        f"totalling {result.circles[packing.CIRCLE_AREA].sum():,.0f} µm²."
    )
    st.dataframe(result.achieved.round(1), width="stretch")

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
            "narrow the gap between circles, lower the smallest circle area, raise the attempts "
            "under Effort, or include more of the class by lowering how far a region may reach."
        )
    else:
        st.success("Every replicate reached the amount you asked for.")

    if result.n_regions_too_small:
        st.warning(
            f"{result.n_regions_too_small} region(s) are too narrow to hold even one circle of "
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
            "tolerance below."
        )
    elif fraction:
        st.caption(
            f"Smoothing at the default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance takes "
            f"{fraction:.1%} ({lost_um2:,.0f} µm²) off the areas above."
        )

    if result.n_discarded:
        st.caption(
            f"{result.n_discarded} circles were placed but not needed once every replicate was "
            "full, and are not being collected."
        )


def _collect_whole(patches, replicates: dict, pixel_size_um):
    """Deal whole regions across replicates and report what each one holds."""
    replicate_of = regions.deal_patches(patches, replicates)

    frame = pandas.DataFrame(
        {
            CLASS_NAME: patches[CLASS_NAME],
            "replicate": replicate_of,
            "Regions": 1,
            "Cells": patches[regions.N_CELLS],
            _area_column(pixel_size_um): _as_area(patches.geometry.area, pixel_size_um),
        }
    ).dropna(subset=["replicate"])

    if not frame.empty:
        st.dataframe(
            frame.groupby([CLASS_NAME, "replicate"]).sum(numeric_only=True).round(1),
            width="stretch",
        )

    _report_starved_replicates(patches, replicates, replicate_of)
    return patches, replicate_of, None


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
            f"{int((patches[CLASS_NAME] == name).sum())} region(s)"
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


def _export(shapes, replicate_of, settings: dict, params, pixel_size_um, mode, step: str) -> None:
    """Build the plan from whichever shapes were chosen, and hand off to the shared export."""
    recorded = {
        "plate": settings["plate_type"],
        "margins": settings["margins"],
        "step_row": settings["step_row"],
        "step_col": settings["step_col"],
        "randomize_wells": settings["randomize"],
        "classes": st.session_state.selected_classes,
        "replicates": st.session_state.replicates,
        "collect_mode": mode.value,
    }
    recorded.update(st.session_state.region_params or {})
    if params is not None:
        recorded.update(vars(params))
        st.session_state.packing_params = vars(params)

    plan, samples_and_wells = plan_from_selection(
        gdf=shapes,
        replicate_of=replicate_of,
        wells=settings["wells"],
        samples_and_wells=settings.get("samples_and_wells"),
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        pixel_size_um=pixel_size_um,
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

    replicates, pixel_size = replicates_step(report, pixel_size, step="6")
    st.divider()

    settings = capacity_step(replicates, step="7")
    st.divider()

    collect_step(patches, replicates, settings, pixel_size, step="8")
