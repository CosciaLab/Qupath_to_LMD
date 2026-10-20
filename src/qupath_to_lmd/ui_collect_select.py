"""Collect selected shapes: a set number or area of shapes per replicate, spread over the tissue.

The cell-segmentation route, and the route for annotations up to an amount — the selection does
not care what kind of shape it is choosing. With several slides each slide is selected from on
its own and the amount is split between them (`decisions.md` 075, 077).

Runs before the plate: the replicates and amounts decide how many wells are needed, so they are
settled first (`decisions.md` 068). No fragment, for the same reason as the regions route — a
fragment here would leave the plate and the cut below it showing a stale selection.
"""

import hashlib

import pandas
import streamlit as st

from qupath_to_lmd import budget, plot, selection, slides, stats, ui_samples, ui_shared, ui_slides
from qupath_to_lmd.model import CLASS_NAME, SampleSet
from qupath_to_lmd.ui_slides import SlidesContext

LABEL = "Selected shapes — a number or area of shapes per replicate, spread over the tissue"
HELP = (
    "For segmented cells, or annotations up to an amount: each class is collected into "
    "replicates, each its own well, choosing shapes spread across the tissue."
)

MINIMUM_AREA_COLUMN = "Minimum area (µm²)"


@st.cache_data(show_spinner="Choosing shapes...")
def _cached_selection(_pools, _budgets, _mode, _params, cache_key: tuple, scales: tuple, _strategy, order: tuple):
    """The selection, cached on everything that determines it (`decisions.md` 050)."""
    return slides.select_across_slides(_pools, _budgets, _mode, _params, dict(scales), _strategy, list(order))


def _budget_mode(context: SlidesContext) -> tuple[budget.BudgetMode, dict]:
    """What a budget counts, with the image scale beside it (`decisions.md` 057)."""
    labels = {
        budget.BudgetMode.CELLS: "Number of shapes per replicate",
        budget.BudgetMode.AREA: "Area of tissue per replicate (µm²)",
    }
    mode_column, scale_column = st.columns([3, 2])
    with scale_column:
        scales = ui_slides.scale_control(context)
    with mode_column:
        options = [budget.BudgetMode.CELLS]
        if all(scales.values()):
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
        if not all(scales.values()):
            st.caption(
                "Enter an image scale"
                + (" for every slide" if context.several else "")
                + " to budget by area instead."
            )
    return mode, scales


def budgets_step(context: SlidesContext, selected: list[str]):
    """Replicates and per-replicate amount for each class, with a feasibility check."""
    st.markdown("### Replicates and amounts")
    st.markdown(
        "Each replicate of each class is collected into its own well. Set how many "
        "replicates you want and how much goes into each one."
    )
    mode, scales = _budget_mode(context)
    any_scale = any(scales.values())

    # Defaults come from a DVP experiment, not from the whole class: defaulting to everything a
    # class holds meant the feasibility check could never fire (`decisions.md` 073).
    default_floor = stats.DEFAULT_MINIMUM_AREA_UM2 if any_scale else 0.0
    columns = {
        budget.DISPLAY_COLUMNS[budget.REPLICATES]: budget.DEFAULT_REPLICATES,
        budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: mode.default_per_replicate,
    }
    if any_scale:
        columns[MINIMUM_AREA_COLUMN] = default_floor
    editable = pandas.DataFrame(columns, index=pandas.Index(selected, name=CLASS_NAME))

    signature = hashlib.md5(("|".join(sorted(selected)) + mode.value).encode()).hexdigest()[:8]
    config = {
        budget.DISPLAY_COLUMNS[budget.REPLICATES]: st.column_config.NumberColumn(
            budget.DISPLAY_COLUMNS[budget.REPLICATES], min_value=1, step=1, format="%d"
        ),
        budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: st.column_config.NumberColumn(
            f"Per replicate ({mode.unit})",
            min_value=0.0,
            step=1_000.0 if mode is budget.BudgetMode.AREA else 10.0,
            format="localized",
        ),
    }
    if any_scale:
        config[MINIMUM_AREA_COLUMN] = st.column_config.NumberColumn(
            MINIMUM_AREA_COLUMN,
            min_value=0.0,
            step=10.0,
            format="localized",
            help=(
                "Shapes smaller than this are left out before anything is counted, so every "
                "figure below describes tissue you can actually collect. Different biologies "
                "differ in size, so this is per class. Zero keeps everything."
            ),
        )
    edited = st.data_editor(editable, width="stretch", key=f"budget_editor_{signature}", column_config=config)

    floors = (
        {str(name): float(row.get(MINIMUM_AREA_COLUMN) or 0.0) for name, row in edited.iterrows()}
        if any_scale
        else dict.fromkeys(selected, 0.0)
    )
    pools, excluded = {}, pandas.Series(dtype=int)
    for slide in context.slides:
        pool, dropped = stats.filter_by_minimum_area(slide.gdf, floors, scales.get(slide.name))
        pools[slide.name] = pool
        excluded = excluded.add(dropped, fill_value=0)

    budgets = [
        budget.ClassBudget(
            class_name=str(class_name),
            replicates=int(row[budget.DISPLAY_COLUMNS[budget.REPLICATES]] or 1),
            per_replicate=float(row[budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]] or 0),
        )
        for class_name, row in edited.iterrows()
    ]

    per_slide = {
        name: stats.class_statistics(pool, pixel_size_um=scales.get(name)) for name, pool in pools.items()
    }
    available = ui_samples.pooled_statistics(per_slide) if context.several else per_slide[context.names[0]]
    _report_feasibility(available, budgets, mode, excluded.astype(int) if any_scale else None, context.several)

    st.session_state.budget_mode = mode.value
    st.session_state.budgets = [vars(item) for item in budgets]
    st.session_state.minimum_area_um2 = floors
    return budgets, mode, scales, pools, floors


def _report_feasibility(table, budgets, mode, excluded, several: bool) -> None:
    """What each class is asked for against what it holds — over every slide (`decisions.md` 065)."""
    check = budget.feasibility(table, budgets, mode, excluded=excluded)
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
    if several:
        st.caption("Available is summed over every slide.")

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
            f"- **{name}**: asked for {row[budget.REQUIRED]:,.0f} {mode.unit}, "
            f"has {row[budget.AVAILABLE]:,.0f} — enough for "
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


def _selection_params(scales: dict) -> selection.SelectionParams:
    """Mode, the adjacency preference, and the seed that makes a selection reproducible."""
    mode_column, adjacency_column, distance_column, seed_column = st.columns([3, 3, 2, 1])
    with mode_column:
        mode = st.radio(
            "How to choose shapes within a class",
            options=list(selection.SelectionMode),
            format_func=lambda m: {
                selection.SelectionMode.SPREAD: "Spread out across the tissue (recommended)",
                selection.SelectionMode.RANDOM: "Random",
            }[m],
            key="selection_mode",
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
            key="avoid_adjacent",
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
            key="neighbour_distance",
            help=(
                "Shapes closer than this count as neighbours. Not zero by default: QuPath "
                "segmentation leaves a sub-pixel gap between cells that are adjacent in every "
                "sense that matters, so a strict zero would find almost none of them."
            ),
        )
    with seed_column:
        seed = st.number_input(
            "Seed", min_value=0, max_value=10_000, value=0, step=1, key="selection_seed",
            help="Same seed, same selection. Recorded in provenance.json so you can report it.",
        )

    known = [value for value in scales.values() if value]
    if len(set(known)) == 1 and neighbour_distance:
        st.caption(
            f"A neighbour distance of {neighbour_distance:g} px is "
            f"{neighbour_distance * known[0]:.2f} µm at your image scale."
        )
    return selection.SelectionParams(
        mode=mode, avoid_adjacent=avoid_adjacent, neighbour_distance_px=float(neighbour_distance), seed=int(seed)
    )


def _report(pooled: slides.PooledSelection, mode: budget.BudgetMode, several: bool) -> None:
    """Achieved against requested per replicate, and neighbours collected together."""
    results = list(pooled.per_slide.values())
    n_selected = sum(result.n_selected for result in results)
    conflicts = sum(result.n_with_collected_neighbour for result in results)

    if several:
        table = pooled.by_sample()
        st.write(f"**{n_selected:,} shapes** selected across {len(table)} replicates.")
        ui_shared.show_amounts(
            table.rename(
                columns={
                    CLASS_NAME: "Class", "replicate": "Replicate",
                    "achieved": f"Collected ({mode.unit})", "requested": f"Asked for ({mode.unit})",
                    **{name: f"{name} ({mode.unit})" for name in pooled.per_slide},
                }
            ).set_index(["Class", "Replicate"])
        )
        short = table[table["achieved"] < table["requested"] - 1e-9]
    else:
        result = results[0]
        st.write(f"**{n_selected:,} shapes** selected across {len(result.achieved)} replicates.")
        ui_shared.show_amounts(
            result.achieved.rename(
                columns={
                    CLASS_NAME: "Class", "replicate": "Replicate", "shapes": "Shapes",
                    "area_um2": "Area (µm²)", "requested": f"Asked for ({mode.unit})",
                    "achieved": f"Collected ({mode.unit})", selection.WITH_NEIGHBOUR: "With a collected neighbour",
                }
            )
        )
        short = result.shortfalls

    if not short.empty:
        lines = "\n".join(
            f"- **{row[CLASS_NAME]} replicate {int(row['replicate'])}**: got "
            f"{row['achieved']:,.0f} of {row['requested']:,.0f} {mode.unit}"
            for _, row in short.iterrows()
        )
        st.warning(
            f"{len(short)} replicate(s) could not be filled completely:\n\n{lines}\n\n"
            "They will still be collected, just with less material than you asked for."
        )
    if conflicts:
        st.warning(
            f"**{conflicts} of the {n_selected:,} collected shapes have a neighbour that is also "
            "being collected.** Those pairs share a cut boundary, so material from one may end up "
            "in the other's well. The budget was filled anyway rather than under-delivering. To "
            "reduce it, ask for fewer shapes per replicate, fewer replicates, or accept it as a "
            "limit of this tissue's density."
        )
    else:
        st.success("No collected shape has a neighbour that is also being collected.")


def _preview(context: SlidesContext, pooled: slides.PooledSelection) -> None:
    """What will be cut, coloured by class as in the classes picture, over every shape — per slide."""
    palette = ui_samples.class_palette(context)

    def draw(slide) -> None:
        replicate_of = pooled.per_slide[slide.name].replicate_of.reindex(slide.gdf.index)
        labels = slide.gdf[CLASS_NAME].where(replicate_of.notna())
        calibration_array = context.calibration[slide.name][1]
        with st.spinner("Drawing the selection..."):
            ui_shared.show_picture(
                lambda: plot.plot_shapes(
                    slide.gdf, labels=labels, calibration_array=calibration_array,
                    title=f"What will be cut on {slide.name}", colors=palette,
                ),
                ui_shared.picture_key(
                    "selection", context.fingerprint(slide.name), labels, calibration_array, palette
                ),
                width="content",
            )

    if context.several:
        for tab, slide in zip(st.tabs(context.names), context.slides, strict=True):
            with tab:
                draw(slide)
    else:
        draw(context.slides[0])
    st.caption(
        "Coloured by class, in the same colours as the classes picture above; grey is not "
        "collected. Which replicate each shape goes to is in the table above."
    )


def render(context: SlidesContext) -> SampleSet | None:
    """Classes, amounts, how to choose, and the shapes chosen."""
    selected = ui_samples.class_step(context)
    if not selected:
        return None

    budgets, mode, scales, pools, floors = budgets_step(context, selected)
    strategy, order = ui_samples.strategy_control(context, key="select")

    st.markdown("### Choose the shapes")
    params = _selection_params(scales)
    cache_key = (
        context.fingerprint(),
        tuple(sorted(floors.items())),
        tuple((item.class_name, item.replicates, item.per_replicate) for item in budgets),
        mode.value,
        params.mode.value,
        params.avoid_adjacent,
        params.neighbour_distance_px,
        params.seed,
        strategy.value,
    )
    try:
        pooled = _cached_selection(
            pools, budgets, mode, params, cache_key, tuple(sorted(scales.items())), strategy, tuple(order)
        )
    except ValueError as error:
        st.error(str(error))
        return None

    n_selected = sum(result.n_selected for result in pooled.per_slide.values())
    if n_selected == 0:
        st.warning("Nothing was selected. Check the amounts above.")
        return None

    _report(pooled, mode, context.several)
    _preview(context, pooled)

    recorded = {
        "selection_mode": params.mode.value,
        "avoid_adjacent": params.avoid_adjacent,
        "neighbour_distance_px": params.neighbour_distance_px,
        "seed": params.seed,
        "budget_mode": mode.value,
        "minimum_area_um2": floors,
        "budgets": [vars(item) for item in budgets],
    }
    if context.several:
        recorded.update({"slide_strategy": strategy.value, "slide_order": order})
    return slides.selected_samples(context.frames, pooled, budgets, mode, scales, params=recorded)
