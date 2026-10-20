"""Stage 3, Collector: fit the samples onto as many plates, tube holders or strips as they need.

Reads only `SampleSet.samples`, so it is the same stage whatever chose the samples. One plate is
the layout the app has always made; more collectors appear only when the samples need them or the
user asks (`decisions.md` 076). One kind of collector per experiment (`decisions.md` 081).
"""

import json
from dataclasses import dataclass

import streamlit as st
from loguru import logger

from qupath_to_lmd import plate, qc, ui_shared
from qupath_to_lmd.model import SampleSet


def _distribution_labels(noun: str) -> dict:
    return {
        plate.PlateDistribution.BALANCED: f"Balanced — every {noun} holds every class (recommended)",
        plate.PlateDistribution.SEQUENTIAL: f"Fill {noun} 1, then {noun} 2",
    }


@dataclass
class PlateLayout:
    """Where every sample goes, and the settings that put it there."""

    plate_type: str
    settings: dict
    assignment: dict[str, tuple[str, str]]
    source: str = "plate builder"

    @property
    def schemes(self) -> dict[str, dict[str, str]]:
        """One samples-and-wells scheme per plate."""
        return plate.per_plate(self.assignment)

    @property
    def n_plates(self) -> int:
        """Plates that receive at least one sample."""
        return len(self.schemes)

    def params(self) -> dict:
        """The plate settings, for provenance."""
        recorded = {
            "plate": self.plate_type,
            "margins": self.settings["margins"],
            "step_row": self.settings["step_row"],
            "step_col": self.settings["step_col"],
            "randomize_wells": self.settings["randomize"],
            "samples_and_wells_source": self.source,
        }
        if self.n_plates > 1:
            recorded.update({"plates": self.n_plates, "plate_distribution": self.settings["distribution"]})
        return recorded


PLATE_ONLY_KEYS = ("plate_margin", "plate_step_row", "plate_step_col")


def settings_step() -> dict:
    """Collector, margin, spacing, start position and randomizing."""
    collector_col, margin_col, step_row_col, step_col_col, start_col, random_col = st.columns(6)
    with collector_col:
        plate_type = st.selectbox(
            "Collector",
            list(plate.COLLECTORS),
            format_func=lambda key: plate.COLLECTORS[key].label,
            key="plate_type",
            help="What the LMD collects into. One kind per experiment.",
        )
    chosen = plate.collector(plate_type)
    margin, step_row, step_col = 0, 1, 1
    if not chosen.spacing:
        # Streamlit drops a keyed widget's value on any run that does not draw it, so a glance
        # at the tube holder would reset a plate's margin 2 to the default 1. Writing the values
        # back keeps them for when a plate is chosen again (`decisions.md` 083).
        for key in PLATE_ONLY_KEYS:
            if key in st.session_state:
                st.session_state[key] = st.session_state[key]
    # Hidden rather than disabled on the holders: a margin means nothing on four tubes, and a
    # greyed-out control asks why it cannot be used (`decisions.md` 081).
    if chosen.spacing:
        st.markdown(
            "Choose how many wells to leave as a margin (for a 384 well plate we suggest 2), and "
            "how many to leave blank between samples for easier pipetting."
        )
        with margin_col:
            margin = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1, key="plate_margin")
        with step_row_col:
            step_row = st.number_input(
                "Space between rows", min_value=1, max_value=10, value=1, key="plate_step_row"
            )
        with step_col_col:
            step_col = st.number_input(
                "Space between columns", min_value=1, max_value=10, value=1, key="plate_step_col"
            )
    with start_col:
        start_well = st.text_input(
            f"Start at {chosen.position}",
            value="",
            placeholder="auto",
            key="plate_start_well",
            help=(
                f"Fill the first {chosen.noun} from this {chosen.position} onwards instead of the "
                f"first usable one — for adding to a {chosen.noun} that already holds samples from "
                "an earlier run. Leave blank to start at the beginning."
            ),
        )
    with random_col:
        randomize = st.toggle(
            f"Randomize {chosen.position}s",
            value=False,
            key="plate_randomize",
            help=(
                f"Spread samples over the {chosen.noun} instead of filling it in order, so a "
                f"systematic position effect cannot be mistaken for a biological one. Seeded, so "
                "the layout is still reproducible."
            ),
        )

    usable = plate.acceptable_wells(plate=plate_type, margins=margin, step_row=step_row, step_col=step_col)
    start = start_well.strip().upper() or None
    first_plate = plate.wells_from(usable, start)
    if start and first_plate is usable:
        st.warning(
            f"**{start_well}** is not one of the {len(usable)} {chosen.position}s usable on this "
            f"{chosen.noun}, so filling starts from the beginning instead."
        )
        start = None
    return {
        "plate_type": plate_type,
        "margins": margin,
        "step_row": step_row,
        "step_col": step_col,
        "randomize": randomize,
        "start_well": start,
        "usable": usable,
        "first_plate": first_plate,
    }


def _plates_control(samples: list[str], settings: dict) -> tuple[int, plate.PlateDistribution]:
    """How many collectors, and how samples are spread over them — only shown when it matters."""
    chosen = plate.collector(settings.get("plate_type", "384"))
    usable = settings["usable"]
    if not usable:
        st.error("This margin and spacing leave no usable wells. Lower them, or use a 384 well plate.")
        st.stop()
    needed = plate.plates_needed(len(samples), len(usable), len(settings["first_plate"]))
    labels = _distribution_labels(chosen.noun)

    plates_column, distribution_column = st.columns([1, 3])
    with plates_column:
        n_plates = st.number_input(
            f"{chosen.noun.capitalize()}s",
            min_value=1,
            # Four tubes to a holder runs past 50 holders at 200 samples, and a value above the
            # maximum makes Streamlit raise.
            max_value=max(50, needed),
            value=needed,
            step=1,
            key=f"n_plates_{needed}",
            help=(
                f"These {len(samples)} samples need at least {needed}. More {chosen.noun}s spread "
                "the samples thinner, which some designs want."
            ),
        )
    distribution = plate.PlateDistribution.BALANCED
    if n_plates > 1:
        with distribution_column:
            distribution = st.radio(
                f"How samples are spread over the {chosen.noun}s",
                options=list(labels),
                format_func=lambda option: labels[option],
                key="plate_distribution_choice",
                help=(
                    f"Filling {chosen.noun} 1 first puts whole classes on one {chosen.noun}, so "
                    f"anything that differs between {chosen.noun}s — a batch, a run, a day — would "
                    f"look like a difference between classes. Balanced puts every class on every "
                    f"{chosen.noun} where the replicates allow."
                ),
            )
    st.session_state.n_plates = int(n_plates)
    st.session_state.plate_distribution = distribution.value
    return int(n_plates), distribution


def _custom_assignment(samples: list[str], plate_type: str) -> dict[str, tuple[str, str]] | None:
    """A samples-and-wells file of the user's own, overriding the generated layout.

    One collector (`{sample: well}`) or several (`{"Plate1": {sample: well}, ...}`) — so the
    `samples_and_wells.json` of any earlier download loads back in for changes.
    """
    chosen = plate.collector(plate_type)
    first, second = ("C3", "C5") if chosen.numbered else ("C", "D")
    with st.expander("Use your own samples-and-wells file instead"):
        st.caption(
            f"A `.txt` or `.json` holding a dictionary of sample to {chosen.position}, e.g. "
            f"`{{'Tumor_r1': '{first}', 'Tumor_r2': '{second}'}}`, or of {chosen.noun} to such a "
            f"dictionary, e.g. `{{'{chosen.name_prefix}1': {{...}}, '{chosen.name_prefix}2': {{...}}}}`. "
            "The `samples_and_wells.json` of an earlier download works as it is. It replaces the "
            "layout above."
        )
        uploaded = st.file_uploader(
            "Samples-and-wells file", type=["txt", "json"], accept_multiple_files=False, key="saw_uploader"
        )
        if uploaded is None:
            return None
        try:
            assignment = plate.assignment_from_scheme(plate.parse_saw_file(uploaded), plate=plate_type)
        except plate.SawParseError as error:
            st.error(f"Could not read that samples-and-wells file: {error}")
            logger.error(f"Samples-and-wells parse failed: {error}")
            return None

        invalid = False
        for name, scheme in plate.per_plate(assignment).items():
            report = qc.validate_saw(scheme, [], plate=plate_type)
            if report.duplicate_wells:
                st.warning(f"{name}: {chosen.position}s receiving more than one sample: {report.duplicate_wells}")
            if report.invalid_wells:
                st.error(
                    f"{name}: these {chosen.position}s do not exist on a {chosen.label}: "
                    f"{sorted(report.invalid_wells)}. Fix the file or change the collector."
                )
                invalid = True
        if invalid:
            return None
        missing = [sample for sample in samples if sample not in assignment]
        if missing:
            st.warning(
                f"{len(missing)} samples have no {chosen.position} in your file and will not be "
                f"collected: {', '.join(missing[:10])}"
            )
        unknown = [sample for sample in assignment if sample not in samples]
        if unknown:
            st.caption(f"Ignored, not samples of this collection: {', '.join(unknown[:10])}")
            assignment = {sample: place for sample, place in assignment.items() if sample in samples}
        n_plates = len(plate.per_plate(assignment))
        st.success(
            f"Using your samples-and-wells file: {len(assignment)} samples on {n_plates} {chosen.noun}(s)."
        )
        return assignment


def _capacity_report(
    samples: list[str], settings: dict, n_plates: int, assignment: dict[str, tuple[str, str]]
) -> None:
    """How many positions the samples need against what the collectors offer, before download."""
    chosen = plate.collector(settings.get("plate_type", "384"))
    offered = len(settings["first_plate"]) + len(settings["usable"]) * (n_plates - 1)
    st.write(
        f"These samples need **{len(samples)} {chosen.position}s**. "
        + (
            f"This {chosen.noun} offers **{offered}**."
            if n_plates == 1
            else f"**{n_plates} {chosen.noun}s** offer **{offered}**."
        )
    )
    # Counted from where the samples went, not from the number box: sequential filling or an
    # uploaded file can leave collectors empty, and an empty one gets no .xml.
    used = len(plate.per_plate(assignment))
    if used > 1:
        st.warning(
            f"The samples go onto {used} {chosen.noun}s. That is **{used} separate cutting runs** "
            f"for each slide, one `.xml` per slide and {chosen.noun}, with the {chosen.noun} "
            "changed between them."
        )
    unplaced = [sample for sample in samples if sample not in assignment]
    if unplaced:
        remedy = ", or lower the margin or spacing" if chosen.spacing else ""
        st.warning(
            f"{len(unplaced)} sample(s) have no {chosen.position} and will not be collected: "
            f"{', '.join(unplaced[:8])}{' ...' if len(unplaced) > 8 else ''}. Add a {chosen.noun}, "
            f"or reduce the replicates{remedy}."
        )


def render(sample_set: SampleSet) -> PlateLayout | None:
    """Stage 3. Returns where every sample goes."""
    st.markdown("## 3 · Collector")
    samples = sample_set.samples
    if not samples:
        st.info("No samples to place yet.")
        return None

    settings = settings_step()
    plate_type = settings["plate_type"]
    n_plates, distribution = _plates_control(samples, settings)
    settings["distribution"] = distribution.value

    custom = _custom_assignment(samples, plate_type)
    if custom is not None:
        layout = PlateLayout(plate_type, settings, custom, source="uploaded")
    else:
        assignment = plate.assign_to_plates(
            samples, settings["usable"], n_plates, distribution,
            randomize=settings["randomize"], start_well=settings["start_well"], plate=plate_type,
        )
        layout = PlateLayout(plate_type, settings, assignment)

    chosen = plate.collector(plate_type)
    _capacity_report(samples, settings, n_plates, layout.assignment)

    schemes = layout.schemes
    names = list(schemes)
    if not names:
        st.warning(f"No sample has a {chosen.position}, so there is nothing to cut.")
        return None
    edited: dict[str, tuple[str, str]] = {}
    slots = []
    several = len(names) > 1
    containers = st.tabs(names) if several else [st.container()]
    for container, name in zip(containers, names, strict=True):
        with container:
            wells = settings["first_plate"] if name == plate.plate_names(1, plate_type)[0] else settings["usable"]
            scheme = ui_shared.editable_plate(schemes[name], plate_type, key_suffix=name)
            slots.append(
                ui_shared.plate_preview(
                    scheme, plate_type, wells=wells, key_suffix=name, plate_name=name, slot_for_all=several
                )
            )
            edited.update({sample: (name, well) for sample, well in scheme.items()})
    layout.assignment = edited

    if several:
        # Filled after every tab, so hand edits on any collector are in the file. The same shape as
        # `samples_and_wells.json` in the download, and both load back in below.
        everything = json.dumps(plate.per_plate(edited), indent=4)
        for slot, name in zip(slots, names, strict=True):
            slot.download_button(
                f"Download samples and wells setup for all {chosen.noun}s",
                data=everything,
                file_name=f"samples_and_wells_all_{chosen.noun.replace(' ', '_')}s.json",
                mime="application/json",
                key=f"saw_download_all_{name}",
            )
    return layout
