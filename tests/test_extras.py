"""The QuPath classes generator on the Extras page: categoricals in, class names out."""

import pytest

from qupath_to_lmd import extras


def test_values_are_split_on_commas_and_trimmed_in_the_order_typed():
    values, repeated = extras.parse_values(" tumour, stroma ,, immune ")
    assert values == ["tumour", "stroma", "immune"], "Typed values were lost or reordered, so class names would not match what the user typed."
    assert repeated == []


def test_a_value_typed_twice_is_kept_once_and_reported():
    values, repeated = extras.parse_values("control, drug, control")
    assert values == ["control", "drug"], "A repeated value would make every one of its classes twice."
    assert repeated == ["control"], "The page could not tell the user which value was merged."


def test_any_number_of_categoricals_combine_in_the_order_given():
    combos = extras.combinations([["a", "b"], ["x"], ["1", "2", "3"]])
    assert len(combos) == 6, "Three categoricals did not give every combination."
    assert combos[0] == ("a", "x", "1") and combos[-1] == ("b", "x", "3"), (
        "Names would not follow the order the user typed the categoricals in."
    )


def test_one_categorical_is_enough():
    combos = extras.combinations([["tumour", "stroma"]])
    names = extras.class_names(combos, {}, default=2)
    assert names == ["tumour_r1", "tumour_r2", "stroma_r1", "stroma_r2"], (
        "A single categorical with replicates no longer produces classes."
    )


def test_the_replicate_number_is_marked_with_an_r():
    """`_r1` rather than `_1`, so a replicate cannot be mistaken for a value that is itself a number."""
    combos = extras.combinations([["celltype_A", "celltype_B"], ["control", "drug_treated"]])
    names = extras.class_names(combos, {}, default=2)
    assert names[:4] == [
        "celltype_A_control_r1",
        "celltype_A_control_r2",
        "celltype_A_drug_treated_r1",
        "celltype_A_drug_treated_r2",
    ], "Class names no longer read value_value_rN, so they would not match what the page shows."


def test_a_combination_can_have_its_own_replicate_count_or_be_left_out():
    combos = extras.combinations([["tumour", "stroma"], ["control", "drug"]])
    names = extras.class_names(combos, {("tumour", "drug"): 4, ("stroma", "control"): 0}, default=2)
    assert [n for n in names if n.startswith("tumour_drug")] == [f"tumour_drug_r{i}" for i in range(1, 5)], (
        "A combination given 4 replicates did not get 4 classes numbered 1 to 4."
    )
    assert not any(n.startswith("stroma_control") for n in names), (
        "A combination set to 0 still made classes, so the user cannot leave it out."
    )
    assert len(names) == 2 + 4 + 2, "Untouched combinations did not keep the default replicate count."


def test_names_that_come_out_the_same_are_reported():
    combos = extras.combinations([["a_b", "a"], ["c", "b_c"]])
    names = extras.class_names(combos, {}, default=1)
    assert extras.repeated_names(names) == ["a_b_c_r1"], (
        "Two combinations that give the same class name went unnoticed; QuPath would hold one class for both."
    )


@pytest.mark.parametrize("default", [0, -1])
def test_the_default_replicate_count_must_be_positive(default):
    with pytest.raises(ValueError):
        extras.class_names([("a",)], {}, default=default)
