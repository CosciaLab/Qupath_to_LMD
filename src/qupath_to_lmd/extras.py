"""Standalone conveniences that do not touch the collection pipeline."""

import collections
import itertools

from loguru import logger

# QuPath stores class colours as signed 32-bit Java ints.
COLORS = {
    "red": 0xFF0000,
    "green": 0x00FF00,
    "blue": 0x0000FF,
    "magenta": 0xFF00FF,
    "cyan": 0x00FFFF,
    "yellow": 0xFFFF00,
}
JAVA_COLORS = [-(0x1000000 - rgb) for rgb in COLORS.values()]


# Past this many classes QuPath's class list becomes a long scroll to pick from; said, not enforced.
MANY_CLASSES = 500


def parse_values(text: str) -> tuple[list[str], list[str]]:
    """Comma-separated values in the order typed, and the values typed more than once."""
    values: list[str] = []
    repeated: list[str] = []
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        if item in values:
            if item not in repeated:
                repeated.append(item)
        else:
            values.append(item)
    return values, repeated


def combinations(categoricals: list[list[str]]) -> list[tuple[str, ...]]:
    """Every combination of the categoricals, in the order they were given."""
    if not all(isinstance(item, str) for values in categoricals for item in values):
        raise TypeError("Categoricals must be lists of strings")
    return list(itertools.product(*categoricals))


def class_names(combos: list[tuple[str, ...]], replicates: dict[tuple[str, ...], int], default: int) -> list[str]:
    """`value_value_rN` for each combination; `replicates` overrides `default`, 0 leaves one out.

    The `r` keeps the replicate apart from a value that is itself a number, such as a day or a dose.
    """
    if not isinstance(default, int) or default < 1:
        raise ValueError("Replicates must be a positive integer")
    names = [
        f"{'_'.join(combo)}_r{i}"
        for combo in combos
        for i in range(1, replicates.get(combo, default) + 1)
    ]
    logger.info(f"Generated {len(names)} class names")
    return names


def repeated_names(names: list[str]) -> list[str]:
    """Class names that come out more than once, e.g. `a_b` + `c` and `a` + `b_c`."""
    counts = collections.Counter(names)
    return [name for name, count in counts.items() if count > 1]


def build_classes_json(names: list[str]) -> dict:
    """A QuPath `classes.json` payload for a list of class names."""
    return {
        "pathClasses": [
            {"name": name, "color": JAVA_COLORS[i % len(JAVA_COLORS)]} for i, name in enumerate(names)
        ]
    }
