"""Stage 4, Cut: the cutting files, what goes where, and the download.

Works from a `SampleSet` and a `PlateLayout` only. One slide on one collector builds exactly the
bundle the app always has; anything more becomes one `.xml` per slide and collector, with numbered
instructions in the order the user cuts in (`decisions.md` 076).
"""

import hashlib
from pathlib import Path

import geopandas
import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import export, plate, plot, slides, ui_shared
from qupath_to_lmd.model import CLASS_NAME, GROUP_KEY, SampleSet
from qupath_to_lmd.ui_plates import PlateLayout
from qupath_to_lmd.ui_slides import SlidesContext


def _cut_order_labels(noun: str) -> dict:
    return {
        export.CutOrder.BY_SLIDE: "By slide — a folder per slide",
        export.CutOrder.BY_PLATE: f"By {noun} — a folder per {noun}",
    }


def _cut_order(context: SlidesContext, layout: PlateLayout) -> export.CutOrder:
    if not (context.several and layout.n_plates > 1):
        return export.CutOrder.BY_SLIDE
    labels = _cut_order_labels(plate.collector(layout.plate_type).noun)
    order = st.radio(
        "Organise the download",
        options=list(labels),
        format_func=lambda option: labels[option],
        key="cut_order_choice",
        horizontal=True,
    )
    st.caption(
        "This only changes how the download is organised: the folders, the file names, and the "
        "order of COLLECTION_PLAN.txt. Every `.xml` is the same either way, and you can load them "
        "on the LMD in any order you like."
    )
    st.session_state.cut_order = order.value
    return order


def _for_csv(sheet: pandas.DataFrame) -> pandas.DataFrame:
    """µm² as whole numbers, as everywhere else they are shown (`decisions.md` 072)."""
    areas = [column for column in sheet.columns if column.endswith("µm²")]
    return sheet.assign(**{column: sheet[column].round(0).astype("Int64") for column in areas})


def _overview(context: SlidesContext, sample_set: SampleSet, layout: PlateLayout) -> pandas.DataFrame:
    """Every sample with its plate and well, and the files that will cut them."""
    sheet = sample_set.sheet()
    if layout.n_plates > 1:
        sheet.insert(1, "plate", sheet["sample"].map(lambda s: layout.assignment.get(s, (None, None))[0]))
    sheet.insert(1 + (layout.n_plates > 1), "well", sheet["sample"].map(lambda s: layout.assignment.get(s, (None, None))[1]))
    st.markdown("**Every sample, where it goes, and what it holds**")
    ui_shared.show_amounts(sheet.set_index("sample"))
    st.download_button(
        "Download this sample sheet",
        data=_for_csv(sheet).to_csv(index=False),
        file_name="samples.csv",
        mime="text/csv",
        key="sample_sheet_download",
    )

    chosen = plate.collector(layout.plate_type)
    files = []
    for plate_name, scheme in layout.schemes.items():
        for name, frame in sample_set.shapes.items():
            cut = frame[frame[GROUP_KEY].isin(list(scheme))]
            if cut.empty:
                continue
            files.append(
                {
                    "Slide": name,
                    chosen.noun.capitalize(): plate_name,
                    "Shapes": len(cut),
                    f"{chosen.position.capitalize()}s": cut[GROUP_KEY].nunique(),
                    "Calibration points": ", ".join(context.calibration[name][0]),
                }
            )
    if len(files) > 1:
        st.markdown(f"**{len(files)} cutting files, one per slide and {chosen.noun}**")
        st.dataframe(pandas.DataFrame(files), width="stretch", hide_index=True)
    return sheet


def _report_excluded(sample_set: SampleSet, layout: PlateLayout) -> None:
    """What will not be cut, by cause (`decisions.md` 048), over every slide and plate."""
    unplaced, not_selected, total = 0, 0, 0
    unplaced_samples: set[str] = set()
    not_selected_classes: set[str] = set()
    for frame in sample_set.shapes.values():
        total += len(frame)
        grouped = frame[GROUP_KEY].notna()
        missing = grouped & ~frame[GROUP_KEY].isin(list(layout.assignment))
        unplaced += int(missing.sum())
        unplaced_samples |= set(frame.loc[missing, GROUP_KEY])
        not_selected += int((~grouped).sum())
        not_selected_classes |= set(frame.loc[~grouped, CLASS_NAME])

    if unplaced:
        chosen = plate.collector(layout.plate_type)
        names = sorted(unplaced_samples)
        remedy = ", or lower the margin or spacing" if chosen.spacing else ""
        st.warning(
            f"{unplaced} shapes you asked to collect will **not** be cut, because their sample "
            f"got no {chosen.position}: {', '.join(names[:8])}{' ...' if len(names) > 8 else ''}. "
            f"Add a {chosen.noun}, or reduce the replicates{remedy}."
        )
    if not not_selected:
        return
    if sample_set.workflow == "legacy":
        st.warning(
            f"{not_selected} of {total} shapes are in classes you did not keep and will not be "
            f"cut: {', '.join(sorted(not_selected_classes)[:10])}"
        )
    else:
        st.caption(f"{not_selected:,} of {total:,} shapes are not part of this collection, as intended — you asked for a subset.")


EXPANSION_HELP = (
    "Moves every outline outward before it is cut. QuPath draws a cell where it found the "
    "membrane, and the laser burns a line about 1-2 µm wide along its path, so cutting on the "
    "outline burns the cell's own edge. Expanding by 1 µm keeps that edge in the piece that falls. "
    "In dense tissue cells sit well under a micrometre apart, so any expansion also takes a little "
    "of each neighbour. Lines are not expanded."
)
# Without a scale for every slide a distance can only be given in pixels.
DEFAULT_EXPANSION_PX = 2.0


def _expansion(context: SlidesContext, sample_set: SampleSet, layout: PlateLayout) -> tuple[dict[str, float], dict]:
    """Expanding shapes before cutting (issue 31): off unless asked, its effect shown before download.

    Returns the distance in pixels per slide (empty when off) and what to record in provenance.
    """
    if not st.checkbox("Expand shapes before cutting", key="expand_shapes", help=EXPANSION_HELP):
        return {}, {}
    names = sample_set.slide_names
    scales = {name: sample_set.pixel_sizes.get(name) or context.pixel_sizes().get(name) for name in names}
    if all(scales.values()):
        amount = st.number_input(
            "Expand each shape by (µm)", min_value=0.0, max_value=20.0, value=export.DEFAULT_EXPANSION_UM,
            step=0.1, format="%.1f", key="expand_um", help=EXPANSION_HELP,
        )
        distances = {name: amount / scales[name] for name in names}
        params = {"expand_um": amount}
    else:
        amount = st.number_input(
            "Expand each shape by (px)", min_value=0.0, max_value=100.0, value=DEFAULT_EXPANSION_PX,
            step=0.5, format="%.1f", key="expand_px", help=EXPANSION_HELP,
        )
        st.caption(
            "In pixels, because not every slide has an image scale. Enter one in Stage 2 to set "
            "the distance in µm."
        )
        distances = dict.fromkeys(names, amount)
        params = {"expand_px": amount}
    if amount <= 0:
        return {}, {}

    rows, example = [], None
    for name in names:
        frame = sample_set.shapes[name]
        destination = frame[GROUP_KEY].map(layout.assignment)
        report = export.expansion_report(frame, destination, distances[name])
        if not report.n_shapes:
            continue
        scale = scales[name]
        unit = scale**2 if scale else 1.0
        rows.append({
            "Slide": name,
            "Shapes": report.n_shapes,
            f"Area before ({'µm²' if scale else 'px²'})": report.area_before_px2 * unit,
            f"Area after ({'µm²' if scale else 'px²'})": report.area_after_px2 * unit,
            "Added (%)": round(100 * report.added_fraction, 1),
            "Overlap another sample": report.n_into_other_sample,
            "Reach into shapes not collected": report.n_into_uncollected,
        })
        if example is None:
            example = (context, name, frame, destination, distances[name], scale)
    if not rows:
        return distances, params

    table = pandas.DataFrame(rows).set_index("Slide")
    if not context.several:
        table = table.reset_index(drop=True)
    ui_shared.show_amounts(table)
    added = sum(r["Added (%)"] * r["Shapes"] for r in rows) / sum(r["Shapes"] for r in rows)
    st.caption(
        f"Expanding adds about {added:.0f}% to the area cut. The amounts in the sample sheet are "
        "for the shapes as drawn."
    )
    overlapping = sum(r["Overlap another sample"] for r in rows)
    if overlapping:
        st.warning(
            f"After expanding, {overlapping:,} collected shapes overlap a shape going to a "
            f"different {plate.collector(layout.plate_type).position}. Where two cuts overlap, the "
            "tissue between them falls with whichever is cut first, so samples can lose tissue to "
            "each other or carry some of each other's. Expand less, or collect fewer neighbouring "
            "shapes (Stage 2's spread and neighbour settings)."
        )
    reaching = sum(r["Reach into shapes not collected"] for r in rows)
    if reaching:
        st.caption(
            f"{reaching:,} collected shapes reach into a neighbour that is not being collected, so "
            "each piece carries a thin rim of that neighbour."
        )
    _expansion_picture(*example)
    return distances, params


def _expansion_picture(context, name, frame, destination, distance_px, scale) -> None:
    """One typical collected shape, before and after, with its neighbours."""
    collected = frame[destination.notna() & (frame.geom_type == "Polygon")]
    areas = collected.geometry.area
    index = (areas - areas.median()).abs().idxmin()
    shape = collected.geometry[index]
    grown = export.expand_shapes(geopandas.GeoSeries([shape]), distance_px).iloc[0]
    cut = grown.simplify(st.session_state.get("simplify_tolerance") or export.DEFAULT_SIMPLIFY_TOLERANCE)
    nearby = frame.geometry[frame.geometry.dwithin(shape, 4 * distance_px + shape.length / 8)].drop(index)
    # the experiment's palette, as every other picture uses (`decisions.md` 080)
    palette = plot.class_colors(sorted({c for f in context.frames.values() for c in f[CLASS_NAME].dropna()}))
    color = palette.get(frame.at[index, CLASS_NAME], plot.PALETTE[0])
    figure_key = ui_shared.picture_key("expansion", name, geopandas.GeoSeries([shape]), distance_px, scale, color)
    column, _margin = st.columns([1, 2])
    with column:
        ui_shared.show_picture(
            lambda: plot.plot_expansion(shape, grown, cut, list(nearby), color, scale), figure_key
        )
        st.caption(f"A typical shape from {name}, before and after expanding.")


def _signature(sample_set: SampleSet, layout: PlateLayout, tolerance: float, path_order, cut_order, expand_px=None) -> str:
    """What the download was built from, so a stale one is never offered as current."""
    parts = [
        repr(sorted(layout.assignment.items())),
        str(sample_set.n_collected()),
        repr({name: frame[GROUP_KEY].value_counts().to_dict() for name, frame in sample_set.shapes.items()}),
        repr(sorted(sample_set.params.items(), key=lambda item: item[0])),
        f"{tolerance}|{path_order.value}|{cut_order.value}",
        repr(sorted((expand_px or {}).items())),
    ]
    return hashlib.md5("||".join(parts).encode()).hexdigest()


def _process_one(context, sample_set, layout, tolerance, path_order, expansion=({}, {})) -> None:
    """One slide, one plate: the bundle the app has always produced."""
    slide = context.slides[0]
    (plate_name, scheme), = layout.schemes.items()
    names, array = context.calibration[slide.name]
    plan = sample_set.plan(
        slide.name, scheme, names, array,
        source_file=slide.source_file, session_id=st.session_state.session_id,
        params={**layout.params(), "simplify_tolerance_px": tolerance, "path_order": path_order.value, **expansion[1]},
    )
    try:
        result = export.build_collection(
            plan, samples_and_wells=scheme, simplify_tolerance=tolerance,
            plate=layout.plate_type, path_order=path_order, expand_px=expansion[0].get(slide.name, 0.0),
        )
    except ValueError as error:
        st.error(str(error))
        logger.error(str(error))
        return
    st.session_state.zip_buffer = export.build_bundle(
        plan=plan, result=result, samples_and_wells=scheme, plate=layout.plate_type,
        log_path=st.session_state.log_file_path,
    )
    st.session_state.bundle_name = f"{Path(slide.source_file or 'collection').stem}_collection.zip"
    st.session_state.collection_image = result.image_path
    st.write(
        f"Collection: {result.n_shapes} shapes, {result.n_vertices} vertices, "
        f"{len(plan.wells_used)} {plate.collector(layout.plate_type).position}s used."
    )
    ui_shared.report_path(result, plan.pixel_size_um)
    st.image(result.image_path, caption="The shapes that will be cut", width="content")


def _process_experiment(context, sample_set, layout, tolerance, path_order, cut_order, expansion=({}, {})) -> None:
    """Several slides or plates: one `.xml` per pair, the sample sheet, and the instructions."""
    try:
        cuts = slides.cuts_for_experiment(
            sample_set, context.slides, layout.assignment, context.calibration,
            plate=layout.plate_type, simplify_tolerance=tolerance, path_order=path_order,
            session_id=st.session_state.session_id,
            params={**layout.params(), "simplify_tolerance_px": tolerance, "path_order": path_order.value, **expansion[1]},
            expand_px=expansion[0],
        )
    except ValueError as error:
        st.error(str(error))
        logger.error(str(error))
        return
    if not cuts:
        st.warning("Nothing to cut: no slide has shapes in a well.")
        return
    st.session_state.zip_buffer = export.build_experiment_bundle(
        cuts, _for_csv(sample_set.sheet()).assign(
            plate=lambda sheet: sheet["sample"].map(lambda s: layout.assignment.get(s, (None, None))[0]),
            well=lambda sheet: sheet["sample"].map(lambda s: layout.assignment.get(s, (None, None))[1]),
        ),
        layout.schemes, plate=layout.plate_type, order=cut_order,
        provenance={
            "workflow": sample_set.workflow, "slides": context.names, **sample_set.params, **layout.params(),
            **expansion[1],
        },
        log_path=st.session_state.log_file_path, session_id=st.session_state.session_id,
    )
    first = Path(context.slides[0].source_file or "experiment").stem
    st.session_state.bundle_name = f"{first}_experiment.zip"
    st.session_state.collection_image = cuts[0].result.image_path

    st.write(
        f"**{len(cuts)} cutting files**, {sum(c.result.n_shapes for c in cuts):,} shapes in all. "
        f"COLLECTION_PLAN.txt in the download lists each one with its slide, "
        f"{plate.collector(layout.plate_type).noun} and calibration points."
    )
    for cut in cuts:
        ui_shared.report_path(cut.result, cut.plan.pixel_size_um, label=f"{cut.slide} → {cut.plate}")


def render(context: SlidesContext, sample_set: SampleSet, layout: PlateLayout) -> bool:
    """Stage 4. Returns True once a current download is ready."""
    st.markdown("## 4 · Cut")
    st.markdown(
        f"Create the `.xml` file(s) for the LMD. Keep the QC image and the "
        f"{plate.collector(layout.plate_type).noun} map in the download for your records."
    )
    tolerance, path_order = ui_shared.export_parameters()
    expand_px, expand_params = _expansion(context, sample_set, layout)
    cut_order = _cut_order(context, layout)

    _overview(context, sample_set, layout)
    _report_excluded(sample_set, layout)

    signature = _signature(sample_set, layout, tolerance, path_order, cut_order, expand_px)
    if st.button("Process files", type="primary"):
        logger.info("Process files button clicked")
        st.session_state.zip_buffer = None
        expansion = (expand_px, expand_params)
        if not context.several and layout.n_plates == 1:
            _process_one(context, sample_set, layout, tolerance, path_order, expansion)
        else:
            _process_experiment(context, sample_set, layout, tolerance, path_order, cut_order, expansion)
        if st.session_state.zip_buffer is not None:
            st.session_state.bundle_signature = signature
            st.success("All files have been processed and are ready for download.")
            logger.success("All files processed and zipped successfully")

    if st.session_state.zip_buffer is None:
        return False
    if st.session_state.bundle_signature != signature:
        st.info("Something above has changed since these files were made. Process them again to download.")
        return False
    st.download_button(
        label="Download files",
        data=st.session_state.zip_buffer.getvalue(),
        file_name=st.session_state.bundle_name or "collection.zip",
        mime="application/zip",
    )
    return True
