"""Per-class descriptive statistics, so a user can see what is available before committing.

Areas are derived from the shape geometry and the user's µm/px, never from QuPath
`measurements` — a typical QuPath export has none (`decisions.md` 029).
"""

import geopandas
import pandas
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME


def class_statistics(
    gdf: geopandas.GeoDataFrame, pixel_size_um: float | None = None
) -> pandas.DataFrame:
    """Summarise every class in the frame, one row per class.

    Args:
        gdf: QC'd shapes carrying `classification_name`.
        pixel_size_um: micrometres per pixel. **Optional** — without it only shape counts are
            returned, because an area in pixels² is not a quantity anyone can reason about
            experimentally. A user budgeting purely by cell count never needs it
            (`decisions.md` 038).

    Returns:
        A numeric frame indexed by class name. Formatting is the caller's business.
    """
    # An empty slide still gets the columns: parent annotations can leave a slide with no shapes, and the
    # class table across slides reads `shapes` from every one.
    counts = gdf.groupby(CLASS_NAME).size().rename("shapes").to_frame()
    if not pixel_size_um:
        logger.info(f"Computing counts only for {len(counts)} classes (no pixel size given)")
        return counts.sort_values("shapes", ascending=False)
    if pixel_size_um <= 0:
        raise ValueError("pixel_size_um must be positive to compute areas")

    logger.info(f"Computing statistics for {gdf[CLASS_NAME].nunique()} classes at {pixel_size_um} µm/px")

    um2_per_px2 = pixel_size_um**2
    frame = pandas.DataFrame(
        {
            CLASS_NAME: gdf[CLASS_NAME].to_numpy(),
            "area_um2": gdf.geometry.area.to_numpy() * um2_per_px2,
        }
    )

    grouped = frame.groupby(CLASS_NAME)["area_um2"]
    stats = pandas.DataFrame(
        {
            "shapes": grouped.size(),
            "area_total_um2": grouped.sum(),
            "area_median_um2": grouped.median(),
            # Sample standard deviation; NaN for a single-shape class, which is honest —
            # one shape has no spread to report.
            "area_std_um2": grouped.std(),
            "area_q1_um2": grouped.quantile(0.25),
            "area_q3_um2": grouped.quantile(0.75),
            "area_min_um2": grouped.min(),
            "area_max_um2": grouped.max(),
        }
    )
    return stats.sort_values("shapes", ascending=False)


DISPLAY_COLUMNS = {
    "shapes": "Shapes",
    "area_total_um2": "Total area (µm²)",
    "area_median_um2": "Median area (µm²)",
    "area_std_um2": "Std. dev. (µm²)",
    "area_q1_um2": "Q1 (µm²)",
    "area_q3_um2": "Q3 (µm²)",
    "area_min_um2": "Min (µm²)",
    "area_max_um2": "Max (µm²)",
}


DECIMALS = 2

# Microdissection cannot reliably collect less tissue than this, whatever a budget asks for.
# Per class, because different biologies genuinely differ in size (`decisions.md` 060).
DEFAULT_MINIMUM_AREA_UM2 = 100.0


def filter_by_minimum_area(
    gdf: geopandas.GeoDataFrame,
    minimum_area_um2: dict[str, float],
    pixel_size_um: float | None,
) -> tuple[geopandas.GeoDataFrame, pandas.Series]:
    """Drop shapes below their class's minimum collectable area.

    Applied **before** anything is measured, so every figure the user then sees — statistics,
    feasibility, what a replicate can hold — describes shapes that can actually be collected.
    Filtering afterwards would show an amount the user cannot have, which is the class of error
    this app exists to prevent (`decisions.md` 060).

    Args:
        gdf: the QC'd shapes.
        minimum_area_um2: floor per class name. A missing class, or a floor of zero, keeps
            everything in that class.
        pixel_size_um: needed to turn pixel areas into µm². Without it nothing is filtered,
            because a floor in µm² is meaningless.

    Returns:
        The surviving shapes, and how many were excluded per class.
    """
    excluded = pandas.Series(0, index=sorted(set(gdf[CLASS_NAME].dropna())), dtype=int)
    if not pixel_size_um or not any(minimum_area_um2.values()):
        return gdf, excluded

    areas = gdf.geometry.area * pixel_size_um**2
    floors = gdf[CLASS_NAME].map(minimum_area_um2).fillna(0.0)
    too_small = areas < floors

    if too_small.any():
        counts = gdf.loc[too_small, CLASS_NAME].value_counts()
        excluded = excluded.add(counts.reindex(excluded.index).fillna(0).astype(int), fill_value=0).astype(int)
        logger.info(f"Minimum area excluded {int(too_small.sum())} shapes: {counts.to_dict()}")

    return gdf[~too_small], excluded


def for_display(stats: pandas.DataFrame) -> pandas.DataFrame:
    """Rename, order and round the columns for showing to a user.

    Areas are rounded to two decimal places: the raw values carry a long float tail that
    implies a precision segmentation boundaries and an entered pixel size do not have.
    Shape counts are left exact — a count is not a measurement.
    """
    columns = [c for c in DISPLAY_COLUMNS if c in stats.columns]
    display = stats[columns].copy()

    area_columns = [c for c in display.columns if c != "shapes"]
    display[area_columns] = display[area_columns].round(DECIMALS)

    return display.rename(columns=DISPLAY_COLUMNS)


# Pixel size is the camera's sensor pitch divided by the total magnification, so it is a
# property of the whole optical path, not of the objective. These two pitches bracket most
# modern scientific cameras and exist to show the user how wide that spread is.
SENSOR_PITCHES_UM = (3.45, 6.5)
OBJECTIVES = (4, 10, 20, 40, 63)


def reference_pixel_sizes() -> pandas.DataFrame:
    """Indicative µm/px per objective, for users who know their magnification but not their scale.

    Purely a sanity-check aid. The authoritative value is in QuPath under
    *Image → Image properties → Pixel width*.
    """
    return pandas.DataFrame(
        {f"{pitch} µm sensor": [round(pitch / mag, 3) for mag in OBJECTIVES] for pitch in SENSOR_PITCHES_UM},
        index=pandas.Index([f"{mag}×" for mag in OBJECTIVES], name="Objective"),
    )
