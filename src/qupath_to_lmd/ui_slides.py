"""Stage 1, Slides: upload, what reading found, calibration and image scale — per slide.

Every collection method starts from what this stage returns, a `SlidesContext`. A new input
format is a change here and nowhere else.
"""

import io
from dataclasses import dataclass

import numpy
import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import geojson, qc, slides, ui_shared
from qupath_to_lmd.model import CLASS_NAME

# Keys holding anything derived from the uploaded slides. A new upload clears them, so a choice
# made for one experiment never leaks into the next.
DERIVED_KEYS = (
    "selected_classes", "budgets", "budget_mode", "minimum_area_um2", "region_params",
    "packing_params", "region_budgets", "zip_buffer", "bundle_name", "bundle_signature",
    "collection_image",
)


@dataclass
class SlidesContext:
    """The slides, each with its calibration, as every later stage sees them."""

    slides: list[slides.Slide]
    calibration: dict[str, tuple[list[str], numpy.ndarray]]

    @property
    def names(self) -> list[str]:
        """Slide names, in upload order."""
        return [slide.name for slide in self.slides]

    @property
    def frames(self) -> dict:
        """Each slide's shapes."""
        return {slide.name: slide.gdf for slide in self.slides}

    @property
    def several(self) -> bool:
        """More than one slide — the condition every slide-only control appears under."""
        return len(self.slides) > 1

    def slide(self, name: str) -> slides.Slide:
        """One slide by name."""
        return next(slide for slide in self.slides if slide.name == name)

    def pixel_sizes(self) -> dict[str, float | None]:
        """Each slide's scale in force: what the user typed, else what the file implies."""
        return {slide.name: resolve_pixel_size(slide)[0] for slide in self.slides}

    def fingerprint(self, name: str | None = None) -> tuple:
        """A cheap identity for cache keys: never the frames themselves (`decisions.md` 051)."""
        chosen = [self.slide(name)] if name else self.slides
        return tuple(
            (
                slide.name, slide.source_file, slide.content_digest, len(slide.gdf),
                tuple(sorted(slide.gdf[CLASS_NAME].dropna().unique())),
            )
            for slide in chosen
        )


def resolve_pixel_size(slide: slides.Slide) -> tuple[float | None, str]:
    """A slide's scale and where it came from: `entered`, `estimated` or `none`.

    Typed wins; otherwise the scale QuPath's own area measurements imply. None is normal — most
    exports carry no measurements, and counting shapes never needs a scale (`decisions.md` 038,
    056).
    """
    typed = (st.session_state.get("pixel_size_by_slide") or {}).get(slide.name)
    if typed:
        return float(typed), "entered"
    if slide.report.implied_pixel_size_um:
        return float(slide.report.implied_pixel_size_um), "estimated"
    return None, "none"


def _upload_key(files) -> tuple:
    return tuple((file.name, file.size) for file in files)


def _read(files) -> list[slides.Slide]:
    sources = []
    for file in files:
        buffer = io.BytesIO(file.getvalue())
        buffer.name = file.name
        sources.append(buffer)
    return slides.read_slides(sources)


def _forget_derived_state() -> None:
    for key in DERIVED_KEYS:
        st.session_state[key] = None
    st.session_state.pixel_size_by_slide = {}
    st.session_state.calibration = {}


def upload_step() -> list[slides.Slide] | None:
    """Upload one or more exports, or a zip of them, and show what reading each one found."""
    st.markdown("### Upload")
    st.markdown(
        "Upload the `.geojson` QuPath exported for each slide — one file, several, or a `.zip` "
        "of a folder of them. Each file is one slide, with its own calibration points."
    )
    files = st.file_uploader(
        "QuPath exports", type=["geojson", "zip"], accept_multiple_files=True, key="slide_uploader"
    )
    if not files:
        if st.session_state.slides is not None:
            st.session_state.slides = None
            st.session_state.upload_key = None
            _forget_derived_state()
        return None

    key = _upload_key(files)
    if st.session_state.upload_key != key or st.session_state.slides is None:
        logger.info(f"New upload: {[name for name, _ in key]}")
        try:
            with st.spinner("Reading and checking your QuPath exports..."):
                read = _read(files)
        except (geojson.GeojsonError, slides.SlideError) as error:
            st.error(str(error))
            logger.error(str(error))
            st.stop()
        st.session_state.slides = read
        st.session_state.upload_key = key
        _forget_derived_state()

    read = st.session_state.slides
    if len(read) == 1:
        _describe_one(read[0])
    else:
        _describe_several(read)
    ui_shared.report_scale(sum(slide.report.n_shapes_kept for slide in read))
    return read


def _describe_one(slide: slides.Slide) -> None:
    """One slide: the reading table as it has always looked (`decisions.md` 064)."""
    report = slide.report
    described = f"**{report.n_shapes_in_file:,} shapes**"
    if report.calibration_point_names:
        described += f" and {len(report.calibration_point_names)} named calibration points"
    st.write(f"This file holds {described}.")
    st.dataframe(report.summary(), width="stretch")
    _unnamed_points(slide, several=False)
    st.success(f"File check complete, {report.n_shapes_kept:,} shapes are ready to collect.")


def _describe_several(read: list[slides.Slide]) -> None:
    """Several slides: one row each, the full reading table one click away."""
    overview = pandas.DataFrame(
        [
            {
                "Slide": slide.name,
                "File": slide.source_file,
                "Shapes in file": slide.report.n_shapes_in_file,
                "Ready to collect": slide.report.n_shapes_kept,
                "Calibration points": len(slide.calibration_points),
                "Image scale (µm/px)": slide.report.implied_pixel_size_um,
            }
            for slide in read
        ]
    ).set_index("Slide")
    st.dataframe(
        overview,
        width="stretch",
        column_config={
            "Shapes in file": ui_shared.WHOLE_NUMBER,
            "Ready to collect": ui_shared.WHOLE_NUMBER,
            "Image scale (µm/px)": st.column_config.NumberColumn(format="%.4f"),
        },
    )
    with st.expander("What reading each slide found"):
        for slide in read:
            st.markdown(f"**{slide.name}**")
            st.dataframe(slide.report.summary(), width="stretch")
    for slide in read:
        _unnamed_points(slide, several=True)
    total = sum(slide.report.n_shapes_kept for slide in read)
    st.success(f"{len(read)} slides read, {total:,} shapes ready to collect.")


def _unnamed_points(slide: slides.Slide, several: bool) -> None:
    if slide.report.n_unnamed_points:
        where = f" on slide **{slide.name}**" if several else " in this file"
        st.warning(
            f"{slide.report.n_unnamed_points} point(s){where} have no name, so they cannot be "
            "chosen as calibration points. Name each point annotation in QuPath's annotation "
            "list and export again."
        )


def calibration_step(read: list[slides.Slide]) -> dict[str, tuple[list[str], numpy.ndarray]]:
    """Three calibration points per slide, and how much of each slide they cover.

    The two hard stops of `decisions.md` 031 apply to every slide: without three points, or with
    three that make no triangle, no file for that slide can cut the right place.
    """
    st.markdown("### Calibration points")
    calibration = {}
    if len(read) == 1:
        calibration[read[0].name] = _calibrate(read[0], suggestion=None, several=False)
        return calibration

    st.caption(
        "Each slide is mounted and calibrated on its own, so each has its own three points. "
        "Where a slide has points with the same names as the first, they are suggested — check "
        "and confirm every slide."
    )
    # The ticks sit above the tabs rather than in their labels: Streamlit tabs take no key, so
    # relabelling one can send the user back to the first tab after every confirmation.
    st.markdown("   ·   ".join(f"{'✅' if is_confirmed(slide) else '⬜'} {slide.name}" for slide in read))
    tabs = st.tabs([slide.name for slide in read])
    suggestion = None
    for tab, slide in zip(tabs, read, strict=True):
        with tab:
            calibration[slide.name] = _calibrate(slide, suggestion=suggestion, several=True)
            suggestion = suggestion or calibration[slide.name][0]
    return calibration


def _confirm_key(slide: slides.Slide, chosen: list[str]) -> str:
    """Tied to the points chosen, so changing any of them withdraws the confirmation."""
    return f"calib_confirmed_{slide.name}_{'|'.join(chosen)}"


def is_confirmed(slide: slides.Slide) -> bool:
    """Whether the user confirmed this slide's current calibration points (as of the last run)."""
    chosen = [st.session_state.get(f"calib_{slide.name}_{n}") for n in range(3)]
    return None not in chosen and bool(st.session_state.get(_confirm_key(slide, chosen)))


def _calibrate(slide: slides.Slide, suggestion: list[str] | None, several: bool):
    available = slide.calibration_points or {}
    which = f"Slide **{slide.name}**" if several else "This file"
    if len(available) < 3:
        found = (
            f"{which} has no calibration points."
            if not available
            else f"{which} has only {len(available)} calibration point(s): {', '.join(available)}."
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
        logger.error(f"Stopping: {slide.name} has {len(available)} calibration points, 3 required")
        st.stop()

    options = list(available)
    defaults = [0, 1, 2]
    if suggestion and all(name in options for name in suggestion):
        defaults = [options.index(name) for name in suggestion]
    chosen = [
        st.selectbox(f"Select calibration point {n + 1}", options, index=defaults[n], key=f"calib_{slide.name}_{n}")
        for n in range(3)
    ]
    logger.info(f"Calibration points for {slide.name}: {chosen}")

    triangle = qc.triangle_qc(slide.gdf, slide.calibration_points, chosen)
    if triangle.is_degenerate:
        repeated = len(set(chosen)) < 3
        st.error(
            f"**The three calibration points{' of slide ' + slide.name if several else ''} do not "
            "form a triangle, so no collection can be made from them.**\n\n"
            + (
                "The same point is selected more than once. Pick three different points."
                if repeated
                else "All three points lie on a straight line. Pick three that form a proper "
                "triangle around your tissue."
            )
            + "\n\nThis has to stop here: the LMD software would accept the resulting file "
            "without complaint and cut in the wrong place."
        )
        logger.error(f"Stopping: degenerate calibration triangle on {slide.name} from {chosen}")
        st.stop()

    st.write(f"{triangle.fraction_inside * 100:.2f}% of shapes are inside the calibration triangle")
    if triangle.is_concerning:
        st.warning(
            "Less than 25% of the shapes fall inside the calibration triangle. Shapes far "
            "outside it get distorted by the coordinate transform, so you may cut the wrong "
            "tissue. Consider calibration points closer to your annotations."
        )
    st.checkbox(
        "These are the right calibration points" + (f" for {slide.name}" if several else ""),
        key=_confirm_key(slide, chosen),
        help=(
            "The points above are a suggestion — the first three in the file, or the names you "
            "chose on the first slide. A wrong or swapped point maps every shape to the wrong "
            "place on the stage, so nothing goes further until you have looked and confirmed."
        ),
    )
    return chosen, triangle.calibration_array


def scale_control(context: SlidesContext) -> dict[str, float | None]:
    """The image scale of every slide, editable — placed beside whatever needs it.

    Not a step of its own: the scale only matters where an amount becomes an area, and asking
    for it there explains why it is asked for (`decisions.md` 057).
    """
    if not context.several:
        slide = context.slides[0]
        current, _source = resolve_pixel_size(slide)
        entered = st.number_input(
            "Image scale (µm per pixel)",
            min_value=ui_shared.PIXEL_SIZE_MIN,
            max_value=ui_shared.PIXEL_SIZE_MAX,
            value=float(current) if current else None,
            step=ui_shared.PIXEL_SIZE_STEP,
            format=ui_shared.PIXEL_SIZE_FORMAT,
            key="pixel_size_input",
            placeholder="e.g. 0.3467",
            help=ui_shared.PIXEL_SIZE_HELP,
        )
        _remember_scale(slide, entered)
        value, source = resolve_pixel_size(slide)
        ui_shared.report_pixel_size(value, source, slide.report.implied_pixel_size_um, slide.report)
        return {slide.name: value}

    table = pandas.DataFrame(
        {"Image scale (µm/px)": [resolve_pixel_size(slide)[0] for slide in context.slides]},
        index=pandas.Index(context.names, name="Slide"),
    )
    edited = st.data_editor(
        table,
        width="stretch",
        key=f"scale_editor_{len(context.slides)}",
        column_config={
            "Image scale (µm/px)": st.column_config.NumberColumn(
                format=ui_shared.PIXEL_SIZE_FORMAT,
                min_value=ui_shared.PIXEL_SIZE_MIN,
                max_value=ui_shared.PIXEL_SIZE_MAX,
                step=ui_shared.PIXEL_SIZE_STEP,
                help=ui_shared.PIXEL_SIZE_HELP,
            )
        },
    )
    for slide in context.slides:
        value = edited.at[slide.name, "Image scale (µm/px)"]
        _remember_scale(slide, None if pandas.isna(value) else float(value))

    scales = context.pixel_sizes()
    _report_scales(context, scales)
    return scales


def _remember_scale(slide: slides.Slide, entered: float | None) -> None:
    typed = dict(st.session_state.get("pixel_size_by_slide") or {})
    estimate = slide.report.implied_pixel_size_um
    if entered and (not estimate or abs(entered - estimate) > ui_shared.PIXEL_SIZE_STEP / 2):
        typed[slide.name] = float(entered)
    else:
        typed.pop(slide.name, None)
    st.session_state.pixel_size_by_slide = typed


def _report_scales(context: SlidesContext, scales: dict[str, float | None]) -> None:
    """Several slides: where each scale came from, and whether they disagree."""
    missing = [name for name, value in scales.items() if not value]
    if missing:
        st.caption(
            f"No scale for {', '.join(missing)}, so amounts there can only be counted in shapes. "
            "Type one in to work in areas."
        )
    for slide in context.slides:
        value, source = resolve_pixel_size(slide)
        estimate = slide.report.implied_pixel_size_um
        if source == "entered" and estimate:
            check = qc.compare_pixel_size(value, estimate, slide.report.n_area_measurements, slide.report.pixel_size_spread)
            if check.is_concerning:
                st.warning(
                    f"Slide **{slide.name}**: your value is **{check.ratio:.2f}×** what its file "
                    f"implies ({estimate:.4f} µm/px). A 2× error in scale is a 4× error in every area."
                )
    known = [value for value in scales.values() if value]
    if len(known) > 1 and max(known) / min(known) > 1 + ui_shared.SCALE_DISAGREEMENT:
        st.warning(
            f"The slides' scales differ by up to {max(known) / min(known) - 1:.0%}. That is right "
            "if they were scanned at different magnifications, and a mistake otherwise. Each "
            "slide's areas are measured at its own scale either way."
        )


def render() -> SlidesContext | None:
    """Stage 1. Returns the slides with their calibration, or None until there is a file."""
    st.markdown("## 1 · Slides")
    read = upload_step()
    if not read:
        return None
    calibration = calibration_step(read)
    st.session_state.calibration = {name: names for name, (names, _array) in calibration.items()}

    waiting = [slide.name for slide in read if not st.session_state.get(_confirm_key(slide, calibration[slide.name][0]))]
    if waiting:
        # Not a stop: nothing is wrong, the user has simply not looked yet (`decisions.md` 079).
        st.info(
            "Confirm the calibration points to continue"
            + (f" — still to confirm: {', '.join(waiting)}." if len(read) > 1 else ".")
        )
        return None
    return SlidesContext(slides=read, calibration=calibration)
