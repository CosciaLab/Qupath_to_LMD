"""Replicates and per-replicate budgets, and whether a class can actually supply them.

Feasibility is reported, never enforced: a user may knowingly ask for more than a class
holds and accept a partially-filled replicate (`decisions.md` 003).
"""

from dataclasses import dataclass
from enum import Enum

import pandas
from loguru import logger

# Column names of the feasibility table, so the UI and the tests agree on them.
REPLICATES = "replicates"
PER_REPLICATE = "per_replicate"
REQUIRED = "required"
AVAILABLE = "available"
SHORTFALL = "shortfall"
ACHIEVABLE = "achievable_replicates"
FILTERED = "filtered_by_area"
FILTERED_SHARE = "filtered_share"


# What a DVP experiment is normally designed around. Three replicates is the smallest number
# that supports a variance estimate; 25 000 µm² and 150 cells are the per-well amounts in use.
# Shared by both workflows that collect into replicates, so the figure is one fact rather than
# two that can drift apart (`decisions.md` 073).
DEFAULT_REPLICATES = 3
DEFAULT_AREA_PER_REPLICATE_UM2 = 25_000.0
DEFAULT_CELLS_PER_REPLICATE = 150


class BudgetMode(str, Enum):
    """What a budget counts."""

    CELLS = "cells"
    AREA = "area"

    @property
    def unit(self) -> str:
        """How the amount is spelled in the interface."""
        return "shapes" if self is BudgetMode.CELLS else "µm²"

    @property
    def stats_column(self) -> str:
        """Which `stats.class_statistics` column holds the supply for this mode."""
        return "shapes" if self is BudgetMode.CELLS else "area_total_um2"

    @property
    def default_per_replicate(self) -> float:
        """How much to offer per replicate before the user changes anything.

        A concrete amount rather than "everything this class holds". The old default asked for
        the whole class, which is never what an experiment wants and hid the feasibility check —
        a class can always supply all of itself, so nothing was ever flagged.
        """
        return (
            DEFAULT_CELLS_PER_REPLICATE
            if self is BudgetMode.CELLS
            else DEFAULT_AREA_PER_REPLICATE_UM2
        )


@dataclass(frozen=True)
class ClassBudget:
    """How much of one class to collect, and across how many replicates."""

    class_name: str
    replicates: int
    per_replicate: float

    @property
    def required(self) -> float:
        """Total demanded across every replicate of this class."""
        return self.replicates * self.per_replicate


def feasibility(
    stats: pandas.DataFrame,
    budgets: list[ClassBudget],
    mode: BudgetMode,
    excluded: pandas.Series | None = None,
) -> pandas.DataFrame:
    """Compare what each class is asked for against what it holds.

    Args:
        stats: output of `stats.class_statistics` for the shapes still in play, indexed by
            class name. A class filtered away entirely is simply absent, and counts as zero
            available rather than an error.
        budgets: one entry per class the user is collecting.
        mode: whether budgets count shapes or µm².
        excluded: how many shapes each class lost to its minimum area, from
            `stats.filter_by_minimum_area`. Given, two more columns report what the size filter
            took, which is how that gets communicated instead of a block of prose
            (`decisions.md` 065).

    Returns:
        A frame indexed by class with the request, the supply, any shortfall, and how many
        whole replicates the class can actually fill.

    Raises:
        KeyError: `stats` lacks the column this mode needs — for area budgets that means no
            pixel size was given.
    """
    if mode.stats_column not in stats.columns and not stats.empty:
        raise KeyError(
            f"Cannot check an {mode.value} budget: '{mode.stats_column}' is missing. "
            "Area budgets need the image scale."
        )

    rows = {}
    for item in budgets:
        available = (
            float(stats.at[item.class_name, mode.stats_column])
            if item.class_name in stats.index
            else 0.0
        )
        required = item.required
        row = {
            REPLICATES: item.replicates,
            PER_REPLICATE: item.per_replicate,
            REQUIRED: required,
            AVAILABLE: available,
            SHORTFALL: max(0.0, required - available),
            ACHIEVABLE: int(available // item.per_replicate) if item.per_replicate > 0 else 0,
        }
        if excluded is not None:
            dropped = int(excluded.get(item.class_name, 0))
            kept = (
                int(stats.at[item.class_name, "shapes"])
                if item.class_name in stats.index and "shapes" in stats.columns
                else 0
            )
            row[FILTERED] = dropped
            row[FILTERED_SHARE] = 100.0 * dropped / (dropped + kept) if dropped + kept else 0.0
        rows[item.class_name] = row

    table = pandas.DataFrame.from_dict(rows, orient="index")
    table.index.name = stats.index.name
    short = table[table[SHORTFALL] > 0]
    if not short.empty:
        logger.warning(f"{len(short)} classes cannot supply their budget: {list(short.index)}")

    return table


def group_keys(budgets: list[ClassBudget]) -> list[str]:
    """The `class_r<replicate>` key of every group the plan will produce.

    Depends only on the budgets, not on which shapes end up selected, so the plate layout can
    be shown before the selection runs.
    """
    return [
        f"{item.class_name}_r{replicate}"
        for item in budgets
        for replicate in range(1, item.replicates + 1)
    ]


def total_groups(budgets: list[ClassBudget]) -> int:
    """One well per replicate per class, so this is how many wells the plan needs."""
    return sum(item.replicates for item in budgets)


DISPLAY_COLUMNS = {
    REPLICATES: "Replicates",
    PER_REPLICATE: "Per replicate",
    REQUIRED: "Total requested",
    AVAILABLE: "Available",
    SHORTFALL: "Short by",
    ACHIEVABLE: "Replicates fillable",
    FILTERED: "Too small to collect",
    FILTERED_SHARE: "% too small",
}


def for_display(table: pandas.DataFrame, decimals: int = 2) -> pandas.DataFrame:
    """Rename and round the feasibility table for showing to a user."""
    columns = [c for c in DISPLAY_COLUMNS if c in table.columns]
    display = table[columns].copy()
    for column in (PER_REPLICATE, REQUIRED, AVAILABLE, SHORTFALL, FILTERED_SHARE):
        if column in display.columns:
            display[column] = display[column].round(decimals)
    return display.rename(columns=DISPLAY_COLUMNS)
