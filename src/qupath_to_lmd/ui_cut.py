"""Stage 4, Cut: the cutting files, what goes where, and the download.

Works from a `SampleSet` and a `PlateLayout` only. One slide on one collector builds exactly the
bundle the app always has; anything more becomes one `.xml` per slide and collector, with numbered
instructions in the order the user cuts in (`decisions.md` 076).
"""

import hashlib
from pathlib import Path

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import export, plate, slides, ui_shared
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


def _signature(sample_set: SampleSet, layout: PlateLayout, tolerance: float, path_order, cut_order) -> str:
    """What the download was built from, so a stale one is never offered as current."""
    parts = [
        repr(sorted(layout.assignment.items())),
        str(sample_set.n_collected()),
        repr({name: frame[GROUP_KEY].value_counts().to_dict() for name, frame in sample_set.shapes.items()}),
        repr(sorted(sample_set.params.items(), key=lambda item: item[0])),
        f"{tolerance}|{path_order.value}|{cut_order.value}",
    ]
    return hashlib.md5("||".join(parts).encode()).hexdigest()


def _process_one(context, sample_set, layout, tolerance, path_order) -> None:
    """One slide, one plate: the bundle the app has always produced."""
    slide = context.slides[0]
    (plate_name, scheme), = layout.schemes.items()
    names, array = context.calibration[slide.name]
    plan = sample_set.plan(
        slide.name, scheme, names, array,
        source_file=slide.source_file, session_id=st.session_state.session_id,
        params={**layout.params(), "simplify_tolerance_px": tolerance, "path_order": path_order.value},
    )
    try:
        result = export.build_collection(
            plan, samples_and_wells=scheme, simplify_tolerance=tolerance,
            plate=layout.plate_type, path_order=path_order,
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


def _process_experiment(context, sample_set, layout, tolerance, path_order, cut_order) -> None:
    """Several slides or plates: one `.xml` per pair, the sample sheet, and the instructions."""
    try:
        cuts = slides.cuts_for_experiment(
            sample_set, context.slides, layout.assignment, context.calibration,
            plate=layout.plate_type, simplify_tolerance=tolerance, path_order=path_order,
            session_id=st.session_state.session_id,
            params={**layout.params(), "simplify_tolerance_px": tolerance, "path_order": path_order.value},
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
        provenance={"workflow": sample_set.workflow, "slides": context.names, **sample_set.params, **layout.params()},
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
    cut_order = _cut_order(context, layout)

    _overview(context, sample_set, layout)
    _report_excluded(sample_set, layout)

    signature = _signature(sample_set, layout, tolerance, path_order, cut_order)
    if st.button("Process files", type="primary"):
        logger.info("Process files button clicked")
        st.session_state.zip_buffer = None
        if not context.several and layout.n_plates == 1:
            _process_one(context, sample_set, layout, tolerance, path_order)
        else:
            _process_experiment(context, sample_set, layout, tolerance, path_order, cut_order)
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
