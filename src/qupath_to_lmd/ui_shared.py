"""Helpers more than one stage uses: amounts, the image scale, the plate, the cut path, extras.

The stages themselves are `ui_slides`, `ui_samples` (with one `ui_collect_*` module per method),
`ui_plates` and `ui_cut`. This is the UI layer, so these functions may read and write
`st.session_state`; the library modules stay pure and take explicit arguments.
"""

import hashlib
import io
import json
import uuid

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


def every_class_step(
    method: str, default_replicates: int, unit: str | None, default_amount: float | None, amount_step: float = 1.0
) -> tuple[int, float | None, str]:
    """Replicates and amount per replicate, set once for every class of a per-class table.

    A lab with thirteen patients and nineteen categories has over two hundred classes, and
    setting each row by hand does not scale. The values reach the table only when Apply is
    pressed: the table keeps its key until then, so rows edited one by one survive a stray
    change here (`decisions.md` 095).

    Args:
        method: which table, so the two methods keep their own values.
        default_replicates: the table's replicates before anything is applied.
        unit: what the amount counts, `"shapes"` or `"µm²"`; None when the table has no amount.
        default_amount: the table's amount before anything is applied.
        amount_step: the amount input's step.

    Returns:
        The replicates and amount every row starts from, and a token for the table's key that
        changes only when Apply is pressed.
    """
    state_key = f"every_class_{method}"
    applied = st.session_state.get(state_key) or {}
    replicates = applied.get("replicates", default_replicates)
    # An amount counts shapes or area; one set in the other unit is not this table's amount.
    amount = applied["amount"] if unit is not None and applied.get("unit") == unit else default_amount

    with st.form(f"{state_key}_form", border=False):
        replicates_column, amount_column, button_column = st.columns([1, 1, 1], vertical_alignment="bottom")
        with replicates_column:
            typed_replicates = st.number_input("Replicates", min_value=1, value=int(replicates), step=1)
        typed_amount = None
        if unit is not None:
            with amount_column:
                typed_amount = st.number_input(
                    f"Per replicate ({unit})", min_value=0.0, value=float(amount), step=float(amount_step)
                )
        with button_column:
            pressed = st.form_submit_button("Apply to every class")
    st.caption(
        "Sets these for every class in the table below. Rows you edited one by one are "
        "overwritten in those columns; edit them again afterwards to set a class apart."
    )
    if pressed:
        applied = {
            "replicates": int(typed_replicates),
            "amount": None if typed_amount is None else float(typed_amount),
            "unit": unit,
            "token": uuid.uuid4().hex[:8],
        }
        st.session_state[state_key] = applied
        replicates = applied["replicates"]
        amount = default_amount if typed_amount is None else applied["amount"]
    return int(replicates), amount, applied.get("token", "default")


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


EXTRAS_REPLICATES_COLUMN = "Replicates"


def extras_step() -> None:
    """Extra #1: generate QuPath classes from categoricals. Its own page, Extras."""
    st.markdown("""
                ## Create QuPath classes from categoricals
                Creating many QuPath classes by hand is tedious and error prone. Here a class is made for every
                combination of your categoricals, once per replicate.
                1. Type one categorical per row, its values separated by commas: cell types in one row, treatments
                   in the next, and so on. A new empty row appears as soon as you fill the last one.
                2. Set how many replicates each combination needs. To give one combination more, fewer or none,
                   open the table under the replicates.
                3. Download `classes.json` and add it to your QuPath project, as described under the download button.
                """)
    st.image(image="./assets/sample_names_example.png", width=360)

    categoricals = _categorical_rows()
    st.session_state.extras_default = int(st.number_input(
        "Replicates per combination", min_value=1, step=1, value=st.session_state.extras_default,
        key="extras_default_input",
    ))
    if categoricals:
        _classes_from(categoricals, st.session_state.extras_default)


def _categorical_rows() -> list[tuple[str, list[str]]]:
    """One row per categorical, always one empty row waiting at the end. Returns (label, values)."""
    rows = st.session_state.extras_rows
    # Read what was just typed before deciding which rows to show, so the empty row appears on the
    # same rerun that filled the last one.
    for row in rows:
        row["name"] = st.session_state.get(f"extras_name_{row['id']}", row["name"])
        row["values"] = st.session_state.get(f"extras_values_{row['id']}", row["values"])
    rows[:] = [row for row in rows[:-1] if row["name"].strip() or row["values"].strip()] + rows[-1:]
    if not rows or rows[-1]["values"].strip():
        rows.append({"id": max((row["id"] for row in rows), default=-1) + 1, "name": "", "values": ""})

    categoricals = []
    labels = {EXTRAS_REPLICATES_COLUMN}
    for position, row in enumerate(rows, start=1):
        name_column, values_column, remove_column = st.columns([2, 5, 1], vertical_alignment="bottom")
        row["name"] = name_column.text_input(
            f"Categorical {position}", value=row["name"], key=f"extras_name_{row['id']}",
            placeholder="name, optional — e.g. cell type",
        )
        row["values"] = values_column.text_input(
            "Values, comma-separated", value=row["values"], key=f"extras_values_{row['id']}",
            placeholder="e.g. tumour, stroma, immune" if position == 1 else "another categorical, e.g. control, drug",
        )
        if row is not rows[-1]:
            remove_column.button(
                "✕", key=f"extras_remove_{row['id']}", on_click=_remove_row, args=(row["id"],),
                help="Remove this categorical",
            )
        values, repeated = extras.parse_values(row["values"])
        if repeated:
            st.warning(
                f"Categorical {position} has {', '.join(f'`{v}`' for v in repeated)} more than once. "
                "Each value is used once, so those classes are not made twice."
            )
        if values:
            label = row["name"].strip() or f"Categorical {position}"
            if label in labels:
                label = f"{label} ({position})"
            labels.add(label)
            categoricals.append((label, values))
    return categoricals


def _remove_row(row_id: int) -> None:
    st.session_state.extras_rows = [row for row in st.session_state.extras_rows if row["id"] != row_id]
    for widget in ("name", "values"):
        st.session_state.pop(f"extras_{widget}_{row_id}", None)


def _classes_from(categoricals: list[tuple[str, list[str]]], default: int) -> None:
    """The counts, the optional per-combination replicates, the names and the download."""
    labels = [label for label, _ in categoricals]
    combos = extras.combinations([values for _, values in categoricals])
    replicates = _replicate_exceptions(labels, combos, default)
    names = extras.class_names(combos, replicates, default)

    line = (
        f"{len(combos)} values" if len(categoricals) == 1
        else f"{' × '.join(str(len(values)) for _, values in categoricals)} = {len(combos)} combinations"
    )
    replicate_word = "replicate" if default == 1 else "replicates"
    if replicates:
        left_out = sum(1 for count in replicates.values() if count == 0)
        line += (
            f", {default} {replicate_word} each except {len(replicates)} changed"
            f"{f' ({left_out} left out)' if left_out else ''} = **{len(names)} classes**"
        )
    else:
        line += f" × {default} {replicate_word} = **{len(names)} classes**"
    st.markdown(line)

    repeated = extras.repeated_names(names)
    if repeated:
        st.warning(
            f"{len(repeated)} class name(s) come out the same from different combinations, "
            f"e.g. `{repeated[0]}`. QuPath keeps one class per name, so those shapes could not be told "
            "apart later. Rename a value so no two combinations join to the same text."
        )
    if len(names) > extras.MANY_CLASSES:
        st.warning(
            f"{len(names)} classes is a long list to pick from in QuPath. That works, but check the "
            "categoricals and replicates are what you meant before importing."
        )
    if not names:
        st.warning("Every combination is set to 0 replicates, so there are no classes to download.")
        return

    st.download_button(
        "Download classes.json for QuPath",
        data=json.dumps(extras.build_classes_json(names), indent=2),
        file_name="classes.json",
        mime="application/json",
    )
    st.markdown("""
                **Add the classes to QuPath**
                1. Create a new QuPath project, then close QuPath.
                2. In the project folder, open `classifiers/annotations/` and delete `classes.json`.
                3. Put the downloaded file there. If your browser saved it as `classes (1).json` or similar,
                   rename it to `classes.json`.
                4. Reopen the project in QuPath; the classes are listed in the *Annotations* tab.
                """)
    st.dataframe(pandas.DataFrame({"Class": names}), hide_index=True, height=250)


def _replicate_exceptions(labels: list[str], combos: list[tuple[str, ...]], default: int) -> dict:
    """Combinations whose replicate count differs from the default, edited in a collapsed table."""
    exceptions = st.session_state.extras_replicates
    # The editor keeps its edits relative to the table it was given, so that table must not change
    # under it. A new one is started only when the combinations or the default change, seeded with
    # every exception so far, so adding a value does not forget the counts already typed.
    basis = hashlib.md5(repr((combos, default)).encode()).hexdigest()[:8]
    if st.session_state.extras_editor is None or st.session_state.extras_editor[0] != basis:
        st.session_state.extras_editor = (basis, dict(exceptions))
    seed = st.session_state.extras_editor[1]

    table = pandas.DataFrame(combos, columns=labels)
    table[EXTRAS_REPLICATES_COLUMN] = [seed.get(combo, default) for combo in combos]
    with st.expander("Different replicates for some combinations? (optional)"):
        st.caption("Change a number to give that combination more or fewer replicates. 0 leaves it out.")
        edited = st.data_editor(
            table,
            key=f"extras_editor_{basis}",
            hide_index=True,
            disabled=labels,
            width="stretch",
            column_config={
                EXTRAS_REPLICATES_COLUMN: st.column_config.NumberColumn(
                    EXTRAS_REPLICATES_COLUMN, min_value=0, step=1, format="%d",
                ),
            },
        )
    for combo, count in zip(combos, edited[EXTRAS_REPLICATES_COLUMN], strict=True):
        count = default if pandas.isna(count) else int(count)
        if count == default:
            exceptions.pop(combo, None)
        else:
            exceptions[combo] = count
    return {combo: exceptions[combo] for combo in combos if combo in exceptions}
