"""Helpers more than one stage uses: amounts, the image scale, the plate, the cut path, extras.

The stages themselves are `ui_slides`, `ui_samples` (with one `ui_collect_*` module per method),
`ui_plates` and `ui_cut`. This is the UI layer, so these functions may read and write
`st.session_state`; the library modules stay pure and take explicit arguments.
"""

import hashlib
import io
import json

import geopandas
import numpy
import pandas
import shapely
import streamlit as st

from qupath_to_lmd import export, extras, plate, qc, stats

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

# Slides whose scales differ by more than this are flagged: right for different magnifications,
# a mistake otherwise.
SCALE_DISAGREEMENT = 0.05

PIXEL_SIZE_HELP = (
    "How many micrometres one image pixel covers. It is needed only to express amounts "
    "as areas — collecting a number of shapes does not need it at all.\n\n"
    "Where to find it: QuPath, *Image → Image properties → Pixel width*.\n\n"
    "If your file carries QuPath measurements, this is filled in from them and you can "
    "leave it alone. Magnification does **not** determine pixel size — it is your "
    "camera's sensor pitch divided by the total magnification, so the same 20× objective "
    f"spans roughly {stats.SENSOR_PITCHES_UM[0] / 20:.2f}–{stats.SENSOR_PITCHES_UM[-1] / 20:.2f} "
    "µm/px across common cameras."
)

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


def report_scale(n_shapes: int) -> None:
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


def report_pixel_size(value, source, estimate, report) -> None:
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


# Every widget change reruns the whole script, and redrawing each slide's pictures was nearly all
# of a rerun's cost: 2.4 s of 2.5 s with demo1's six slides. A picture is drawn once per thing it
# shows and kept as a PNG (`decisions.md` 085).
#
# Streamlit shows a picture at most 1460 px wide, and decodes, shrinks and re-encodes anything wider
# on every rerun, cached or not. So pictures are rendered to fit, at up to the 200 dpi `st.pyplot`
# used; the PNG then reaches the browser untouched.
PICTURE_MAX_WIDTH_PX = 1460
PICTURE_MAX_DPI = 200
PICTURE_PAD_INCHES = 0.1


def picture_key(*parts) -> str:
    """A hash of everything a picture shows, so it is redrawn exactly when one of them changes.

    Tables and geometries are hashed by value. Pass a slide by its fingerprint, never its frame:
    hashing every shape on every rerun costs what the cache saves (`decisions.md` 050).
    """
    digest = hashlib.blake2b(digest_size=16)
    for part in parts:
        if isinstance(part, geopandas.GeoSeries):
            digest.update(b"\0".join(wkb or b"" for wkb in shapely.to_wkb(numpy.asarray(part.values))))
        elif isinstance(part, pandas.Series):
            digest.update(pandas.util.hash_pandas_object(part, index=True).values.tobytes())
        elif isinstance(part, numpy.ndarray):
            digest.update(repr((part.dtype.str, part.shape)).encode() + part.tobytes())
        else:
            digest.update(repr(part).encode())
        digest.update(b"|")
    return digest.hexdigest()


def picture_png(figure) -> bytes:
    """A figure as PNG, no wider than Streamlit shows it."""
    width_inches = figure.get_figwidth() + 2 * PICTURE_PAD_INCHES
    dpi = min(PICTURE_MAX_DPI, int(PICTURE_MAX_WIDTH_PX / width_inches))
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight", pad_inches=PICTURE_PAD_INCHES)
    return buffer.getvalue()


@st.cache_data(show_spinner=False, max_entries=64)
def _cached_picture(_draw, key: str) -> bytes:
    return picture_png(_draw())


def show_picture(draw, key: str, width="stretch") -> None:
    """Show the figure `draw` makes, drawing it only when `key` (from `picture_key`) is new."""
    st.image(_cached_picture(draw, key), width=width)


# Amounts in this app run from tens to millions of µm², and no decimal in them is meaningful:
# a tenth of a square micrometre is far below anything the laser can place. So every table of
# amounts shows whole numbers with a thousands separator (`decisions.md` 068, 072).
WHOLE_NUMBER = st.column_config.NumberColumn(format="localized")


def show_amounts(table, **kwargs) -> None:
    """Show a table of amounts: whole numbers, thousands separated.

    Only the numeric columns get a number format. Handing a `NumberColumn` to a text column —
    a class name — makes Streamlit mark every cell with "this value cannot be interpreted as a
    number", which reads as an error in a table that is perfectly fine.
    """
    if table is None or len(table) == 0:
        return
    rounded = table.copy()
    for column in rounded.columns:
        if pandas.api.types.is_numeric_dtype(rounded[column]):
            rounded[column] = rounded[column].round(0).astype("Int64")
    config = {
        name: WHOLE_NUMBER
        for name in rounded.columns
        if pandas.api.types.is_numeric_dtype(rounded[name])
    }
    config.update(kwargs.pop("column_config", {}) or {})
    st.dataframe(rounded, width="stretch", column_config=config, **kwargs)


def plate_preview(
    samples_and_wells: dict[str, str],
    plate_type: str,
    wells: list[str] | None = None,
    key_suffix: str = "",
    plate_name: str | None = None,
    slot_for_all: bool = False,
):
    """Show the collector with each sample in its position, and offer the scheme as a download.

    The single collector renderer for every method, so what a user sees does not depend on which
    one they picked (`decisions.md` 045). With `slot_for_all`, returns an empty slot beside the
    download, for the caller to fill with every collector's scheme once all are drawn.
    """
    chosen = plate.collector(plate_type)
    if not samples_and_wells:
        st.warning(f"No {chosen.position}s assigned yet.")
        return

    layout = plate.placement_dataframe(samples_and_wells, plate=plate_type)
    st.dataframe(layout.style.map(plate.highlight(set(samples_and_wells))), width="stretch")

    taken = set(samples_and_wells.values())
    used = sorted(taken, key=lambda well: (plate.split_position(well)[0], plate.split_position(well)[1] or 0))
    where = plate_name or f"a {chosen.label}"
    if wells and not chosen.spacing:
        caption = f"{len(samples_and_wells)} of {len(wells)} {chosen.position}s in use on {where}"
    else:
        caption = f"{len(samples_and_wells)} {chosen.position}s in use on {where}"
    if used:
        caption += f", {used[0]} to {used[-1]}"
        if wells:
            # Against the wells, not the group names — comparing with the dict's keys meant
            # nothing ever matched and the "start at" always named the first usable well.
            remaining = [well for well in wells if well not in taken]
            if remaining:
                caption += f". For another slide into this {chosen.noun}, start at **{remaining[0]}**"
            else:
                caption += f". This {chosen.noun} is now full"
    st.caption(caption + ".")

    if wells and chosen.spacing:
        with st.expander(f"Which wells the current margin and spacing leave usable ({len(wells)})"):
            usable = plate.default_layout(plate=plate_type)
            st.dataframe(usable.style.map(plate.highlight(set(wells))), width="stretch")

    this_plate, all_plates = st.columns(2)
    with this_plate:
        st.download_button(
            label=f"Download samples and wells setup for current {chosen.noun}",
            data=json.dumps(samples_and_wells, indent=4),
            file_name=f"samples_and_wells_{plate_name}.json" if plate_name else "samples_and_wells.json",
            mime="application/json",
            key=f"saw_download_{plate_type}_{len(samples_and_wells)}_{key_suffix}",
        )
    return all_plates.empty() if slot_for_all else None


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
    chosen = plate.collector(plate_type)
    if not st.checkbox(
        f"Move samples between {chosen.position}s by hand",
        value=False,
        key=f"edit_plate_{key_suffix}",
        help=(
            f"Opens the {chosen.noun} as an editable table. Pick a sample from any "
            f"{chosen.position}'s dropdown to move it there, or clear a {chosen.position} to leave "
            "it empty. The automatic layout is used unless you change something."
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

    by_well = plate.layout_to_saw(edited, plate=plate_type)
    duplicated = [name for name in options if list(by_well.values()).count(by_well.get(name, "")) > 1]
    placed = set(by_well)
    missing = [name for name in options if name not in placed]

    if missing:
        st.error(
            f"{len(missing)} sample(s) are no longer on the {chosen.noun} and will not be cut: "
            f"{', '.join(missing[:8])}. Put them back in a {chosen.position}, or untick the box "
            "above to return to the automatic layout."
        )
    if duplicated:
        st.warning(
            f"More than one sample shares a {chosen.position}: {', '.join(sorted(set(duplicated))[:8])}."
        )
    if not missing and not duplicated:
        st.success(f"Using your layout: {len(by_well)} samples placed by hand.")

    return by_well


PATH_ORDER_LABELS = {
    export.PathOrder.HILBERT: "Shortest path within each well (recommended)",
    export.PathOrder.GROUPED: "Group each well together, no path shortening",
    export.PathOrder.NONE: "As loaded — no reordering (what this app did before)",
}


def export_parameters() -> tuple[float, export.PathOrder]:
    """Simplification tolerance and cut order. Both default to today's behaviour."""
    tolerance_column, order_column = st.columns([1, 2])

    with tolerance_column:
        tolerance = st.number_input(
            "Smoothing tolerance (px)",
            min_value=0.0,
            max_value=100.0,
            value=export.DEFAULT_SIMPLIFY_TOLERANCE,
            step=0.5,
            key="simplify_tolerance",
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
            key="path_order",
            help=(
                "The order shapes are written in is the order the LMD cuts them, and stage "
                "movement between shapes is a leading cause of cutting misalignment. Grouping "
                "a well's shapes together means the collector moves once per well instead of "
                "once per shape; shortening the path within each well cuts how far the stage "
                "travels between cuts. None of this changes which tissue lands in which well."
            ),
        )

    return float(tolerance), path_order


def report_path(result, pixel_size_um: float | None, label: str | None = None) -> None:
    """Show what the cutting order costs in stage travel and collector movements."""
    def as_distance(pixels: float) -> str:
        if pixel_size_um:
            return f"{pixels * pixel_size_um / 1000:,.1f} mm"
        return f"{pixels:,.0f} px"

    line = (
        (f"{label}: " if label else "")
        + f"Cut path: **{as_distance(result.path_length_px)}** of stage travel, "
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
    """Extra #1: generate QuPath classes from two categoricals. Its own page, Extras."""
    st.markdown("""
                ## Create QuPath classes from categoricals
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
