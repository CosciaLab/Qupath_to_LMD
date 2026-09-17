"""The cell-segmentation workflow: pick classes, replicates, and how much to collect.

Phases 0–3 are in place: routing, image scale, per-class statistics, and replicates with
budgets. The selection engine itself is Phase 4; see ROADMAP.md.
"""

import hashlib

import pandas
import streamlit as st

from qupath_to_lmd import budget, plate, plot, selection, stats, ui_shared
from qupath_to_lmd.model import CLASS_NAME, plan_from_selection

MINIMUM_AREA_COLUMN = "Minimum area (µm²)"


@st.cache_data(show_spinner=False)
def _filtered_statistics(_pool, floors: tuple, pixel_size_um: float | None):
    """Statistics for the post-filter pool, cached on the floors that produced it."""
    return stats.class_statistics(_pool, pixel_size_um=pixel_size_um)


@st.cache_data(show_spinner="Choosing shapes...")
def _cached_selection(_gdf, _budgets, _mode, _params, cache_key: tuple, pixel_size_um):
    """The selection, cached on everything that determines it.

    Streamlit reruns the whole script on every widget change, and selecting from 150 000
    shapes costs 1.4 s. Without this, adjusting an unrelated control re-runs the whole
    selection (`decisions.md` 050).
    """
    return selection.select(_gdf, _budgets, _mode, _params, pixel_size_um=pixel_size_um)


def _budget_mode() -> tuple[budget.BudgetMode, float | None]:
    """Choose what a budget counts, with the image scale beside it.

    The scale sits here rather than in a step of its own because this is the only thing it
    feeds: an area budget. Users were confused about why the app asked for it at all
    (`decisions.md` 057).
    """
    labels = {
        budget.BudgetMode.CELLS: "Number of shapes per replicate",
        budget.BudgetMode.AREA: "Area of tissue per replicate (µm²)",
    }
    mode_column, scale_column = st.columns([3, 2])

    with scale_column:
        pixel_size_um = ui_shared.pixel_size_control()

    with mode_column:
        options = [budget.BudgetMode.CELLS]
        if pixel_size_um:
            options.append(budget.BudgetMode.AREA)
        mode = st.radio(
            "Budget by",
            options=options,
            format_func=lambda mode: labels[mode],
            key="budget_mode_choice",
            help=(
                "Collecting a number of shapes needs nothing else. Collecting a target area "
                "needs the image scale, since areas are measured from the shapes themselves."
            ),
        )
        if not pixel_size_um:
            st.caption("Enter an image scale to budget by area instead.")

    return mode, pixel_size_um


def budgets_step(selected: list[str], step: str = "5") -> tuple[list[budget.ClassBudget], float | None]:
    """Replicates and per-replicate amount for each class, with a feasibility check."""
    st.markdown(f"## Step {step}: Replicates and amounts")
    st.markdown(
        "Each replicate of each class is collected into its own well. Set how many "
        "replicates you want and how much goes into each one."
    )

    mode, pixel_size_um = _budget_mode()
    gdf = st.session_state.gdf

    # The minimum area applies before anything is measured, so the suggested amounts start from
    # the pool that survives the default floor — otherwise they describe shapes that will be
    # filtered out a moment later (`decisions.md` 060).
    default_floor = stats.DEFAULT_MINIMUM_AREA_UM2 if pixel_size_um else 0.0
    default_floors = dict.fromkeys(selected, default_floor)
    pool, _excluded = stats.filter_by_minimum_area(gdf, default_floors, pixel_size_um)
    table = _filtered_statistics(pool, tuple(sorted(default_floors.items())), pixel_size_um)
    supply = table.reindex(selected)[mode.stats_column].fillna(0)

    # Default to the whole class in a single replicate — the same thing the annotations
    # workflow would do — so the starting point is neutral rather than an invented number.
    columns = {
        budget.DISPLAY_COLUMNS[budget.REPLICATES]: 1,
        budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: supply.round(stats.DECIMALS),
    }
    if pixel_size_um:
        columns[MINIMUM_AREA_COLUMN] = default_floor
    editable = pandas.DataFrame(columns, index=supply.index)

    # A key tied to the selection and mode, so changing either gives a fresh editor rather
    # than leaving rows from the previous one behind.
    signature = hashlib.md5(("|".join(sorted(selected)) + mode.value).encode()).hexdigest()[:8]
    editor_config = {
        budget.DISPLAY_COLUMNS[budget.REPLICATES]: st.column_config.NumberColumn(
            budget.DISPLAY_COLUMNS[budget.REPLICATES], min_value=1, step=1, format="%d"
        ),
        budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: st.column_config.NumberColumn(
            f"Per replicate ({mode.unit})", min_value=0.0, format=f"%.{stats.DECIMALS}f"
        ),
    }
    if pixel_size_um:
        editor_config[MINIMUM_AREA_COLUMN] = st.column_config.NumberColumn(
            MINIMUM_AREA_COLUMN,
            min_value=0.0,
            step=10.0,
            format=f"%.{stats.DECIMALS}f",
            help=(
                "Shapes smaller than this are left out before anything is counted, so every "
                "figure below describes tissue you can actually collect. Different biologies "
                "differ in size, so this is per class. Zero keeps everything."
            ),
        )
    edited = st.data_editor(
        editable, width="stretch", key=f"budget_editor_{signature}", column_config=editor_config
    )

    floors = (
        {str(name): float(row.get(MINIMUM_AREA_COLUMN) or 0.0) for name, row in edited.iterrows()}
        if pixel_size_um
        else dict.fromkeys(selected, 0.0)
    )
    pool, excluded = stats.filter_by_minimum_area(gdf, floors, pixel_size_um)
    table = _filtered_statistics(pool, tuple(sorted(floors.items())), pixel_size_um)

    budgets = [
        budget.ClassBudget(
            class_name=str(class_name),
            replicates=int(row[budget.DISPLAY_COLUMNS[budget.REPLICATES]] or 1),
            per_replicate=float(row[budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]] or 0),
        )
        for class_name, row in edited.iterrows()
    ]

    _report_feasibility(table, budgets, mode, excluded if pixel_size_um else None)

    st.session_state.budget_mode = mode.value
    st.session_state.budgets = [vars(item) for item in budgets]
    st.session_state.minimum_area_um2 = floors
    return budgets, pixel_size_um, pool


def _report_feasibility(
    table: pandas.DataFrame,
    budgets: list[budget.ClassBudget],
    mode: budget.BudgetMode,
    excluded=None,
) -> None:
    """Show what each class is asked for against what it holds, and warn on shortfalls.

    What the size filter removed is two columns of this table rather than its own warning box.
    The box said the same thing at ten times the length, in the middle of the step where the
    user is typing numbers, so it interrupted the very decision it was meant to inform
    (`decisions.md` 065).
    """
    check = budget.feasibility(table, budgets, mode, excluded=excluded)
    # The unit is named in every amount column, not only in "per replicate": a column headed
    # "Available" tells the reader nothing about whether it counts shapes or µm².
    display = budget.for_display(check).rename(
        columns={
            budget.DISPLAY_COLUMNS[column]: f"{budget.DISPLAY_COLUMNS[column]} ({mode.unit})"
            for column in (budget.PER_REPLICATE, budget.REQUIRED, budget.AVAILABLE, budget.SHORTFALL)
        }
    )
    ui_shared.show_amounts(
        display,
        column_config={
            budget.DISPLAY_COLUMNS[budget.FILTERED_SHARE]: st.column_config.NumberColumn(
                budget.DISPLAY_COLUMNS[budget.FILTERED_SHARE],
                format="%d%%",
                help=(
                    "Share of this class left out for being under its minimum area. Those "
                    "shapes are gone before anything else here is counted, so every other "
                    "figure in this row describes tissue you can actually collect."
                ),
            ),
        },
    )

    if excluded is None:
        st.caption(
            "No size filter is applied, because a minimum collectable area needs the image "
            "scale. Enter one above to set one."
        )
    elif int(excluded.sum()):
        st.caption(
            f"{int(excluded.sum()):,} shapes were too small to collect and are already left "
            "out of the figures above. Change a class's minimum area if you disagree."
        )

    short = check[check[budget.SHORTFALL] > 0]
    if not short.empty:
        lines = "\n".join(
            f"- **{name}**: asked for {row[budget.REQUIRED]:,.{stats.DECIMALS}f} {mode.unit}, "
            f"has {row[budget.AVAILABLE]:,.{stats.DECIMALS}f} — enough for "
            f"{int(row[budget.ACHIEVABLE])} full replicate(s)"
            for name, row in short.iterrows()
        )
        st.warning(
            f"{len(short)} class(es) cannot supply what you asked for:\n\n{lines}\n\n"
            "You can continue — those replicates will be filled as far as the class allows — "
            "or reduce the amount or the number of replicates."
        )
    else:
        st.success("Every class can supply its budget.")

    zero = [item.class_name for item in budgets if item.per_replicate <= 0]
    if zero:
        st.warning(f"Nothing will be collected for: {', '.join(zero)} — the amount is zero.")


def capacity_step(budgets: list[budget.ClassBudget], step: str = "7") -> dict:
    """Plate settings, the well assignment they produce, and whether it fits.

    The assignment depends only on the budgets — one well per class per replicate — so the
    whole layout can be shown here, before the selection decides *which* shapes fill it.
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
    samples_and_wells = ui_shared.editable_plate(samples_and_wells, settings["plate_type"], key_suffix="cells")
    st.session_state.saw = samples_and_wells
    ui_shared.plate_preview(
        samples_and_wells, settings["plate_type"], wells=usable, key_suffix="cells"
    )

    settings["samples_and_wells"] = samples_and_wells
    return settings


def _selection_params(step: str) -> selection.SelectionParams:
    """Mode, the adjacency constraint, and the seed that makes a selection reproducible."""
    mode_column, adjacency_column, distance_column, seed_column = st.columns([3, 3, 2, 1])

    with mode_column:
        mode = st.radio(
            "How to choose shapes within a class",
            options=list(selection.SelectionMode),
            format_func=lambda m: {
                selection.SelectionMode.SPREAD: "Spread out across the tissue (recommended)",
                selection.SelectionMode.RANDOM: "Random",
            }[m],
            key=f"selection_mode_{step}",
            help=(
                "Spread lays a grid over each class and takes the shape nearest each grid "
                "square, so a replicate samples the whole class rather than one corner of it. "
                "Random draws without regard to position, which is unbiased but clumps."
            ),
        )

    with adjacency_column:
        avoid_adjacent = st.checkbox(
            "Avoid collecting neighbouring shapes",
            value=True,
            key=f"avoid_adjacent_{step}",
            help=(
                "Neighbouring cells share a cut boundary, so collecting both risks material "
                "from one ending up in the other's well. This is a strong preference, not a "
                "guarantee: in dense tissue a large budget cannot always be filled without "
                "neighbours, and under-delivering would be worse. Whatever remains is counted "
                "in the table below. Judged across the whole collection, so two replicates "
                "cannot take neighbouring cells either."
            ),
        )

    with distance_column:
        neighbour_distance = st.number_input(
            "Neighbour distance (px)",
            min_value=0.0,
            max_value=50.0,
            value=selection.DEFAULT_NEIGHBOUR_DISTANCE_PX,
            step=0.5,
            key=f"neighbour_distance_{step}",
            help=(
                "Shapes closer than this count as neighbours. Not zero by default: QuPath "
                "segmentation leaves a sub-pixel gap between cells that are adjacent in every "
                "sense that matters, so a strict zero would find almost none of them."
            ),
        )

    with seed_column:
        seed = st.number_input(
            "Seed", min_value=0, max_value=10_000, value=0, step=1, key=f"seed_{step}",
            help="Same seed, same selection. Recorded in provenance.json so you can report it.",
        )

    if st.session_state.pixel_size_um and neighbour_distance:
        st.caption(
            f"A neighbour distance of {neighbour_distance:g} px is "
            f"{neighbour_distance * st.session_state.pixel_size_um:.2f} µm at your image scale."
        )

    return selection.SelectionParams(
        mode=mode,
        avoid_adjacent=avoid_adjacent,
        neighbour_distance_px=float(neighbour_distance),
        seed=int(seed),
    )


@st.fragment
def selection_step(budgets, settings: dict, pixel_size_um: float | None, pool, step: str = "7") -> None:
    """Choose the shapes, preview them, and offer the export.

    A fragment, so changing the mode, the seed or the neighbour distance reruns only this step
    and the export below it, instead of re-executing steps 1-7 as well. Everything downstream
    of these controls lives inside the fragment, so nothing can be left showing a stale
    selection (`decisions.md` 051).
    """
    st.markdown(f"## Step {step}: Select shapes and export")
    params = _selection_params(step)

    mode = budget.BudgetMode(st.session_state.budget_mode or budget.BudgetMode.CELLS.value)
    # The pool, not the whole file: shapes below their class's minimum area were removed before
    # anything was measured, so selecting from the full frame would hand back shapes the user
    # was already told are too small to collect (`decisions.md` 060).
    gdf = pool
    cache_key = (
        ui_shared.shape_fingerprint(gdf),
        tuple(sorted((st.session_state.minimum_area_um2 or {}).items())),
        tuple((item.class_name, item.replicates, item.per_replicate) for item in budgets),
        mode.value,
        params.mode.value,
        params.avoid_adjacent,
        params.neighbour_distance_px,
        params.seed,
        pixel_size_um,
    )
    try:
        result = _cached_selection(gdf, budgets, mode, params, cache_key, pixel_size_um)
    except ValueError as error:
        st.error(str(error))
        return

    if result.n_selected == 0:
        st.warning("Nothing was selected. Check the amounts in step 6.")
        return

    _report_selection(result, mode)
    _preview_selection(result)
    _export_selection(result, settings, params, pixel_size_um)


def _report_selection(result: selection.SelectionResult, mode: budget.BudgetMode) -> None:
    """Achieved against requested, per replicate."""
    st.write(f"**{result.n_selected:,} shapes** selected across {len(result.achieved)} replicates.")
    # Renamed at render time: the frame carries the library's own column names, and
    # `area_um2` / `neighbour_also_collected` are identifiers, not something to show a user.
    ui_shared.show_amounts(
        result.achieved.rename(
            columns={
                CLASS_NAME: "Class",
                "replicate": "Replicate",
                "shapes": "Shapes",
                "area_um2": "Area (µm²)",
                "requested": f"Asked for ({mode.unit})",
                "achieved": f"Collected ({mode.unit})",
                selection.WITH_NEIGHBOUR: "With a collected neighbour",
            }
        )
    )

    short = result.shortfalls
    if not short.empty:
        lines = "\n".join(
            f"- **{row[CLASS_NAME]} replicate {int(row['replicate'])}**: got "
            f"{row['achieved']:,.{stats.DECIMALS}f} of {row['requested']:,.{stats.DECIMALS}f} {mode.unit}"
            for _, row in short.iterrows()
        )
        st.warning(
            f"{len(short)} replicate(s) could not be filled completely:\n\n{lines}\n\n"
            "They will still be collected, just with less material than you asked for."
        )
    conflicts = result.n_with_collected_neighbour
    if conflicts:
        st.warning(
            f"**{conflicts} of the {result.n_selected:,} collected shapes have a neighbour that "
            "is also being collected** — see the last column. Those pairs share a cut boundary, "
            "so material from one may end up in the other's well. The budget was filled anyway "
            "rather than under-delivering. To reduce it, ask for fewer shapes per replicate, "
            "fewer replicates, or accept it as a limit of this tissue's density."
        )
    else:
        st.success("No collected shape has a neighbour that is also being collected.")


def _preview_selection(result: selection.SelectionResult) -> None:
    """Draw what will be cut, coloured by replicate, over every shape in the file."""
    labels = result.replicate_of.map(lambda value: f"replicate {int(value)}" if pandas.notna(value) else None)
    with st.spinner("Drawing the selection..."):
        figure = plot.plot_shapes(
            st.session_state.gdf,
            labels=labels,
            calibration_array=st.session_state.calib_array,
            title="What will be cut, coloured by replicate",
        )
    st.pyplot(figure, width="content")
    st.caption(
        "Classes are merged here so you can judge whether the replicates are spread and "
        "comparable across the tissue."
    )


def _export_selection(result, settings: dict, params: selection.SelectionParams, pixel_size_um) -> None:
    """Assign wells, build the plan, and hand off to the shared export step."""
    plan, samples_and_wells = plan_from_selection(
        # The whole file, so shapes that were filtered out or not selected stay reportable.
        gdf=st.session_state.gdf,
        replicate_of=result.replicate_of.reindex(st.session_state.gdf.index),
        wells=settings["wells"],
        samples_and_wells=settings.get("samples_and_wells"),
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        pixel_size_um=pixel_size_um,
        params={
            "plate": settings["plate_type"],
            "margins": settings["margins"],
            "step_row": settings["step_row"],
            "step_col": settings["step_col"],
            "randomize_wells": settings["randomize"],
            "selection_mode": params.mode.value,
            "avoid_adjacent": params.avoid_adjacent,
            "neighbour_distance_px": params.neighbour_distance_px,
            "seed": params.seed,
            "budget_mode": st.session_state.budget_mode,
            "minimum_area_um2": st.session_state.minimum_area_um2,
            "budgets": st.session_state.budgets,
        },
    )

    st.session_state.saw = samples_and_wells

    ui_shared.export_step(settings, lambda _settings: plan, step="8")


def render(uploaded_file) -> None:
    """The cell workflow.

    The image scale is no longer a step of its own. Where the file allows it, the scale is
    estimated from QuPath's own measurements and the class table simply shows areas; the input
    to override or supply it sits beside the area budget control, which is the only thing it
    feeds (`decisions.md` 056, 057).
    """
    if st.session_state.gdf is None:
        st.info("Upload a GeoJSON to continue.")
        return

    pixel_size, _source = ui_shared.resolve_pixel_size()

    selected = ui_shared.class_selection_step(pixel_size, step="4")
    if not selected:
        return
    st.divider()

    budgets, pixel_size, pool = budgets_step(selected, step="5")
    st.divider()

    settings = capacity_step(budgets, step="6")
    st.divider()

    selection_step(budgets, settings, pixel_size, pool, step="7")
