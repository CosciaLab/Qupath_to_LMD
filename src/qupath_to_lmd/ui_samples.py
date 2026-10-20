"""Stage 2, Samples: choose how to collect, then let that method decide the samples.

Every method is a module with `LABEL`, `HELP` and `render(context) -> SampleSet | None`. The
plate and cut stages only ever see the `SampleSet`, so a new way of choosing tissue is a new
module registered in `methods()` and nothing else.

Also holds what more than one method needs: the class table across slides, and the slide
strategy control.
"""

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import plot, slides, stats, ui_shared
from qupath_to_lmd.model import CLASS_NAME, SampleSet
from qupath_to_lmd.ui_slides import SlidesContext, resolve_pixel_size

POOLING_NOTE = (
    "Shapes of the same class from different slides are collected into the same well. Use this "
    "when your slides are sections of the same sample, classified with the same classes. To keep "
    "slides apart, give their classes different names in QuPath (e.g. `P1_Tumor`, `P2_Tumor`)."
)


def methods() -> dict:
    """The collection methods, keyed by the workflow name recorded in provenance."""
    from qupath_to_lmd import ui_collect_regions, ui_collect_select, ui_collect_whole

    return {"legacy": ui_collect_whole, "cells": ui_collect_select, "regions": ui_collect_regions}


def class_palette(context: SlidesContext) -> dict[str, str]:
    """One colour per class for the whole experiment, so every picture agrees (`decisions.md` 080)."""
    classes = set()
    for frame in context.frames.values():
        classes |= set(frame[CLASS_NAME].dropna())
    return plot.class_colors(sorted(classes))


def _suggest(context: SlidesContext) -> str:
    """Selected shapes when the files are mostly cells, whole shapes otherwise."""
    counts: dict[str, int] = {}
    for frame in context.frames.values():
        if "objectType" in frame.columns:
            for kind, count in frame["objectType"].value_counts().items():
                counts[kind] = counts.get(kind, 0) + int(count)
    n_cells = counts.get("cell", 0) + counts.get("detection", 0)
    return "cells" if n_cells > counts.get("annotation", 0) else "legacy"


def method_step(context: SlidesContext) -> str:
    """Pick a method. Suggested from what the files contain; the user decides."""
    registry = methods()
    suggestion = _suggest(context)
    options = list(registry)
    chosen = st.radio(
        "How to collect",
        options=options,
        index=options.index(suggestion),
        format_func=lambda key: registry[key].LABEL,
        key="workflow_choice",
        help="\n\n".join(f"**{module.LABEL}** — {module.HELP}" for module in registry.values()),
    )
    st.caption(f"Suggested from what your {'files hold' if context.several else 'file holds'}: "
               f"{registry[suggestion].LABEL.split(' —')[0].lower()}.")
    if st.session_state.workflow != chosen:
        logger.info(f"Collection method set to {chosen}")
        st.session_state.workflow = chosen
    return chosen


@st.cache_data(show_spinner=False)
def _statistics(_gdf, cache_key: tuple, pixel_size_um: float | None):
    return stats.class_statistics(_gdf, pixel_size_um=pixel_size_um)


def class_statistics(context: SlidesContext, scales: dict[str, float | None]) -> dict[str, pandas.DataFrame]:
    """Per slide, what each class holds. Cached per slide and scale."""
    return {
        slide.name: _statistics(slide.gdf, context.fingerprint(slide.name), scales.get(slide.name))
        for slide in context.slides
    }


def pooled_statistics(per_slide: dict[str, pandas.DataFrame]) -> pandas.DataFrame:
    """Shape counts and µm² summed over slides, for checks that ask what the experiment holds."""
    columns = [c for c in ("shapes", "area_total_um2") if all(c in t.columns for t in per_slide.values())]
    frames = [table[columns] for table in per_slide.values() if not table.empty]
    if not frames:
        return pandas.DataFrame(columns=columns)
    return pandas.concat(frames).groupby(level=0).sum()


def class_step(context: SlidesContext) -> list[str]:
    """What each class holds and what it looks like, then the classes to collect.

    The table is full width with the picture below it (`decisions.md` 079). With several slides
    it gains a column per slide, so a class missing from a slide is visible at once.
    """
    st.markdown("### Classes")
    scales = context.pixel_sizes()
    per_slide = class_statistics(context, scales)

    # Full width, then the picture below: beside a picture the table was a third of the page and
    # had to be scrolled sideways, worst of all with a column per slide (`decisions.md` 079).
    if context.several:
        ui_shared.show_amounts(_class_table_across(per_slide))
    else:
        _scale_sentence(context.slides[0], scales[context.names[0]])
        ui_shared.show_amounts(stats.for_display(next(iter(per_slide.values()))))

    all_classes = sorted(set().union(*(set(t.index) for t in per_slide.values())))
    remembered = [name for name in (st.session_state.selected_classes or []) if name in all_classes]
    selected = st.multiselect(
        "Classes to collect",
        options=all_classes,
        default=remembered or all_classes,
        help="Everything after this step works only on the classes you keep here.",
    )
    if selected != st.session_state.selected_classes:
        st.session_state.selected_classes = selected
        logger.info(f"Classes selected: {selected}")

    _summarise_selection(per_slide, selected)
    if context.several:
        _name_partial_classes(per_slide, selected)

    palette = class_palette(context)
    picture, _margin = st.columns([2, 1])
    with picture:
        if context.several:
            tabs = st.tabs(context.names)
            for tab, slide in zip(tabs, context.slides, strict=True):
                with tab:
                    _draw_input(slide, context.calibration[slide.name][1], selected, palette, context.fingerprint(slide.name))
        else:
            slide = context.slides[0]
            _draw_input(slide, context.calibration[slide.name][1], selected, palette, context.fingerprint(slide.name))
    return selected


def _scale_sentence(slide: slides.Slide, pixel_size_um: float | None) -> None:
    _value, source = resolve_pixel_size(slide)
    if pixel_size_um and source == "estimated":
        st.markdown(
            f"Areas are measured from the shapes at **{pixel_size_um:.4f} µm/px**, estimated "
            "from this file's own QuPath measurements. You can change the scale later."
        )
    elif pixel_size_um:
        st.markdown(f"Areas are measured from the shapes at **{pixel_size_um:.4f} µm/px**, the scale you entered.")
    else:
        st.markdown(
            "This file carries no measurements to estimate an image scale from, so amounts "
            "are in numbers of shapes. Enter a scale later to work in areas."
        )


def _class_table_across(per_slide: dict[str, pandas.DataFrame]) -> pandas.DataFrame:
    """Class × slide: shapes, and µm² where the slide has a scale, then the totals."""
    columns = {}
    for name, table in per_slide.items():
        columns[f"{name} shapes"] = table["shapes"]
        if "area_total_um2" in table.columns:
            columns[f"{name} µm²"] = table["area_total_um2"]
    frame = pandas.DataFrame(columns).fillna(0)
    frame["Shapes"] = sum(table["shapes"].reindex(frame.index).fillna(0) for table in per_slide.values())
    if all("area_total_um2" in table.columns for table in per_slide.values()):
        frame["µm²"] = sum(table["area_total_um2"].reindex(frame.index).fillna(0) for table in per_slide.values())
    frame.index.name = "Class"
    return frame


def _summarise_selection(per_slide: dict[str, pandas.DataFrame], selected: list[str]) -> None:
    if not selected:
        st.warning("No classes selected, so there is nothing to collect yet.")
        return
    pooled = pooled_statistics(per_slide)
    kept = pooled.reindex(selected).fillna(0)
    summary = f"**{int(kept['shapes'].sum()):,} shapes** across {len(selected)} classes"
    if "area_total_um2" in kept.columns:
        summary += f", totalling **{kept['area_total_um2'].sum():,.0f} µm²** of tissue"
    st.write(summary + ".")


def _name_partial_classes(per_slide: dict[str, pandas.DataFrame], selected: list[str]) -> None:
    """A class on only some slides is usually a spelling difference, not biology."""
    partial = {
        name: [slide for slide, table in per_slide.items() if name not in table.index]
        for name in selected
    }
    partial = {name: missing for name, missing in partial.items() if missing}
    if partial:
        lines = "; ".join(f"**{name}** is not on {', '.join(missing)}" for name, missing in partial.items())
        st.caption(
            f"{lines}. Those samples are filled from the other slides only. If a class is spelled "
            "differently on one slide, rename it in QuPath so the two pool."
        )


def _draw_input(slide: slides.Slide, calibration_array, selected: list[str], palette: dict, fingerprint: tuple) -> None:
    """Everything on a slide, coloured where kept and grey where left out."""
    gdf = slide.gdf
    with st.spinner("Drawing shapes..."):
        ui_shared.show_picture(
            lambda: plot.plot_shapes(
                gdf, included=selected, calibration_array=calibration_array,
                title=f"{len(gdf):,} shapes on {slide.name}", colors=palette,
            ),
            ui_shared.picture_key("classes", fingerprint, selected, calibration_array, palette),
        )
    if len(gdf) > plot.SHAPE_LIMIT:
        st.caption(
            f"Over {plot.SHAPE_LIMIT:,} shapes, so each one is drawn as a dot rather than its "
            "outline. The outlines are still what gets cut."
        )
    st.caption(
        "Greyed-out shapes are the classes you left out. Dashed triangle and crosses are the "
        "calibration points; shapes far outside it are the ones at risk of distortion."
    )


STRATEGY_LABELS = {
    slides.SlideStrategy.PROPORTIONAL: "In proportion to what each slide holds (recommended)",
    slides.SlideStrategy.EQUAL: "Equal share from every slide",
    slides.SlideStrategy.PRIORITY: "Use slides in order — the next only for what the first lacks",
}


def strategy_control(context: SlidesContext, key: str) -> tuple[slides.SlideStrategy, list[str]]:
    """How each amount is split between slides. Shown only with more than one slide."""
    if not context.several:
        return slides.SlideStrategy.PROPORTIONAL, context.names

    strategy = st.radio(
        "Where each sample's amount comes from",
        options=list(STRATEGY_LABELS),
        format_func=lambda option: STRATEGY_LABELS[option],
        key=f"slide_strategy_{key}",
        help=(
            "Every sample draws on the slides the same way. Proportional takes most from the slide "
            "with most of the class; equal share asks every slide for the same, and the others "
            "cover a slide that runs out; in order uses the first slide fully before the next. In "
            "every case a slide is never asked for more than it holds while another still has tissue."
        ),
    )
    order = context.names
    if strategy is slides.SlideStrategy.PRIORITY:
        edited = st.data_editor(
            pandas.DataFrame({"Order": range(1, len(order) + 1)}, index=pandas.Index(order, name="Slide")),
            key=f"slide_order_{key}_{len(order)}",
            column_config={"Order": st.column_config.NumberColumn(min_value=1, step=1, format="%d")},
        )
        order = list(edited.sort_values("Order", kind="stable").index)
    st.session_state.slide_strategy = strategy.value
    st.session_state.slide_order = order
    return strategy, order


def render(context: SlidesContext) -> SampleSet | None:
    """Stage 2. Returns what the chosen method decided, or None while it cannot decide yet."""
    st.markdown("## 2 · Samples")
    if context.several:
        st.info(POOLING_NOTE)
    workflow = method_step(context)
    return methods()[workflow].render(context)
