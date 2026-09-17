"""Steps used by both workflows.

This is the UI layer, so these functions may read and write `st.session_state` — that is
what they are for. The library modules (`geojson`, `plate`, `qc`, `export`, `model`) stay
pure and take explicit arguments.
"""

import json
from pathlib import Path

import streamlit as st
from loguru import logger

from qupath_to_lmd import export, extras, geojson, plate, plot, qc, stats
from qupath_to_lmd.model import CLASS_NAME

# The rendered number input carries `step` as an HTML attribute and browsers snap entries
# to that grid, so the step has to be as fine as the format can display or typed values get
# rounded. Four decimals matches what QuPath reports for pixel width.
PIXEL_SIZE_DECIMALS = 4
PIXEL_SIZE_STEP = 10**-PIXEL_SIZE_DECIMALS
PIXEL_SIZE_FORMAT = f"%.{PIXEL_SIZE_DECIMALS}f"
PIXEL_SIZE_MIN = PIXEL_SIZE_STEP
PIXEL_SIZE_MAX = 100.0

# Above this, the scale implied by different objects disagrees enough to suggest the export
# mixes images or was rescaled.
WIDE_SPREAD = 0.02

# Where the hosted app stops being comfortable. 40 000 shapes is about the most a TMA core
# yields, so anything much beyond it is whole-slide territory. Figures are measured, not
# guessed — see `facts.md` and `decisions.md` 051.
HOSTED_COMFORTABLE_SHAPES = 40_000
SCALE_BENCHMARKS = (
    # shapes, seconds per interaction, seconds per collection, peak MB
    (8_500, "0.5 s", "9 s", "690 MB"),
    (50_000, "0.6 s", "10 s", "710 MB"),
    (150_000, "2 s", "15 s", "940 MB"),
    (1_000_000, "16 s", "58 s", "2 700 MB"),
)
# Community Cloud documents 690 MB guaranteed and 2.7 GB maximum per app.
HOSTED_MEMORY_CEILING_MB = 2_700

WORKFLOWS = {
    "legacy": "Annotations — one class is one sample is one well",
    "cells": "Cell segmentation — pick classes, replicates and how much to collect",
    "regions": "Cellular neighbourhoods — merge cells into regions, then pack circles",
}


@st.cache_data(show_spinner="Reading and checking your GeoJSON...")
def _read_geojson(source):
    """Cached wrapper so a rerun does not re-parse the same upload."""
    return geojson.read_and_qc(source)


def reset_file_state():
    """Forget the uploaded file and everything derived from it."""
    for key in ("gdf", "geojson_report", "calibration_points", "calibs", "calib_array", "file_name"):
        st.session_state[key] = None


def upload_step(step: str = "1"):
    """Upload a GeoJSON and show what QC found. Returns the uploaded file or None."""
    st.markdown(f"""
                ## Step {step}: Upload and check .geojson file from Qupath
                Upload your .geojson file from qupath, order of calibration points is important
                """)

    uploaded_file = st.file_uploader(label="Choose a file", type="geojson", accept_multiple_files=False)

    if not uploaded_file:
        if st.session_state.file_name is not None:
            reset_file_state()
        return None

    if st.session_state.file_name != uploaded_file.name or st.session_state.gdf is None:
        logger.info(f"New file detected: {uploaded_file.name}")
        try:
            gdf, calibration_points, report = _read_geojson(uploaded_file)
        except geojson.GeojsonError as error:
            st.error(str(error))
            logger.error(str(error))
            st.stop()
        st.session_state.file_name = uploaded_file.name
        st.session_state.gdf = gdf
        st.session_state.calibration_points = calibration_points
        st.session_state.geojson_report = report

    report = st.session_state.geojson_report
    described = f"**{report.n_shapes_in_file:,} shapes**"
    if report.calibration_point_names:
        described += f" and {len(report.calibration_point_names)} named calibration points"
    st.write(f"This file holds {described}.")

    # One table rather than a stack of warning boxes. Six coloured boxes about classification
    # and geometry got skimmed and then ignored, which defeated the point of showing them at
    # all; the per-class detail belongs later in the workflow (`decisions.md` 064).
    st.dataframe(report.summary(), width="stretch")

    if report.n_unnamed_points:
        # Not in the table: this is about calibration, not about shapes, and it decides whether
        # the user can get past the calibration step at all.
        st.warning(
            f"{report.n_unnamed_points} point(s) in this file have no name, so they cannot be "
            "chosen as calibration points. Name each point annotation in QuPath's annotation "
            "list and export again."
        )

    # Carries the surviving count now that the table above does not repeat it.
    st.success(f"File check complete, {report.n_shapes_kept:,} shapes are ready to collect.")
    _report_scale(report.n_shapes_kept)
    return uploaded_file


def _report_scale(n_shapes: int) -> None:
    """Warn when a file is large enough that the hosted app will struggle.

    A whole-slide export can hold a million cells. That does not fit: it needs about 2.7 GB,
    which is the documented ceiling for a Community Cloud app, so it will hit the resource
    limit rather than merely feel slow. Better to say so before the user spends ten minutes
    finding out (`decisions.md` 051).
    """
    if n_shapes <= HOSTED_COMFORTABLE_SHAPES:
        return

    rows = "\n".join(
        f"| {shapes:,} | {per_interaction} | {per_collection} | {memory} |"
        for shapes, per_interaction, per_collection, memory in SCALE_BENCHMARKS
    )
    st.warning(
        f"**This file has {n_shapes:,} shapes, which is a lot for the hosted app.** Around "
        f"{HOSTED_COMFORTABLE_SHAPES:,} is about the most a single TMA core yields, so beyond "
        "that you are into whole-slide territory.\n\n"
        "Measured on this app:\n\n"
        "| shapes | per click or setting change | per collection | memory |\n"
        "| --- | --- | --- | --- |\n" + rows + "\n\n"
        f"The hosted app has about {HOSTED_MEMORY_CEILING_MB:,} MB, so a whole slide of a "
        "million cells does not fit — it will hit the resource limit, not just feel slow. "
        "Nothing stops you continuing here, but for a file this size it is worth either "
        "narrowing the selection in QuPath first, or running the app on your own machine:\n\n"
        "```\n"
        "git clone https://github.com/CosciaLab/Qupath_to_LMD\n"
        "cd Qupath_to_LMD\n"
        "uv sync\n"
        "uv run streamlit run streamlit_app.py\n"
        "```\n\n"
        "Locally you have your whole machine, and nothing is uploaded anywhere."
    )


def workflow_step(step: str = "2") -> str:
    """Choose a workflow. Suggests one from `objectType` once a file is loaded."""
    st.markdown(f"## Step {step}: Which kind of collection is this?")

    suggestion = None
    gdf = st.session_state.gdf
    if gdf is not None and "objectType" in gdf.columns:
        counts = gdf["objectType"].value_counts().to_dict()
        n_cells = counts.get("cell", 0) + counts.get("detection", 0)
        n_annotations = counts.get("annotation", 0)
        described = ", ".join(f"{count} {kind}s" for kind, count in counts.items())
        suggestion = "cells" if n_cells > n_annotations else "legacy"
        st.write(
            f"This file contains {described}. Based on that, the "
            f"**{'cell segmentation' if suggestion == 'cells' else 'annotations'}** workflow is "
            "probably what you want — but you decide."
        )

    options = list(WORKFLOWS)
    default_index = options.index(suggestion) if suggestion else 0
    chosen = st.radio(
        "Workflow",
        options=options,
        index=default_index,
        format_func=lambda key: WORKFLOWS[key],
        key="workflow_choice",
    )

    if st.session_state.workflow != chosen:
        logger.info(f"Workflow set to {chosen}")
        st.session_state.workflow = chosen
    return chosen


def calibration_step(step: str = "3"):
    """Pick three calibration points and report triangle coverage."""
    if st.session_state.gdf is None:
        return

    st.markdown(f"## Step {step}: Calibration points")

    available = st.session_state.calibration_points or {}
    if len(available) < 3:
        found = (
            "This file has no calibration points."
            if not available
            else f"This file has only {len(available)} calibration point(s): {', '.join(available)}."
        )
        st.error(
            f"**{found} Three are required and there is no way to continue without them.**\n\n"
            "The LMD needs three reference points to map image coordinates onto the stage. "
            "Without them any cutting file would be meaningless, so processing stops here.\n\n"
            "In QuPath: select the point tool, click three spots on the slide — ideally close "
            "to the tissue you want to cut — give each point annotation a name in the "
            "annotation list, then export again. The export must include the point annotations "
            "as well as your cells or regions; if you exported a selection, the points were "
            "probably left out."
        )
        logger.error(f"Stopping: {len(available)} calibration points found, 3 required")
        st.stop()

    options = list(available)
    c1 = st.selectbox("Select calibration point 1", options, index=0)
    c2 = st.selectbox("Select calibration point 2", options, index=1)
    c3 = st.selectbox("Select calibration point 3", options, index=2)

    st.session_state.calibs = [c1, c2, c3]
    logger.info(f"Calibration points chosen: {st.session_state.calibs}")

    triangle = qc.triangle_qc(
        st.session_state.gdf, st.session_state.calibration_points, st.session_state.calibs
    )

    if triangle.is_degenerate:
        repeated = len(set(st.session_state.calibs)) < 3
        st.error(
            "**These three calibration points do not form a triangle, so no collection can "
            "be made from them.**\n\n"
            + (
                "The same point is selected more than once. Pick three different points."
                if repeated
                else "All three points lie on a straight line. Pick three that form a proper "
                "triangle around your tissue."
            )
            + "\n\nThis has to stop here: the LMD software would accept the resulting file "
            "without complaint and cut in the wrong place."
        )
        st.session_state.calib_array = None
        logger.error(f"Stopping: degenerate calibration triangle from {st.session_state.calibs}")
        st.stop()

    st.session_state.calib_array = triangle.calibration_array
    st.write(f"{triangle.fraction_inside * 100:.2f}% of shapes are inside the calibration triangle")
    if triangle.is_concerning:
        st.warning(
            "Less than 25% of your shapes fall inside the calibration triangle. Shapes far "
            "outside it get distorted by the coordinate transform, so you may cut the wrong "
            "tissue. Consider calibration points closer to your annotations."
        )


def resolve_pixel_size() -> tuple[float | None, str]:
    """The scale in force, and where it came from.

    An override the user typed wins; otherwise the value QuPath's own area measurements imply,
    derived once when the file was read. Returns `(None, "none")` when neither is available —
    which is normal, because most exports carry no measurements and a user collecting a number
    of cells never needs a scale (`decisions.md` 038, 056).
    """
    override = st.session_state.pixel_size_um
    if override:
        return float(override), "entered"

    report = st.session_state.geojson_report
    if report is not None and report.implied_pixel_size_um:
        return float(report.implied_pixel_size_um), "estimated"

    return None, "none"


def pixel_size_control() -> float | None:
    """A compact scale input, to sit beside whatever needs a scale.

    Deliberately not a step of its own: users were confused about why the app wanted a pixel
    size at all. Next to the control that turns amounts into areas, it explains itself, because
    that is the only thing it feeds (`decisions.md` 057).
    """
    current, source = resolve_pixel_size()
    report = st.session_state.geojson_report
    estimate = report.implied_pixel_size_um if report is not None else None

    entered = st.number_input(
        "Image scale (µm per pixel)",
        min_value=PIXEL_SIZE_MIN,
        max_value=PIXEL_SIZE_MAX,
        value=float(current) if current else None,
        step=PIXEL_SIZE_STEP,
        format=PIXEL_SIZE_FORMAT,
        key="pixel_size_input",
        placeholder="e.g. 0.3467",
        help=(
            "How many micrometres one image pixel covers. It is needed only to express amounts "
            "as areas — collecting a number of shapes does not need it at all.\n\n"
            "Where to find it: QuPath, *Image → Image properties → Pixel width*.\n\n"
            "If your file carries QuPath measurements, this is filled in from them and you can "
            "leave it alone. Magnification does **not** determine pixel size — it is your "
            "camera's sensor pitch divided by the total magnification, so the same 20× objective "
            f"spans roughly {stats.SENSOR_PITCHES_UM[0] / 20:.2f}–{stats.SENSOR_PITCHES_UM[-1] / 20:.2f} "
            "µm/px across common cameras."
        ),
    )

    if entered:
        st.session_state.pixel_size_um = float(entered)
    elif source == "estimated":
        st.session_state.pixel_size_um = None

    value, source = resolve_pixel_size()
    _report_pixel_size(value, source, estimate, report)
    return value


def _report_pixel_size(value, source, estimate, report) -> None:
    """Say where the scale came from, and flag a disagreement or a wide spread."""
    if value is None:
        st.caption(
            "No scale, so amounts are in numbers of shapes. Enter one to work in areas instead."
        )
        return

    if source == "estimated":
        st.caption(
            f"Estimated from this file's own QuPath measurements across "
            f"{report.n_area_measurements:,} shapes (spread {report.pixel_size_spread:.1%}). "
            "Type over it if you know better."
        )
    elif estimate:
        check = qc.compare_pixel_size(value, estimate, report.n_area_measurements, report.pixel_size_spread)
        if check.is_concerning:
            st.warning(
                f"Your value is **{check.ratio:.2f}×** what this file implies "
                f"({estimate:.4f} µm/px from {report.n_area_measurements:,} shapes). One of the "
                "two is wrong — usually a scale read from the wrong image, or a factor-of-ten "
                "slip. A 2× error in scale is a 4× error in every area."
            )
        else:
            st.caption(f"Agrees with this file's own measurements ({estimate:.4f} µm/px).")
    else:
        st.caption("This file carries no measurements, so the value could not be cross-checked.")

    if report is not None and report.pixel_size_spread and report.pixel_size_spread > WIDE_SPREAD:
        st.warning(
            f"The scale implied by this file varies by {report.pixel_size_spread:.1%} between "
            "shapes. That usually means the export mixes images, or was rescaled — worth "
            "checking before relying on any area."
        )


def shape_fingerprint(gdf) -> tuple:
    """A cheap identity for the working shapes, for cache keys.

    Cannot be the filename alone: exploding a class rewrites the class names in place. Cannot
    hash the frame itself either — Streamlit would walk 150 000 rows on every rerun, which is
    what the cache is meant to avoid.
    """
    return (
        st.session_state.get("file_name"),
        len(gdf),
        tuple(sorted(gdf[CLASS_NAME].dropna().unique())),
    )


@st.cache_data(show_spinner=False)
def _cached_statistics(_gdf, cache_key: tuple, pixel_size_um: float | None):
    """Per-class statistics, cached so a rerun does not recompute them (twice)."""
    return stats.class_statistics(_gdf, pixel_size_um=pixel_size_um)


def class_selection_step(pixel_size_um: float | None, step: str = "4") -> list[str]:
    """Show what each class holds and what it looks like, then let the user choose.

    Shared by the cell and regions workflows: both start by asking which biology to collect, and
    a user switching between them should not meet two different tables.

    The table and the picture sit side by side because they answer the same question from two
    directions — how much is there, and is it where I expect. Seeing the input drawn also
    confirms the file is the one the user meant to upload, which nothing else in the app does
    (`decisions.md` 071).
    """
    gdf = st.session_state.gdf

    st.markdown(f"## Step {step}: Choose which classes to collect")

    numbers, picture = st.columns([1, 2], gap="medium")

    with numbers:
        _, source = resolve_pixel_size()
        if pixel_size_um and source == "estimated":
            st.markdown(
                f"Areas are measured from the shapes at **{pixel_size_um:.4f} µm/px**, estimated "
                "from this file's own QuPath measurements. You can change the scale later."
            )
        elif pixel_size_um:
            st.markdown(
                f"Areas are measured from the shapes at **{pixel_size_um:.4f} µm/px**, the scale "
                "you entered."
            )
        else:
            st.markdown(
                "This file carries no measurements to estimate an image scale from, so amounts "
                "are in numbers of shapes. Enter a scale later to work in areas."
            )

        table = _cached_statistics(gdf, shape_fingerprint(gdf), pixel_size_um)
        display = stats.for_display(table)
        # Columns stay numeric so the table remains sortable; the format only trims the display.
        st.dataframe(
            display,
            width="stretch",
            column_config={
                name: st.column_config.NumberColumn(name, format=f"%.{stats.DECIMALS}f")
                for name in display.columns
                if name != stats.DISPLAY_COLUMNS["shapes"]
            },
        )

        all_classes = table.index.tolist()
        selected = st.multiselect(
            "Classes to collect",
            options=all_classes,
            default=st.session_state.selected_classes or all_classes,
            help="Everything after this step works only on the classes you keep here.",
        )

        if selected != st.session_state.selected_classes:
            st.session_state.selected_classes = selected
            logger.info(f"Classes selected: {selected}")

        if selected:
            kept = table.loc[selected]
            summary = f"**{int(kept['shapes'].sum()):,} shapes** across {len(selected)} classes"
            if "area_total_um2" in kept.columns:
                summary += f", totalling **{kept['area_total_um2'].sum():,.0f} µm²** of tissue"
            st.write(summary + ".")
        else:
            st.warning("No classes selected, so there is nothing to collect yet.")

    with picture:
        _draw_input(gdf, selected)

    return selected


def _draw_input(gdf, selected: list[str]) -> None:
    """Draw everything in the file, colouring what is kept and greying out the rest.

    Showing what was left out, rather than only what was taken, is what lets a user notice they
    have excluded something by mistake — or that they uploaded the wrong export.
    """
    with st.spinner("Drawing shapes..."):
        figure = plot.plot_shapes(
            gdf,
            included=selected,
            calibration_array=st.session_state.calib_array,
            title=f"{len(gdf):,} shapes in this file",
        )
    st.pyplot(figure, width="stretch")
    if len(gdf) > plot.SHAPE_LIMIT:
        st.caption(
            f"Over {plot.SHAPE_LIMIT:,} shapes, so each one is drawn as a dot rather than its "
            "outline. The outlines are still what gets cut."
        )
    st.caption(
        "Greyed-out shapes are the classes you left out. Dashed triangle and crosses are your "
        "calibration points; shapes far outside it are the ones at risk of distortion."
    )


def plate_settings_step(step: str = "5") -> dict:
    """Plate type, margin and spacing. Returns the settings and the usable wells."""
    st.markdown(f"""
                ## Step {step}: Decide which plate to collect into, either 384 or 96 well plate.
                Decide how many wells to make unavailable as a margin (for 384wp we suggest a margin of 2).
                Decide how many wells to leave blank in between, for easier pipetting.
                """)
    st.write("You can increase plate size by dragging bottom right corner")

    plate_col, margin_col, step_row_col, step_col_col, start_col, random_col = st.columns(6)
    with plate_col:
        plate_string = st.selectbox("Select a plate type", ("384 well plate", "96 well plate"))
    with margin_col:
        margin = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1)
    with step_row_col:
        step_row = st.number_input("Space between rows", min_value=1, max_value=10, value=1)
    with step_col_col:
        step_col = st.number_input("Space between columns", min_value=1, max_value=10, value=1)
    with start_col:
        start_well = st.text_input(
            "Start at well",
            value="",
            placeholder="auto",
            help=(
                "Fill the plate from this well onwards instead of the first usable one. For "
                "collecting several slides into one plate: run the first slide, note the last "
                "well it used, then start the next slide after it. Leave blank to start at the "
                "beginning."
            ),
        )

    with random_col:
        randomize = st.toggle(
            "Randomize wells",
            value=False,
            help=(
                "Spread samples over the plate instead of filling it in order, so a "
                "systematic plate-position effect cannot be mistaken for a biological one. "
                "Seeded, so the layout is still reproducible."
            ),
        )

    plate_type = plate_string.split(" ")[0]
    usable = plate.acceptable_wells(
        plate=plate_type, margins=margin, step_row=step_row, step_col=step_col
    )
    wells = plate.wells_from(usable, start_well)
    if start_well and wells is usable:
        st.warning(
            f"**{start_well}** is not one of the {len(usable)} wells this margin and spacing "
            "leave usable, so filling starts from the beginning instead."
        )

    return {
        "plate_type": plate_type,
        "margins": margin,
        "step_row": step_row,
        "step_col": step_col,
        "randomize": randomize,
        "start_well": start_well.strip().upper() or None,
        "wells": wells,
    }


def plate_layout_step(settings: dict, uploaded_file) -> None:
    """Plate views, layout confirmation, and the custom samples-and-wells override."""
    plate_type, wells = settings["plate_type"], settings["wells"]

    randomize = settings["randomize"]

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Show plate format with default wells"):
            st.session_state.view_mode = "default"
            logger.info("Show plate format with default wells -- ButtonPress")
    with col2:
        if st.button("Show plate format with samples from geojson"):
            logger.info("Show plate format with samples from geojson -- ButtonPress")
            if uploaded_file is None:
                st.warning("Please upload a file first.")
            else:
                st.session_state.view_mode = "samples"

    params = {k: settings[k] for k in ("plate_type", "margins", "step_row", "step_col", "randomize")}
    params_changed = st.session_state.plate_gen_params != params

    if st.session_state.view_mode == "default":
        layout = plate.default_layout(plate=plate_type)
        st.dataframe(layout.style.map(plate.highlight(set(wells))), width="stretch")

    elif st.session_state.view_mode == "samples":
        if uploaded_file is None:
            st.warning("File no longer available. Please upload a file or switch to the default view.")
        elif st.session_state.gdf is not None:
            if params_changed or st.session_state.plate_df is None:
                st.session_state.plate_gen_params = params
                layout, unplaced = plate.sample_layout(
                    classes=st.session_state.gdf[CLASS_NAME].unique().tolist(),
                    plate=plate_type,
                    wells=wells,
                    randomize=randomize,
                )
                st.session_state.plate_df = layout
                if unplaced:
                    st.warning(
                        f"{len(unplaced)} classes do not fit in the {len(wells)} usable wells and are "
                        f"not placed: {', '.join(unplaced[:10])}{' ...' if len(unplaced) > 10 else ''}. "
                        "Reduce the margin or spacing, use a 384 well plate, or collect in two rounds."
                    )
            if st.session_state.plate_df is not None:
                plate_preview(
                    plate.layout_to_saw(st.session_state.plate_df),
                    plate_type,
                    wells=wells,
                    key_suffix="legacy",
                )

    if st.button("Confirm and use this plate layout"):
        logger.info("Confirm and use this plate layout -- ButtonPress")
        if st.session_state.view_mode == "samples" and st.session_state.plate_df is not None:
            st.session_state.saw = plate.layout_to_saw(st.session_state.plate_df)
            st.session_state.use_plate_wells = True
            _report_saw(qc.validate_saw(
                st.session_state.saw,
                st.session_state.gdf[CLASS_NAME].unique().tolist(),
                plate=plate_type,
            ), plate_type, success="Samples and wells layout confirmed, you are ready to process!")
        else:
            st.warning("Please generate and view a plate layout with samples from your GeoJSON first.")

    _custom_saw_step(plate_type)


def _report_saw(report, plate_type: str, success: str) -> None:
    """Show what samples-and-wells validation found."""
    if report.missing_classes:
        st.warning(
            f"{len(report.missing_classes)} classes in your file have no well and will not be "
            f"collected: {', '.join(sorted(report.missing_classes)[:10])}"
        )
    if report.duplicate_wells:
        st.warning(f"Wells receiving more than one class: {report.duplicate_wells}")
    if report.invalid_wells:
        st.error(
            f"These wells do not exist on a {plate_type} well plate: {sorted(report.invalid_wells)}. "
            "Fix the scheme or change the plate type."
        )
    else:
        st.success(success)


def _custom_saw_step(plate_type: str) -> None:
    """Upload a samples-and-wells dictionary, overriding the generated layout."""
    if st.button("Upload custom samples and wells dictionary, will override"):
        logger.info("Upload custom samples and wells dictionary -- ButtonPress")
        st.session_state.show_saw_uploader = True

    if not st.session_state.show_saw_uploader:
        return

    uploaded_saw = st.file_uploader(
        label="Choose a custom samples-and-wells file (.txt or .json)",
        type=["txt", "json"],
        accept_multiple_files=False,
        key="saw_uploader",
    )
    if uploaded_saw is None:
        return

    try:
        candidate = plate.parse_saw_file(uploaded_saw)
    except plate.SawParseError as error:
        st.error(f"Could not read that samples-and-wells file: {error}")
        logger.error(f"Samples-and-wells parse failed: {error}")
        return

    if st.session_state.gdf is None:
        st.warning("Upload a GeoJSON first, so the scheme can be checked against your classes.")
        return

    report = qc.validate_saw(
        candidate, st.session_state.gdf[CLASS_NAME].unique().tolist(), plate=plate_type
    )
    if report.invalid_wells:
        _report_saw(report, plate_type, success="")
        return

    st.session_state.saw = candidate
    st.session_state.use_plate_wells = False
    st.session_state.show_saw_uploader = False
    _report_saw(report, plate_type, success=f"Custom samples and wells loaded and checked: {len(candidate)} classes.")


def plate_preview(
    samples_and_wells: dict[str, str],
    plate_type: str,
    wells: list[str] | None = None,
    key_suffix: str = "",
) -> None:
    """Show the plate with each sample in its well, and offer the scheme as a download.

    The single plate renderer for both workflows, so what a user sees does not depend on
    which one they picked (`decisions.md` 045).
    """
    if not samples_and_wells:
        st.warning("No wells assigned yet.")
        return

    layout = plate.placement_dataframe(samples_and_wells, plate=plate_type)
    st.dataframe(layout.style.map(plate.highlight(set(samples_and_wells))), width="stretch")

    taken = set(samples_and_wells.values())
    used = sorted(taken, key=lambda well: (well[0], int(well[1:])))
    caption = f"{len(samples_and_wells)} wells in use on a {plate_type} well plate"
    if used:
        caption += f", {used[0]} to {used[-1]}"
        if wells:
            # Against the wells, not the group names — comparing with the dict's keys meant
            # nothing ever matched and the "start at" always named the first usable well.
            remaining = [well for well in wells if well not in taken]
            if remaining:
                caption += f". For another slide into this plate, start at **{remaining[0]}**"
            else:
                caption += ". This plate is now full"
    st.caption(caption + ".")

    if wells:
        with st.expander(f"Which wells the current margin and spacing leave usable ({len(wells)})"):
            usable = plate.default_layout(plate=plate_type)
            st.dataframe(usable.style.map(plate.highlight(set(wells))), width="stretch")

    st.download_button(
        label="Download samples and wells setup",
        data=json.dumps(samples_and_wells, indent=4),
        file_name="samples_and_wells.json",
        mime="application/json",
        key=f"saw_download_{plate_type}_{len(samples_and_wells)}_{key_suffix}",
    )


def editable_plate(
    samples_and_wells: dict[str, str],
    plate_type: str,
    key_suffix: str = "",
) -> dict[str, str]:
    """Let the user move samples between wells by editing the plate directly.

    Streamlit has no drag-and-drop into a grid, and a real one would mean a custom frontend
    (`decisions.md` 055). Editing the plate in place is the same job done with a dropdown per
    well, which cannot produce a typo or name a sample that does not exist.

    Opt-in: the automatic assignment is almost always what the user wants, and an editor shown
    unasked invites fiddling with something that was already correct.

    Returns the assignment to use — the edited one if the user opened the editor, otherwise the
    one passed in.
    """
    if not st.checkbox(
        "Move samples between wells by hand",
        value=False,
        key=f"edit_plate_{key_suffix}",
        help=(
            "Opens the plate as an editable table. Pick a sample from any well's dropdown to "
            "move it there, or clear a well to leave it empty. The automatic layout is used "
            "unless you change something."
        ),
    ):
        return samples_and_wells

    layout = plate.placement_dataframe(samples_and_wells, plate=plate_type)
    options = sorted(samples_and_wells)
    edited = st.data_editor(
        layout,
        width="stretch",
        key=f"plate_editor_{key_suffix}_{len(samples_and_wells)}",
        column_config={
            column: st.column_config.SelectboxColumn(column, options=options, required=False)
            for column in layout.columns
        },
    )

    by_well = plate.layout_to_saw(edited)
    duplicated = [name for name in options if list(by_well.values()).count(by_well.get(name, "")) > 1]
    placed = set(by_well)
    missing = [name for name in options if name not in placed]

    if missing:
        st.error(
            f"{len(missing)} sample(s) are no longer on the plate and will not be cut: "
            f"{', '.join(missing[:8])}. Put them back in a well, or untick the box above to "
            "return to the automatic layout."
        )
    if duplicated:
        st.warning(f"More than one sample shares a well: {', '.join(sorted(set(duplicated))[:8])}.")
    if not missing and not duplicated:
        st.success(f"Using your layout: {len(by_well)} samples placed by hand.")

    return by_well


def export_step(settings: dict, build_plan, step: str = "6") -> None:
    """Process the collection and offer the download bundle.

    Args:
        settings: plate settings from `plate_settings_step`.
        build_plan: callable returning a `CollectionPlan`, supplied by the workflow.
        step: number shown in the heading, since each workflow reaches this step at a
            different point.
    """
    st.markdown(f"""
                ## Step {step}: Process to create .xml file for LMD
                Here we create the .xml file from your geojson.
                Please download the QC image, and plate scheme for future reference.
                """)

    tolerance, path_order = _export_parameters(step)

    if st.button("Process files"):
        logger.info("Process files button clicked")
        if st.session_state.gdf is None:
            st.warning("Please upload a GeoJSON file first.")
        elif st.session_state.saw is None:
            st.warning("Please confirm a plate layout or upload a samples-and-wells scheme first.")
        elif st.session_state.calib_array is None:
            st.warning("Please select three calibration points first.")
        else:
            _process(settings, build_plan, tolerance, path_order)

    if st.session_state.zip_buffer:
        st.download_button(
            label="Download files",
            data=st.session_state.zip_buffer.getvalue(),
            file_name=st.session_state.bundle_name or "collection.zip",
            mime="application/zip",
        )


PATH_ORDER_LABELS = {
    export.PathOrder.HILBERT: "Shortest path within each well (recommended)",
    export.PathOrder.GROUPED: "Group each well together, no path shortening",
    export.PathOrder.NONE: "As loaded — no reordering (what this app did before)",
}


def _export_parameters(step: str) -> tuple[float, export.PathOrder]:
    """Simplification tolerance and cut order. Both default to today's behaviour."""
    tolerance_column, order_column = st.columns([1, 2])

    with tolerance_column:
        tolerance = st.number_input(
            "Smoothing tolerance (pixels)",
            min_value=0.0,
            max_value=100.0,
            value=export.DEFAULT_SIMPLIFY_TOLERANCE,
            step=0.5,
            key=f"simplify_tolerance_{step}",
            help=(
                "How far an outline may move when spare points are removed from it. Higher "
                "values mean fewer points, so the stage traces the shape faster, but the cut "
                "follows your annotation less exactly. Lower values follow it more closely at "
                "the cost of a slower cut. The default of 1 pixel is what this app has always "
                "used."
            ),
        )

    with order_column:
        path_order = st.selectbox(
            "Cutting order",
            options=list(PATH_ORDER_LABELS),
            format_func=lambda mode: PATH_ORDER_LABELS[mode],
            key=f"path_order_{step}",
            help=(
                "The order shapes are written in is the order the LMD cuts them, and stage "
                "movement between shapes is a leading cause of cutting misalignment. Grouping "
                "a well's shapes together means the collector moves once per well instead of "
                "once per shape; shortening the path within each well cuts how far the stage "
                "travels between cuts. None of this changes which tissue lands in which well."
            ),
        )

    return float(tolerance), path_order


def _process(settings: dict, build_plan, tolerance: float, path_order) -> None:
    """Build the plan, render it, and stash the bundle for download."""
    plate_type = settings["plate_type"]
    plan = build_plan(settings)
    plan.params.update(
        {"simplify_tolerance_px": tolerance, "path_order": path_order.value}
    )

    _report_excluded(plan)

    try:
        result = export.build_collection(
            plan,
            samples_and_wells=st.session_state.saw,
            simplify_tolerance=tolerance,
            plate=plate_type,
            path_order=path_order,
        )
    except ValueError as error:
        st.error(str(error))
        logger.error(str(error))
        st.stop()

    st.session_state.zip_buffer = export.build_bundle(
        plan=plan,
        result=result,
        samples_and_wells=st.session_state.saw,
        plate=plate_type,
        log_path=st.session_state.log_file_path,
    )
    st.session_state.bundle_name = f"{Path(st.session_state.file_name).stem}_collection.zip"
    st.session_state.collection_image = result.image_path

    st.write(
        f"Collection: {result.n_shapes} shapes, {result.n_vertices} vertices, "
        f"{len(plan.wells_used)} wells used."
    )
    _report_path(result, plan.pixel_size_um)
    st.image(result.image_path, caption="The shapes that will be cut", width="content")
    st.success("All files have been processed and are ready for download.")
    logger.success("All files processed and zipped successfully")


def _report_excluded(plan) -> None:
    """Say what will not be cut, distinguishing a mistake from the intended outcome.

    In the cell workflow most shapes are deliberately not selected, so warning about them
    would cry wolf on every single collection. What genuinely warrants a warning is a shape
    the user asked to collect whose group ran out of wells.
    """
    unplaced = plan.unplaced
    if not unplaced.empty:
        groups = sorted(set(unplaced["group_key"]))
        st.warning(
            f"{len(unplaced)} shapes you asked to collect will **not** be cut, because their "
            f"group got no well on this plate: {', '.join(groups[:8])}"
            f"{' ...' if len(groups) > 8 else ''}. Reduce the replicates, lower the margin or "
            "spacing, or use a larger plate."
        )

    not_selected = plan.not_selected
    if not_selected.empty:
        return

    if plan.workflow == "cells":
        st.caption(
            f"{len(not_selected):,} of {len(plan.shapes):,} shapes are not part of this "
            "collection, as intended — you asked for a subset."
        )
    else:
        st.warning(
            f"{len(not_selected)} of {len(plan.shapes)} shapes have no well and will not be "
            f"cut. Their classes: {', '.join(sorted(set(not_selected[CLASS_NAME]))[:10])}"
        )


def _report_path(result, pixel_size_um: float | None) -> None:
    """Show what the cutting order costs in stage travel and collector movements."""
    def as_distance(pixels: float) -> str:
        if pixel_size_um:
            return f"{pixels * pixel_size_um / 1000:,.1f} mm"
        return f"{pixels:,.0f} px"

    line = (
        f"Cut path: **{as_distance(result.path_length_px)}** of stage travel, "
        f"**{result.collector_moves}** collector movements."
    )
    saved = result.baseline_path_length_px - result.path_length_px
    if saved > 0 or result.baseline_collector_moves > result.collector_moves:
        line += (
            f" Without reordering it would be {as_distance(result.baseline_path_length_px)} and "
            f"{result.baseline_collector_moves} movements."
        )
    st.write(line)


def extras_step() -> None:
    """Extra #1: generate QuPath classes from two categoricals."""
    st.markdown("""
                # Extras to make your life easier :D
                 - Create Qupath classes
                """)
    st.divider()
    st.markdown("""
                ## Extra #1 : Create QuPath classes from categoricals
                Creating many QuPath classes can be tedious, and is very error prone, especially for large projects.
                This tool takes in two lists of categoricals, and a number for replicates, and create a class for every permutation.

                Afterwards you must:
                1. Create a new QuPath project
                2. Close QuPath window
                3. Delete `<QuPath project>/classifiers/annotations/classes.json`
                4. Replace with newly created file
                5. Rename it as `classes.json`
                6. Reopen QuPath with project, and you should see classes
                """)

    input1 = st.text_area("Enter first categorical (comma-separated)", placeholder="example: celltype_A, celltype_B")
    input2 = st.text_area("Enter second categorical (comma-separated)", placeholder="example: control, drug_treated")
    replicates = st.number_input("Enter number of replicates", min_value=1, step=1, value=2)
    list1 = [i.strip() for i in input1.split(",") if i.strip()]
    list2 = [i.strip() for i in input2.split(",") if i.strip()]

    if st.button("Create class names for QuPath"):
        if not list1 or not list2:
            st.warning("Please enter at least one value in each categorical.")
        else:
            names = extras.generate_combinations(list1, list2, int(replicates))
            st.write(f"{len(names)} class names created.")
            st.download_button(
                "Download classes.json for QuPath",
                data=json.dumps(extras.build_classes_json(names), indent=2),
                file_name="classes.json",
                mime="application/json",
            )

    st.image(image="./assets/sample_names_example.png", caption="Example of class names for QuPath")
