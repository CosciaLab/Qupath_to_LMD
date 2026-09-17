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
    requests: list
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


@st.cache_data(show_spinner=False)
def _cached_spacing(_gdf, cache_key: tuple) -> float:
    """How far apart neighbouring cells are, for the default reach."""
    return regions.median_cell_spacing(_gdf)


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
        "Each cell is given the tissue nearest to it, and touching cells of the same class are "
        "merged into one **region**. Regions are what you collect from, so a whole "
        "neighbourhood can go into a well rather than one cell. A region covers the space "
        "*between* its cells too, so it reaches past the outlines QuPath drew."
    )

    controls, feedback = st.columns([1, 2], gap="medium")

    with controls:
        spacing_px = _cached_spacing(gdf, ui_shared.shape_fingerprint(gdf))
        unit = "µm" if pixel_size_um else "px"
        scale = pixel_size_um or 1.0
        default_reach = round(regions.DEFAULT_RADIUS_FACTOR * spacing_px * scale)

        reach = st.number_input(
            f"Maximum reach from each cell ({unit})",
            min_value=1,
            max_value=100_000,
            value=max(1, int(default_reach)),
            step=1,
            format="%d",
            key=f"max_reach_{step}",
            help=(
                "A region is the tissue nearest to its cell and never further away than this. "
                "It is the only thing bounding the projection: without it the outermost cells "
                "would claim the empty slide around them, and empty space inside the tissue "
                "would be handed to whichever cell happened to be nearest.\n\n"
                f"Neighbouring cells here sit about {spacing_px * scale:,.0f} {unit} apart, so "
                "the default is three times that."
            ),
        )

        params = regions.RegionParams(max_radius_px=float(reach) / scale)
        try:
            patches, report = _cached_projection(
                gdf, ui_shared.shape_fingerprint(gdf), params, tuple(selected)
            )
        except regions.RegionError as error:
            st.error(str(error))
            logger.error(f"Region projection failed: {error}")
            return None, None

        st.caption(
            f"Gaps wider than {2 * reach:,.0f} {unit} are left uncollected. A smaller reach "
            "splits the tissue into more, smaller regions; a larger one merges them into fewer, "
            "bigger ones."
        )

        if st.session_state.region_params != vars(params):
            st.session_state.region_params = vars(params)
            logger.info(f"Region parameters: {vars(params)}")

        ui_shared.show_amounts(report.summary(pixel_size_um))
        st.write(
            f"**{report.n_patches:,} regions** from {report.n_cells_kept:,} cells across "
            f"{len(report.per_class)} classes."
        )

        if report.n_duplicate_centroids:
            st.warning(
                f"{report.n_duplicate_centroids:,} cells sit at exactly the same position as "
                "another cell. Only one of each pair can own the tissue around it, so the others "
                "are left out. This usually means the same cells were exported twice."
            )

    with feedback:
        with st.spinner("Drawing regions..."):
            figure = plot.plot_regions_and_circles(
                patches, calibration_array=st.session_state.calib_array
            )
        st.pyplot(figure, width="stretch")
        st.caption(
            "Fill colour is the class. Dashed triangle and crosses are your calibration points; "
            "regions far outside it are the ones at risk of distortion."
        )

    return patches, report


# The per-class table. Every column is something the user sets for one class, so they sit in one
# row per class rather than scattered between a table and a row of global inputs
# (`decisions.md` 070).
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


def _run_controls(step: str) -> tuple[float | None, packing.PackingParams]:
    """The two settings that are not per class: the image scale and the seed."""
    scale_column, seed_column, _spacer = st.columns([2, 1, 3])
    with scale_column:
        pixel_size_um = ui_shared.pixel_size_control()
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
    return pixel_size_um, packing.PackingParams(seed=int(seed))


def _request_table(report, step: str, with_circles: bool) -> list[packing.ClassPacking]:
    """One row per class: replicates, amount, circle sizes and the gap.

    All of it in one table because every number is a property of one class, and a user deciding
    what to take from `Tumor` wants those numbers together rather than split between a table and
    a row of controls somewhere else.
    """
    classes = report.per_class.index
    columns = {REPLICATES_COLUMN: packing.DEFAULT_REPLICATES}
    if with_circles:
        columns[AMOUNT_COLUMN] = int(packing.DEFAULT_AREA_PER_REPLICATE_UM2)
        columns[MIN_CIRCLE_COLUMN] = int(packing.DEFAULT_MIN_CIRCLE_AREA_UM2)
        columns[MAX_CIRCLE_COLUMN] = int(packing.DEFAULT_MAX_CIRCLE_AREA_UM2)
        columns[GAP_COLUMN] = int(packing.DEFAULT_SPACING_UM)

    steps = {
        REPLICATES_COLUMN: 1,
        AMOUNT_COLUMN: 1_000,
        MIN_CIRCLE_COLUMN: 10,
        MAX_CIRCLE_COLUMN: 50,
        GAP_COLUMN: 1,
    }
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
        pandas.DataFrame(columns, index=classes),
        width="stretch",
        key=f"request_editor_{step}_{len(classes)}_{int(with_circles)}",
        column_config=configuration,
    )

    requests = [
        packing.ClassPacking(
            class_name=str(name),
            replicates=int(row[REPLICATES_COLUMN] or 1),
            area_per_replicate_um2=float(row.get(AMOUNT_COLUMN, 0) or 0),
            min_circle_area_um2=float(
                row.get(MIN_CIRCLE_COLUMN, packing.DEFAULT_MIN_CIRCLE_AREA_UM2) or 1
            ),
            max_circle_area_um2=float(
                row.get(MAX_CIRCLE_COLUMN, packing.DEFAULT_MAX_CIRCLE_AREA_UM2) or 1
            ),
            spacing_um=float(row.get(GAP_COLUMN, 0) or 0),
        )
        for name, row in edited.iterrows()
    ]
    recorded = [vars(item) for item in requests]
    if st.session_state.region_budgets != recorded:
        st.session_state.region_budgets = recorded
        logger.info(f"Per-class requests: {recorded}")
    return requests


def collect_step(patches, report, pixel_size_um, step: str = "6") -> Collected | None:
    """Decide what to cut, how much of it, and into how many replicates.

    The table of settings sits directly above the picture of what they produce, so the loop is
    change-a-number, look down, change again (`decisions.md` 070). It comes before the plate
    because the amount and the replicate count are what the plate has to accommodate.
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
    packing_wanted = mode is CollectMode.CIRCLES
    if not pixel_size_um:
        st.caption(
            "Packing circles needs the image scale, because circle sizes and the amount per "
            "replicate are areas in µm². Enter one below to pack circles."
        )

    pixel_size_um, params = _run_controls(step)
    requests = _request_table(report, step, with_circles=packing_wanted)

    if not packing_wanted:
        return _collect_whole(patches, requests, pixel_size_um)

    if not pixel_size_um:
        st.warning(
            "The image scale was cleared, so circles cannot be sized. Enter one above, or "
            "collect whole regions instead."
        )
        return None
    return _collect_circles(patches, requests, params, pixel_size_um)


def _collect_circles(patches, requests, params, pixel_size_um):
    """Pack, draw the result under the settings, and hand back the shapes to cut."""
    try:
        for item in requests:
            item.validate()
    except packing.PackingError as error:
        st.error(str(error))
        return None

    cache_key = (
        ui_shared.shape_fingerprint(st.session_state.gdf),
        tuple(sorted((st.session_state.region_params or {}).items())),
        tuple(tuple(sorted(vars(item).items())) for item in requests),
        tuple(sorted(vars(params).items())),
        pixel_size_um,
    )
    try:
        result = _cached_packing(patches, requests, params, cache_key, pixel_size_um)
    except packing.PackingError as error:
        st.error(str(error))
        logger.error(f"Packing failed: {error}")
        return None

    circles = geojson.synthesize_qupath_columns(
        result.circles, "circle", source=st.session_state.gdf
    )

    _draw(patches, circles, circles[REPLICATE] if result.n_circles else None)

    if result.n_circles == 0:
        st.warning(
            "No circles could be placed. The regions may all be narrower than the smallest "
            "circle — lower the smallest circle area in the table, or reduce how far a region "
            "may reach in step 5 so the regions are less fragmented."
        )
        return None

    _report_packing(result, requests)
    return Collected(
        shapes=circles,
        replicate_of=circles[REPLICATE],
        requests=requests,
        mode=CollectMode.CIRCLES,
        pixel_size_um=pixel_size_um,
        params=params,
    )


def _draw(patches, circles, replicate_of) -> None:
    """The tissue map with what will be cut on top of it: class by fill, replicate by outline."""
    with st.spinner("Drawing..."):
        figure = plot.plot_regions_and_circles(
            patches,
            circles,
            replicate_of=replicate_of,
            calibration_array=st.session_state.calib_array,
        )
    st.pyplot(figure, width="stretch")
    st.caption(
        "A pale fill is the class, for the regions and the circles alike. A dark outline is the "
        "replicate. Dashed triangle and crosses are your calibration points."
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


def _report_packing(result, requests) -> None:
    """Per replicate, what was achieved, and everything that quietly changes the amount.

    Below the settings and the picture: this is the detail a user reads once they have settled
    on an arrangement, not what they watch while tuning it.
    """
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
        st.warning(
            f"{len(short)} replicate(s) could not be filled:\n\n{lines}\n\n"
            "They will still be collected, with less tissue than you asked for. To fit more, for "
            "that class alone: narrow its gap, lower its smallest circle area, or include more "
            "of it by lowering how far a region may reach in step 5."
        )
    else:
        st.success("Every replicate reached the amount you asked for.")

    with st.expander("How much each class could hold, before packing"):
        ui_shared.show_amounts(_capacity_for_display(result.capacity))
        st.caption(
            "Randomly placed circles cover about 55% of an area at best, and the gap between "
            "them cuts that down further, so what a region can hold is well below its area."
        )

    if result.n_regions_too_small:
        st.warning(
            f"{result.n_regions_too_small:,} region(s) are too narrow to hold even one circle of "
            "the size asked for, so they contribute nothing. Lower the smallest circle area for "
            "that class to use them, or accept that this tissue is too fragmented to collect at "
            "that size."
        )

    zero = [item.class_name for item in requests if item.area_per_replicate_um2 <= 0]
    if zero:
        st.warning(f"Nothing will be collected for: {', '.join(zero)} — the amount is zero.")

    lost, fraction = packing.smoothing_loss(result.circles, export.DEFAULT_SIMPLIFY_TOLERANCE)
    if fraction > 0.05:
        st.warning(
            f"**Smoothing will take {fraction:.0%} of the circle area back off**, because the "
            f"default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance cuts the corners off a "
            "small circle. The amounts above are before smoothing, so each well will hold that "
            "much less than it says. Raise the smallest circle area, or lower the smoothing "
            "tolerance at the export step."
        )
    elif fraction:
        st.caption(
            f"Smoothing at the default {export.DEFAULT_SIMPLIFY_TOLERANCE:g} px tolerance takes "
            f"{fraction:.1%} off the amounts above."
        )


def _collect_whole(patches, requests, pixel_size_um) -> Collected:
    """Deal whole regions across replicates and report what each one holds."""
    replicates = {item.class_name: item.replicates for item in requests}
    replicate_of = regions.deal_patches(patches, replicates)

    _draw(patches, None, None)

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
        ui_shared.show_amounts(frame.groupby(["Class", "Replicate"]).sum(numeric_only=True))

    _report_starved_replicates(patches, replicates, replicate_of)
    return Collected(
        shapes=patches,
        replicate_of=replicate_of,
        requests=requests,
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


def plate_step(requests: list, step: str = "7") -> dict:
    """Plate settings, the well assignment they produce, and whether it fits.

    Comes after the collection is decided, so the well count it has to accommodate is already
    settled and the plate is shown once rather than redrawn on every parameter change.
    """
    settings = ui_shared.plate_settings_step(step=step)

    groups = budget.group_keys([item.as_budget() for item in requests])
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
        "requests": [vars(item) for item in collected.requests],
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

    settings = plate_step(collected.requests, step="7")
    st.divider()

    export_step(collected, settings, step="8")
