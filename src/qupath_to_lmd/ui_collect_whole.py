"""Collect whole shapes: every shape of a class goes into that class's well.

The annotations route. Its output is unchanged from the frozen workflow it replaces — the golden
harness guards that byte for byte — and with several slides a class pools across them by name.
"""

import streamlit as st
from loguru import logger

from qupath_to_lmd import geojson, slides, ui_samples, ui_shared
from qupath_to_lmd.model import CLASS_NAME, SampleSet
from qupath_to_lmd.ui_slides import SlidesContext

LABEL = "Whole shapes — every shape of a class goes into that class's well"
HELP = (
    "For annotations drawn by hand: each class is one sample, cut in full. Optionally give "
    "every shape of a class its own well, for single-cell collection."
)


def _explode(context: SlidesContext, selected: list[str]) -> dict:
    """Optionally give every shape of some classes its own sample."""
    chosen = st.multiselect(
        "Give every shape of these classes its own well (optional)",
        options=selected,
        key="explode_classes",
        help=(
            "Every shape of a chosen class becomes its own sample, numbered: `T-Cell` becomes "
            "`T-Cell_001`, `T-Cell_002`, … Useful for single-cell collection. With several slides "
            "the slide name is part of the number, `T-Cell_A_001`, so cells from different slides "
            "never share a well."
        ),
    )
    frames = context.frames
    if not chosen:
        return frames
    logger.info(f"Exploding classes {chosen}")
    return {
        name: geojson.explode_classes(frame, chosen, label=name if context.several else None)
        for name, frame in frames.items()
    }


def render(context: SlidesContext) -> SampleSet | None:
    """Classes, an optional split into one well per shape, and what each sample holds."""
    selected = ui_samples.class_step(context)
    if not selected:
        return None
    frames = _explode(context, selected)

    original = "original_classification_name"
    classes = sorted(
        {
            name
            for frame in frames.values()
            for name, source in zip(
                frame[CLASS_NAME], frame[original] if original in frame.columns else frame[CLASS_NAME], strict=True
            )
            if source in selected
        }
    )
    sample_set = slides.whole_shape_samples(frames, classes, context.pixel_sizes())

    st.markdown("### What each sample holds")
    ui_shared.show_amounts(sample_set.sheet().set_index("sample"))
    st.write(f"**{len(sample_set.samples):,} samples**, {sample_set.n_collected():,} shapes to cut.")
    return sample_set
