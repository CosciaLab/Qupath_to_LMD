"""Replicates, budgets, and whether a class can supply them."""

import pytest

from qupath_to_lmd import budget, model, stats
from tests.conftest import CELLS_PIXEL_SIZE


def test_group_keys_come_from_budgets_alone():
    """The plate layout is shown before the selection runs, which is only possible because the
    group names depend on the budgets and not on which shapes get chosen."""
    budgets = [budget.ClassBudget("Tumor", 3, 100), budget.ClassBudget("Immune", 2, 100)]
    assert budget.group_keys(budgets) == ["Tumor_r1", "Tumor_r2", "Tumor_r3", "Immune_r1", "Immune_r2"], (
        f"Group keys came out as {budget.group_keys(budgets)}. The plate assignment and the "
        "selection both key off these, so the format matters."
    )
    assert budget.total_groups(budgets) == 5, "One well is needed per replicate per class."


@pytest.mark.parametrize(
    ("mode", "column", "unit"),
    [(budget.BudgetMode.CELLS, "shapes", "shapes"), (budget.BudgetMode.AREA, "area_total_um2", "µm²")],
)
def test_each_mode_points_at_its_own_supply_column(mode, column, unit):
    assert mode.stats_column == column
    assert mode.unit == unit


def test_a_feasible_budget_reports_no_shortfall(cells_gdf):
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(name, 1, 1) for name in table.index]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)
    assert (check[budget.SHORTFALL] == 0).all(), (
        f"One shape per class from a file with {int(table['shapes'].sum())} shapes was reported "
        f"as short: \n{check}"
    )


def test_an_infeasible_budget_reports_the_shortfall_and_what_can_be_filled(cells_gdf):
    """Asking for more than a class holds is a decision to make knowingly. The actionable
    number is how many whole replicates the class can actually fill."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    biggest = table.index[0]
    available = int(table.at[biggest, "shapes"])
    budgets = [budget.ClassBudget(biggest, 4, available)]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)

    row = check.loc[biggest]
    assert row[budget.REQUIRED] == 4 * available
    assert row[budget.SHORTFALL] == 3 * available, (
        f"Asking for 4x the whole class should be short by 3x it; reported {row[budget.SHORTFALL]}."
    )
    assert row[budget.ACHIEVABLE] == 1, (
        f"The class holds exactly one full replicate's worth, so 1 should be fillable; "
        f"reported {row[budget.ACHIEVABLE]}."
    )


def test_an_area_budget_without_a_scale_fails_loudly(cells_gdf):
    """Area budgets need a scale. Failing in the library rather than silently skipping the
    check means the UI cannot show a feasibility table built on nothing."""
    counts_only = stats.class_statistics(cells_gdf, pixel_size_um=None)
    budgets = [budget.ClassBudget(name, 1, 100.0) for name in counts_only.index]
    with pytest.raises(KeyError, match="Area budgets need the image scale"):
        budget.feasibility(counts_only, budgets, budget.BudgetMode.AREA)


def test_a_zero_amount_yields_no_fillable_replicates(cells_gdf):
    """A zero budget collects nothing, and dividing by it must not raise."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(table.index[0], 2, 0)]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)
    assert check.iloc[0][budget.ACHIEVABLE] == 0


def test_display_renames_and_rounds_without_touching_the_replicate_count(cells_gdf):
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(name, 2, 3.14159) for name in table.index]
    display = budget.for_display(budget.feasibility(table, budgets, budget.BudgetMode.CELLS))
    assert budget.DISPLAY_COLUMNS[budget.REPLICATES] in display.columns
    assert display[budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]].iloc[0] == pytest.approx(3.14)
    assert display[budget.DISPLAY_COLUMNS[budget.REPLICATES]].iloc[0] == 2, (
        "Replicate counts must stay exact integers; they are not measurements."
    )


def test_the_table_reports_what_the_size_filter_took(cells_gdf):
    """The share is of the class's own shapes, not of the whole file.

    This column replaced a warning box. If it were computed against the file total, a class
    that lost most of itself would read as a small percentage and the user would sign off on a
    collection with almost nothing in it.
    """
    floors = dict.fromkeys(cells_gdf[model.CLASS_NAME].unique(), 0.0)
    biggest = cells_gdf[model.CLASS_NAME].value_counts().index[0]
    areas = cells_gdf.loc[cells_gdf[model.CLASS_NAME] == biggest].geometry.area
    floors[biggest] = float(areas.median()) * CELLS_PIXEL_SIZE**2

    pool, excluded = stats.filter_by_minimum_area(cells_gdf, floors, CELLS_PIXEL_SIZE)
    table = stats.class_statistics(pool, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(name, 1, 1) for name in floors]

    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS, excluded=excluded)

    dropped = check.at[biggest, budget.FILTERED]
    kept = table.at[biggest, "shapes"]
    expected = 100.0 * dropped / (dropped + kept)
    assert dropped > 0, "A floor at the median area should have removed about half the class."
    assert check.at[biggest, budget.FILTERED_SHARE] == pytest.approx(expected), (
        "The percentage must be of this class's shapes. Computed against another total it "
        "understates the loss and the user under-collects."
    )
    for name in floors:
        if name != biggest:
            assert check.at[name, budget.FILTERED] == 0, (
                f"{name} has no floor, so nothing of it should be reported as filtered."
            )


def test_a_class_filtered_away_entirely_reads_as_empty_not_a_crash(cells_gdf):
    """Raising one class's minimum above every shape in it used to raise KeyError.

    The floor is typed into a table, so it takes one keystroke to exclude a whole class. That
    must show up as zero available, not as a traceback in place of the app.
    """
    name = cells_gdf[model.CLASS_NAME].value_counts().index[0]
    floors = {name: 1e12}
    pool, excluded = stats.filter_by_minimum_area(cells_gdf, floors, CELLS_PIXEL_SIZE)
    table = stats.class_statistics(pool, pixel_size_um=CELLS_PIXEL_SIZE)
    assert name not in table.index, "Fixture check: the class should be gone from the pool."

    check = budget.feasibility(
        table, [budget.ClassBudget(name, 2, 50.0)], budget.BudgetMode.CELLS, excluded=excluded
    )

    assert check.at[name, budget.AVAILABLE] == 0
    assert check.at[name, budget.ACHIEVABLE] == 0
    assert check.at[name, budget.SHORTFALL] == 100.0
    assert check.at[name, budget.FILTERED_SHARE] == 100.0, (
        "Every shape in the class was filtered, so the column should say 100%."
    )


def test_the_filter_columns_are_absent_without_a_scale(cells_gdf):
    """No scale means no size filter, so the columns must not appear as a row of zeros."""
    table = stats.class_statistics(cells_gdf)
    budgets = [budget.ClassBudget(name, 1, 1) for name in cells_gdf[model.CLASS_NAME].unique()]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)
    assert budget.FILTERED not in check.columns
    assert budget.DISPLAY_COLUMNS[budget.FILTERED] not in budget.for_display(check).columns


def test_the_default_amount_is_a_real_experiment_not_the_whole_class():
    """Both workflows start from 3 replicates of 25,000 µm², or 150 cells.

    The old default offered everything a class held, which is never what an experiment wants and
    silently disabled the feasibility check below it: a class can always supply all of itself, so
    the one figure that tells a user whether their plan is possible was always green.
    """
    assert budget.DEFAULT_REPLICATES == 3, (
        "Three replicates is the smallest number that supports a variance estimate; fewer "
        "leaves a user with no way to tell signal from scatter."
    )
    assert budget.BudgetMode.AREA.default_per_replicate == 25_000.0
    assert budget.BudgetMode.CELLS.default_per_replicate == 150


def test_both_workflows_read_the_same_default():
    """One figure, one definition. Two copies drift the moment either is changed."""
    from qupath_to_lmd import packing

    assert packing.DEFAULT_REPLICATES is budget.DEFAULT_REPLICATES
    assert packing.DEFAULT_AREA_PER_REPLICATE_UM2 is budget.DEFAULT_AREA_PER_REPLICATE_UM2
    assert packing.ClassPacking("Tumor").replicates == budget.DEFAULT_REPLICATES, (
        "The regions workflow no longer starts from the shared default."
    )
    assert (
        packing.ClassPacking("Tumor").area_per_replicate_um2
        == budget.BudgetMode.AREA.default_per_replicate
    ), "The two workflows would offer different amounts for the same experiment."


def test_the_default_plan_is_flagged_when_a_class_cannot_supply_it(cells_gdf):
    """The point of a concrete default: the feasibility check can now actually fire.

    The single-cell demo file has 121 cells in its main class, so 3 replicates of 150 is short
    by 329 — and the user is told before they select anything.
    """
    table = stats.class_statistics(cells_gdf)
    budgets = [
        budget.ClassBudget(name, budget.DEFAULT_REPLICATES, budget.BudgetMode.CELLS.default_per_replicate)
        for name in table.index
    ]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)

    short = check[check[budget.SHORTFALL] > 0]
    assert not short.empty, (
        "A file with 121 cells was asked for 450 and no shortfall was reported, so the user "
        "would size a plate for tissue that is not there."
    )
