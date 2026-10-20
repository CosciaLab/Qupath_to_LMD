"""Collectors: which positions are usable, laying samples out on them, and reading layouts back.

A collector is what the LMD cuts into: a plate, a tube holder or a strip holder. The module and
its `plate` parameters keep the older name; in code, `plate` means the collector
(`decisions.md` 081).
"""

import ast
import math
import string
from dataclasses import dataclass
from enum import Enum
from random import Random

import pandas
from loguru import logger


@dataclass(frozen=True)
class Collector:
    """One kind of collector the LMD holds, and how its positions are written."""

    key: str
    label: str
    rows: int
    columns: int
    # Plates write a position as row and column, `C3`. The tube and strip holders have one
    # column, and the LMD names their caps by the letter alone, `C` (`decisions.md` 081).
    numbered: bool
    name_prefix: str
    noun: str
    position: str
    # Margin and spacing exist for the unreliable edge wells of a 384 plate and for pipetting
    # between wells; on a holder of four tubes they would only leave tubes unused.
    spacing: bool


COLLECTORS = {
    "384": Collector("384", "384 well plate", 16, 24, True, "Plate", "plate", "well", True),
    "96": Collector("96", "96 well plate", 8, 12, True, "Plate", "plate", "well", True),
    "tubes": Collector(
        "tubes", "Eppendorf tube holder (4 tubes)", 4, 1, False, "TubeHolder", "tube holder", "tube", False
    ),
    "strip": Collector("strip", "8-well strip holder", 8, 1, False, "Strip", "strip holder", "well", False),
}


class SawParseError(Exception):
    """A samples-and-wells file could not be read as a dictionary."""


def collector(plate: str) -> Collector:
    """The collector for a key of `COLLECTORS`."""
    if plate not in COLLECTORS:
        raise ValueError(f"Collector must be one of {list(COLLECTORS)}, got {plate!r}")
    return COLLECTORS[plate]


def plate_dimensions(plate: str) -> tuple[int, int]:
    """Rows and columns of a supported collector.

    Named `dimensions` rather than `shape`, because in this app a shape is something the
    laser cuts (see GLOSSARY.md).
    """
    chosen = collector(plate)
    return chosen.rows, chosen.columns


def split_position(label: str) -> tuple[str, int | None]:
    """`C3` is row C, column 3; `C`, a tube or a strip well, is row C with no column."""
    return label[0], int(label[1:]) if len(label) > 1 else None


def _column_labels(chosen: Collector, as_text: bool) -> list:
    if not chosen.numbered:
        return [chosen.position]
    return [str(i) if as_text else i for i in range(1, chosen.columns + 1)]


def _cell(chosen: Collector, label: str) -> tuple[str, object]:
    """Where a position sits in a collector's table: row and column label."""
    row, column = split_position(label)
    return row, chosen.position if column is None else column


def acceptable_wells(plate: str = "384", margins: int = 0, step_row: int = 1, step_col: int = 1) -> list[str]:
    """Wells left usable after a margin and row/column spacing.

    The margin exists because the LMD7 collects unreliably into the outermost wells of a
    384 plate; the steps leave blanks between samples for easier pipetting.
    """
    chosen = collector(plate)
    if not chosen.spacing:
        return list(string.ascii_uppercase[: chosen.rows])
    max_row, max_col = plate_dimensions(plate)
    if not isinstance(margins, int):
        raise ValueError("margins must be an integer")

    min_row, min_col = 1, 1
    if margins > 0:
        max_row -= margins
        max_col -= margins
        min_row += margins
        min_col += margins

    return [
        f"{row}{column}"
        for row in string.ascii_uppercase[min_row - 1 : max_row : step_row]
        for column in range(min_col, max_col + 1, step_col)
    ]


def wells_from(wells: list[str], start_well: str | None) -> list[str]:
    """The usable wells from `start_well` onwards, in plate order.

    Exists for collecting several slides into one plate: run the first slide from `B2`, note the
    last well it used, then run the second from the next one along. No cross-file state and no
    new concepts (`decisions.md` 061).

    An unknown or unusable start well returns the list unchanged, so a typo degrades to the
    normal behaviour rather than silently collecting nothing.
    """
    if not start_well:
        return wells
    normalised = str(start_well).strip().upper()
    if normalised not in wells:
        logger.warning(f"Start well {start_well!r} is not among the usable wells; ignoring it")
        return wells
    return wells[wells.index(normalised) :]


def default_layout(plate: str = "384") -> pandas.DataFrame:
    """The bare collector, every cell holding its own position name."""
    chosen = collector(plate)
    row_labels = list(string.ascii_uppercase[: chosen.rows])
    col_labels = _column_labels(chosen, as_text=False)
    return pandas.DataFrame(
        [[f"{row}{col}" if chosen.numbered else row for col in col_labels] for row in row_labels],
        index=row_labels,
        columns=col_labels,
    )


def assign_wells(
    groups: list[str],
    wells: list[str],
    randomize: bool = False,
    seed: int = 0,
) -> dict[str, str]:
    """Map each group to a well, in sorted order so the same plan always lands the same way.

    Randomizing spreads groups over the plate, which guards against a systematic
    position effect being read as a biological one. It is seeded, so a randomized layout is
    still reproducible and can be reported.
    """
    ordered = sorted(groups)
    available = list(wells)
    if randomize:
        available = Random(seed).sample(available, len(available))
    return dict(zip(ordered, available, strict=False))


class PlateDistribution(str, Enum):
    """How samples are spread over several plates."""

    BALANCED = "balanced"
    SEQUENTIAL = "sequential"


def plate_names(n_plates: int, plate: str = "384") -> list[str]:
    """`Plate1`, `TubeHolder1`, `Strip1`, … — the names collectors carry on screen and in files.

    Explicit, because most experiments have only a few (`decisions.md` 081).
    """
    prefix = collector(plate).name_prefix
    return [f"{prefix}{number}" for number in range(1, n_plates + 1)]


def _number_of(name: str) -> int:
    """`Plate10` is collector 10."""
    digits = name[len(name.rstrip("0123456789")) :]
    return int(digits) if digits else 0


def plates_needed(n_samples: int, usable_wells: int, first_plate_wells: int | None = None) -> int:
    """The fewest plates that hold every sample. The first plate may start part-way through."""
    if usable_wells <= 0:
        raise ValueError("No usable wells on this plate, so no number of plates can hold the samples.")
    first = usable_wells if first_plate_wells is None else first_plate_wells
    if n_samples <= first:
        return 1
    return 1 + math.ceil((n_samples - first) / usable_wells)


def _class_of(group: str) -> str:
    """`Tumor_r2` belongs to `Tumor`; a key without a replicate is its own class."""
    head, separator, tail = group.rpartition("_r")
    return head if separator and tail.isdigit() else group


def _replicate_of(group: str) -> int:
    head, separator, tail = group.rpartition("_r")
    return int(tail) if separator and tail.isdigit() else 0


def assign_to_plates(
    groups: list[str],
    wells: list[str],
    n_plates: int = 1,
    distribution: PlateDistribution = PlateDistribution.BALANCED,
    randomize: bool = False,
    seed: int = 0,
    start_well: str | None = None,
    plate: str = "384",
) -> dict[str, tuple[str, str]]:
    """Map each group to a collector and a position.

    BALANCED deals each class's replicates round-robin over the plates, each class starting on
    whichever plate has the most room, so every plate holds every class where the replicate count
    allows. Filling plate 1 first would put whole classes on one plate — `assign_wells` sorts
    groups — and a plate effect would then read as a difference between classes
    (`decisions.md` 076). SEQUENTIAL fills plate 1, then plate 2.

    Within a plate, wells come from `assign_wells`, so one plate gives exactly the layout the
    app has always produced. `start_well` applies to the first plate only. Groups that fit on no
    plate are left out of the result, for the caller to report.
    """
    wells_by_plate = {
        name: (wells_from(wells, start_well) if position == 0 else list(wells))
        for position, name in enumerate(plate_names(max(1, n_plates), plate))
    }
    if len(wells_by_plate) == 1:
        (name, plate_wells), = wells_by_plate.items()
        return {group: (name, well) for group, well in assign_wells(groups, plate_wells, randomize, seed).items()}

    room = {name: len(plate_wells) for name, plate_wells in wells_by_plate.items()}
    on_plate: dict[str, list[str]] = {name: [] for name in wells_by_plate}

    if distribution is PlateDistribution.SEQUENTIAL:
        remaining = sorted(groups)
        for name in on_plate:
            on_plate[name], remaining = remaining[: room[name]], remaining[room[name] :]
    else:
        names = list(on_plate)
        by_class: dict[str, list[str]] = {}
        for group in sorted(groups):
            by_class.setdefault(_class_of(group), []).append(group)
        for members in by_class.values():
            members.sort(key=_replicate_of)
            start = max(range(len(names)), key=lambda i: room[names[i]] - len(on_plate[names[i]]))
            for offset, group in enumerate(members):
                for step in range(len(names)):
                    name = names[(start + offset + step) % len(names)]
                    if len(on_plate[name]) < room[name]:
                        on_plate[name].append(group)
                        break

    assignment = {}
    for name, members in on_plate.items():
        for group, well in assign_wells(members, wells_by_plate[name], randomize, seed).items():
            assignment[group] = (name, well)
    left_over = len(groups) - len(assignment)
    if left_over:
        logger.warning(f"{left_over} samples fit on none of the {n_plates} plates")
    logger.info(
        f"{len(assignment)} samples over {n_plates} plates ({distribution.value}): "
        + ", ".join(f"{name}={len(members)}" for name, members in on_plate.items())
    )
    return assignment


def _named(name: str, prefix: str) -> bool:
    return name.startswith(prefix) and name[len(prefix) :].isdigit()


def assignment_from_scheme(parsed: dict, plate: str = "384") -> dict[str, tuple[str, str]]:
    """Turn a loaded samples-and-wells file into an assignment to collectors of type `plate`.

    Reads both shapes the app writes: one collector, `{"Tumor_r1": "C3", ...}`, which is the first
    one; and several, `{"Plate1": {"Tumor_r1": "C3"}, "Plate2": {...}}`, which is
    `samples_and_wells.json` from an experiment download and the all-collectors button of Stage 3.
    Files from before collectors had names, keyed `P1`, `P2`, load as plates.

    Raises:
        SawParseError: a mixture of the two, names for another kind of collector, or names that
            are not `Plate1`, `Plate2`, … (or the chosen collector's equivalent).
    """
    chosen = collector(plate)
    nested = [isinstance(value, dict) for value in parsed.values()]
    if not any(nested):
        first = plate_names(1, plate)[0]
        return {str(sample): (first, str(well)) for sample, well in parsed.items()}
    if not all(nested):
        raise SawParseError(
            f"The file mixes {chosen.noun}s and {chosen.position}s at the top level. Use either "
            f"{{sample: {chosen.position}}} for one {chosen.noun}, or "
            f"{{{chosen.noun}: {{sample: {chosen.position}}}}} for several."
        )
    names = [str(name) for name in parsed]
    renamed = {}
    for name in names:
        if _named(name, chosen.name_prefix):
            renamed[name] = name
        elif chosen.numbered and _named(name, "P"):
            renamed[name] = f"{chosen.name_prefix}{name[1:]}"
        else:
            other = next(
                (
                    candidate for candidate in COLLECTORS.values()
                    if _named(name, candidate.name_prefix) or (candidate.numbered and _named(name, "P"))
                ),
                None,
            )
            if other is not None:
                raise SawParseError(
                    f"This file is for {other.noun}s ({name}), but a {chosen.label} is chosen. "
                    "Change the collector, or use a file made for it."
                )
            raise SawParseError(
                f"{chosen.noun.capitalize()}s must be named {chosen.name_prefix}1, "
                f"{chosen.name_prefix}2, …; this file has {names}."
            )
    return {
        str(sample): (renamed[str(name)], str(well))
        for name, scheme in parsed.items()
        for sample, well in scheme.items()
    }


def per_plate(assignment: dict[str, tuple[str, str]]) -> dict[str, dict[str, str]]:
    """Split a plate assignment into one samples-and-wells scheme per plate."""
    schemes: dict[str, dict[str, str]] = {}
    for group, (plate_name, well) in assignment.items():
        schemes.setdefault(plate_name, {})[group] = well
    return dict(sorted(schemes.items(), key=lambda item: _number_of(item[0])))


def sample_layout(
    classes: list[str],
    plate: str = "384",
    wells: list[str] | None = None,
    randomize: bool = False,
    seed: int = 0,
) -> tuple[pandas.DataFrame, list[str]]:
    """Place classes into the usable wells, in order, one class per well.

    Returns the layout and the classes that did not fit, so the caller can say so rather
    than let them disappear.
    """
    chosen = collector(plate)
    wells = list(wells if wells is not None else acceptable_wells(plate))

    # Sorted, so the same file laid out twice gives the same plate.
    ordered_classes = sorted(classes)
    if randomize:
        logger.info(f"Randomizing well order with seed {seed}")
        wells = Random(seed).sample(wells, len(wells))

    unplaced = ordered_classes[len(wells) :]
    if unplaced:
        logger.warning(f"{len(unplaced)} classes do not fit in {len(wells)} usable wells")

    layout = pandas.DataFrame(
        None,
        index=list(string.ascii_uppercase[: chosen.rows]),
        columns=_column_labels(chosen, as_text=False),
        dtype=object,
    )
    for class_name, well in zip(ordered_classes, wells, strict=False):
        layout.at[_cell(chosen, well)] = class_name

    return layout, unplaced


def highlight(values: set[str]) -> callable:
    """Styler map: green for cells whose content is in `values`, grey otherwise."""

    def style(cell):
        if cell in values:
            return "background-color: #77dd77; color: black;"
        return "background-color: #f0f2f6;"

    return style


def layout_to_saw(layout: pandas.DataFrame, plate: str = "384") -> dict[str, str]:
    """Read a collector layout back into `{class_name: well}`."""
    numbered = collector(plate).numbered
    return {
        class_name: f"{row}{column}" if numbered else str(row)
        for row, series in layout.iterrows()
        for column, class_name in series.items()
        if class_name and pandas.notna(class_name)
    }


def placement_dataframe(samples_and_wells: dict[str, str], plate: str = "384") -> pandas.DataFrame:
    """The plate as a table of class names, for the CSV in the download bundle."""
    chosen = collector(plate)
    logger.info(f"Building placement table for a {chosen.label}")

    table = pandas.DataFrame(
        "",
        index=list(string.ascii_uppercase[: chosen.rows]),
        columns=_column_labels(chosen, as_text=True),
    )
    for class_name, well in samples_and_wells.items():
        row, column = split_position(well)
        table.at[row, chosen.position if column is None else str(column)] = class_name

    return table


def parse_saw_file(source) -> dict[str, str]:
    """Read a samples-and-wells file written as a Python dict literal.

    Raises:
        SawParseError: unreadable, empty, or not a dictionary.
    """
    logger.info("Parsing samples-and-wells file")
    if isinstance(source, str):
        with open(source, encoding="utf-8-sig") as handle:
            content = handle.read()
    elif hasattr(source, "read"):
        raw = source.read()
        content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    else:
        raise SawParseError(f"Cannot read a samples-and-wells file from {type(source).__name__}")

    if not content.strip():
        raise SawParseError("The file is empty.")

    try:
        parsed = ast.literal_eval(content)
    except (ValueError, SyntaxError) as error:
        raise SawParseError(
            f"Could not read this as a dictionary ({error}). Check for a missing quote, "
            'brace or comma. It should look like {"class_name": "C3", ...}'
        ) from error

    if not isinstance(parsed, dict):
        raise SawParseError(f"The file contains a {type(parsed).__name__}, not a dictionary.")
    if not parsed:
        raise SawParseError("The dictionary is empty.")

    return parsed
