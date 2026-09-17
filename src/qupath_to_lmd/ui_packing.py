"""The cellular-neighbourhood workflow: project cells into regions, then collect them.

A single cell is too little tissue to collect as mini-bulk, and outlining a neighbourhood by
hand is slow and unrepeatable. This workflow derives the tissue that belongs to each class from
the cells themselves, merges it into contiguous regions, and collects those.

Circle packing inside the regions is the next step and is not here yet; see ROADMAP.md.
"""

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import budget, geojson, plate, plot, regions, ui_shared
from qupath_to_lmd.model import CLASS_NAME, plan_from_selection

REPLICATES_COLUMN = "Replicates"


@st.cache_data(show_spinner="Projecting cells into regions...")
def _cached_projection(_gdf, cache_key: tuple, params: regions.RegionParams, include: tuple):
    """The projection, cached on everything that determines it.

    Streamlit reruns the whole script on every widget change and the tessellation costs about
    1.5 s at 20 000 cells, so without this every unrelated control would pay for it again
    (`decisions.md` 050).

    The QuPath fields are added here rather than at export, so the frame every later step
    handles is already one the download bundle can write back out.
    """
    patches, report = regions.project(_gdf, params, include=list(include))
    return geojson.synthesize_qupath_columns(patches, "region", source=_gdf), report


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
        "merged into one **region**. A region is what gets collected, so a whole neighbourhood "
        "goes into a well rather than one cell.\n\n"
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
                "the blank slide around it and the laser would cut glass."
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
            f"class**, totalling {enclosed:,.0f} {unit}. The laser follows a region's outer "
            "outline only, so that enclosed tissue would be cut and collected along with the "
            "region around it. Nothing stops you continuing — but that well will hold a mixture. "
            "To avoid it, leave the enclosing class out, or collect the enclosed class instead."
        )


def replicates_step(
    patches, report, pixel_size_um: float | None, step: str = "6"
) -> tuple[list[budget.ClassBudget], pandas.Series, float | None]:
    """How many replicates per class, and how the regions divide between them."""
    st.markdown(f"## Step {step}: Replicates")
    st.markdown(
        "Each replicate of each class is collected into its own well. A class's regions are "
        "shared out between its replicates, largest first into whichever replicate holds the "
        "least so far, so the replicates end up comparable in amount."
    )

    scale_column, _spacer = st.columns([2, 3])
    with scale_column:
        pixel_size_um = ui_shared.pixel_size_control()

    available = report.per_class["patches"]
    editable = pandas.DataFrame({REPLICATES_COLUMN: 1}, index=available.index)
    edited = st.data_editor(
        editable,
        width="stretch",
        key=f"replicates_editor_{step}_{len(available)}",
        column_config={
            REPLICATES_COLUMN: st.column_config.NumberColumn(
                REPLICATES_COLUMN,
                min_value=1,
                step=1,
                format="%d",
                help=(
                    "A class cannot fill more replicates than it has regions. Ask for more and "
                    "the extra replicates stay empty, which is reported below."
                ),
            )
        },
    )

    replicates = {
        str(name): int(row[REPLICATES_COLUMN] or 1) for name, row in edited.iterrows()
    }
    replicate_of = regions.deal_patches(patches, replicates)

    achieved = _achieved_table(patches, replicate_of, pixel_size_um)
    st.dataframe(achieved, width="stretch")
    _report_replicates(replicates, replicate_of, patches, achieved)

    budgets = [
        budget.ClassBudget(
            class_name=name,
            replicates=count,
            # The amount is not requested here, it is whatever the class's regions hold — so
            # this records the share each replicate actually gets rather than a target.
            per_replicate=float(
                _as_area(
                    patches.loc[patches[CLASS_NAME] == name].geometry.area.sum(), pixel_size_um
                )
                / count
            ),
        )
        for name, count in replicates.items()
    ]
    return budgets, replicate_of, pixel_size_um


def _achieved_table(patches, replicate_of: pandas.Series, pixel_size_um: float | None):
    """Per class and replicate: how many regions, how many cells, how much tissue."""
    frame = pandas.DataFrame(
        {
            CLASS_NAME: patches[CLASS_NAME],
            "replicate": replicate_of,
            "Regions": 1,
            "Cells": patches[regions.N_CELLS],
            _area_column(pixel_size_um): _as_area(patches.geometry.area, pixel_size_um),
        }
    ).dropna(subset=["replicate"])

    if frame.empty:
        return frame

    grouped = frame.groupby([CLASS_NAME, "replicate"]).sum(numeric_only=True)
    return grouped.round(2)


def _report_replicates(replicates: dict, replicate_of: pandas.Series, patches, achieved) -> None:
    """Warn where a class cannot fill the replicates it was asked for."""
    starved = {}
    for name, wanted in replicates.items():
        filled = int(
            replicate_of[patches[CLASS_NAME] == name].dropna().nunique()
        )
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
    elif not achieved.empty:
        st.success("Every replicate of every class has at least one region.")


def capacity_step(budgets: list[budget.ClassBudget], step: str = "7") -> dict:
    """Plate settings, the well assignment they produce, and whether it fits.

    The same step the cell workflow uses, for the same reason: the assignment depends only on
    the classes and their replicates, so the whole plate can be shown before anything is cut.
    """
    settings = ui_shared.plate_settings_step(step=step)

    groups = budget.group_keys(budgets)
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


def export_step(patches, replicate_of, settings, params, pixel_size_um, step: str = "8") -> None:
    """Show what will be cut, then build the plan and hand off to the shared export."""
    st.markdown(f"## Step {step}: What will be cut")

    labels = replicate_of.map(
        lambda value: f"replicate {int(value)}" if pandas.notna(value) else None
    )
    with st.spinner("Drawing the collection..."):
        figure = plot.plot_shapes(
            patches,
            labels=labels,
            calibration_array=st.session_state.calib_array,
            title="What will be cut, coloured by replicate",
        )
    st.pyplot(figure, width="content")
    st.caption(
        "Classes are merged here so you can judge whether the replicates are spread and "
        "comparable across the tissue."
    )

    plan, samples_and_wells = plan_from_selection(
        gdf=patches,
        replicate_of=replicate_of,
        wells=settings["wells"],
        samples_and_wells=settings.get("samples_and_wells"),
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        pixel_size_um=pixel_size_um,
        workflow="regions",
        params={
            "plate": settings["plate_type"],
            "margins": settings["margins"],
            "step_row": settings["step_row"],
            "step_col": settings["step_col"],
            "randomize_wells": settings["randomize"],
            "radius_factor": params.radius_factor,
            "max_radius_px": params.max_radius_px,
            "classes": st.session_state.selected_classes,
        },
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

    budgets, replicate_of, pixel_size = replicates_step(patches, report, pixel_size, step="6")
    st.divider()

    settings = capacity_step(budgets, step="7")
    st.divider()

    params = regions.RegionParams(**(st.session_state.region_params or {}))
    export_step(patches, replicate_of, settings, params, pixel_size, step="8")
