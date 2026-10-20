"""Quality checks. These report; they do not decide. The UI layer chooses what to do."""

import re
import string
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePath

import geopandas
import numpy
import shapely
from loguru import logger

from qupath_to_lmd.geojson import loose_class_key
from qupath_to_lmd.plate import acceptable_wells


@dataclass
class TriangleReport:
    """How much of the tissue sits inside the calibration triangle."""

    calibration_array: numpy.ndarray
    n_shapes: int = 0
    n_intersecting: int = 0
    triangle_area: float = 0.0

    @property
    def fraction_inside(self) -> float:
        """Share of cuttable shapes intersecting the calibration triangle."""
        return self.n_intersecting / self.n_shapes if self.n_shapes else 0.0

    @property
    def is_concerning(self) -> bool:
        """Shapes far outside the triangle get warped by the coordinate transform."""
        return self.n_shapes > 0 and self.fraction_inside < 0.25

    @property
    def is_degenerate(self) -> bool:
        """The three points do not form a triangle, so no coordinate transform exists.

        Happens when the same point is picked more than once, or when all three are
        collinear. py-lmd does not complain — it writes a perfectly well-formed XML whose
        coordinates are meaningless — so this has to be caught here.
        """
        return self.triangle_area <= 0


@dataclass
class SawReport:
    """Mismatches between a samples-and-wells scheme, the shapes, and the plate."""

    missing_classes: set[str] = field(default_factory=set)
    invalid_wells: set[str] = field(default_factory=set)
    duplicate_wells: dict[str, list[str]] = field(default_factory=dict)
    plate: str = "384"

    @property
    def is_usable(self) -> bool:
        """Wells that do not exist would send tissue nowhere; everything else is a warning."""
        return not self.invalid_wells


def triangle_qc(
    gdf: geopandas.GeoDataFrame,
    calibration_points: dict[str, list[float]],
    selected_names: list[str],
) -> TriangleReport:
    """Check how many shapes fall inside the triangle formed by the chosen calibration points.

    Raises:
        KeyError: a selected name is not in the calibration-point pool.
    """
    logger.info(f"Triangle QC for calibration points {selected_names}")
    missing = [name for name in selected_names if name not in calibration_points]
    if missing:
        raise KeyError(f"Calibration point(s) not found in the file: {missing}")

    calibration_array = numpy.array([calibration_points[name] for name in selected_names])
    triangle = shapely.Polygon(calibration_array)

    cuttable = gdf[gdf.geometry.geom_type.isin(["Polygon", "LineString"])]
    report = TriangleReport(
        calibration_array=calibration_array,
        n_shapes=len(cuttable),
        n_intersecting=int(cuttable.geometry.intersects(triangle).sum()),
        triangle_area=float(triangle.area),
    )

    if report.is_degenerate:
        logger.error(f"Calibration points {selected_names} do not form a triangle")
        return report

    logger.info(f"{report.fraction_inside * 100:.2f}% of shapes intersect the calibration triangle")
    if report.is_concerning:
        logger.warning("Under 25% of shapes are inside the calibration triangle; shapes may be warped")

    return report


def validate_saw(
    samples_and_wells: dict[str, str],
    shape_classes: list[str],
    plate: str = "384",
) -> SawReport:
    """Check a samples-and-wells scheme against the shapes and the chosen plate."""
    logger.info(f"Validating samples-and-wells against a {plate} well plate")
    if not isinstance(samples_and_wells, dict):
        raise TypeError("samples and wells must be a dictionary")

    report = SawReport(plate=plate)
    # Full plate, no margin: margins are a collection-quality preference, not a hard limit,
    # so a user deliberately using an edge well should not be blocked.
    report.invalid_wells = set(samples_and_wells.values()) - set(acceptable_wells(plate=plate, margins=0))
    report.missing_classes = set(shape_classes) - set(samples_and_wells)

    by_well: dict[str, list[str]] = {}
    for class_name, well in samples_and_wells.items():
        by_well.setdefault(well, []).append(class_name)
    report.duplicate_wells = {well: names for well, names in by_well.items() if len(names) > 1}

    if report.invalid_wells:
        logger.error(f"Wells that do not exist on a {plate} well plate: {report.invalid_wells}")
    if report.missing_classes:
        logger.warning(f"Classes present in the shapes but absent from samples and wells: {report.missing_classes}")
    if report.duplicate_wells:
        logger.warning(f"Wells receiving more than one class: {report.duplicate_wells}")

    return report


@dataclass
class PixelSizeReport:
    """Cross-check of the user's µm/px against what QuPath's own measurements imply."""

    entered_um_per_px: float
    n_objects_checked: int = 0
    implied_um_per_px: float | None = None
    relative_spread: float | None = None

    @property
    def ratio(self) -> float | None:
        """Entered value divided by the implied one. 1.0 means agreement."""
        if not self.implied_um_per_px:
            return None
        return self.entered_um_per_px / self.implied_um_per_px

    @property
    def is_concerning(self) -> bool:
        """More than 5% apart. A 10x typo turns an area budget into a different experiment."""
        return self.ratio is not None and abs(self.ratio - 1) > 0.05


def compare_pixel_size(
    entered_um_per_px: float,
    implied_um_per_px: float | None,
    n_objects: int = 0,
    relative_spread: float | None = None,
) -> PixelSizeReport:
    """Compare an entered µm/px against the scale QuPath's measurements implied.

    The implied value is computed once when the file is read (`geojson.implied_pixel_size`),
    so this is pure arithmetic and costs nothing on a rerun. Reports only — the entered value
    is never overwritten (`decisions.md` 011).
    """
    report = PixelSizeReport(
        entered_um_per_px=entered_um_per_px,
        implied_um_per_px=implied_um_per_px,
        n_objects_checked=n_objects,
        relative_spread=relative_spread,
    )

    if implied_um_per_px is None:
        logger.info("No area measurements in this file, cannot cross-check pixel size")
        return report

    logger.info(
        f"Pixel size: entered {entered_um_per_px} µm/px, QuPath's areas imply "
        f"{implied_um_per_px:.4f} over {n_objects} objects"
    )
    if report.is_concerning:
        logger.warning(f"Entered pixel size is {report.ratio:.2f}x the implied value")

    return report


# A slide's file name is reused three times in the longest path of an experiment download once
# Windows unzips it: the folder the zip extracts into, the slide's folder, and its `.xml`
# (`<name>_experiment/slide_<name>/<name>__TubeHolder1.xml`). With about 70 characters for the
# user's Downloads folder and the fixed parts, three names of 60 stay inside Windows' 260-character
# path limit, past which Explorer refuses to extract a file. The LMD computers run Windows.
LONGEST_SAFE_FILE_NAME = 60

# Letters, digits and the punctuation file names commonly use. `%`, `#`, `&` and the like are read
# specially by URLs, shells and Windows batch files, and accented letters can be garbled by older
# Windows zip extraction, which ignores the zip's UTF-8 flag.
SAFE_FILE_NAME_CHARACTERS = frozenset(string.ascii_letters + string.digits + " _-.()")


@dataclass
class FileNameReport:
    """Whether an uploaded file's name is safe to reuse for the files in the download."""

    file_name: str
    stem: str
    unusual_characters: list[str] = field(default_factory=list)

    @property
    def too_long(self) -> bool:
        """Long enough that the download's paths can pass Windows' 260-character limit."""
        return len(self.stem) > LONGEST_SAFE_FILE_NAME

    @property
    def padded(self) -> bool:
        """Spaces at either end, which Windows strips from folder names when it unzips."""
        return self.stem != self.stem.strip()

    @property
    def is_concerning(self) -> bool:
        """Any reason the name may not survive the trip to the LMD computer."""
        return self.too_long or self.padded or bool(self.unusual_characters)

    @property
    def suggestion(self) -> str:
        """A name with the same meaning that passes every check, for the user to rename to."""
        ascii_only = unicodedata.normalize("NFKD", self.stem).encode("ascii", "ignore").decode()
        safe = "".join(c if c in SAFE_FILE_NAME_CHARACTERS else "_" for c in ascii_only)
        safe = re.sub(r"_+", "_", re.sub(r" +", " ", safe)).strip(" _-.")
        if len(safe) > LONGEST_SAFE_FILE_NAME:
            cut = safe[: LONGEST_SAFE_FILE_NAME + 1]
            # end on a whole word where one ends near the limit, rather than mid-word
            boundary = max(cut.rfind(" "), cut.rfind("_"), cut.rfind("-"))
            safe = cut[:boundary] if boundary > LONGEST_SAFE_FILE_NAME * 2 // 3 else cut[:-1]
        return safe.rstrip(" _-.") or "slide"


def file_name_qc(file_name: str) -> FileNameReport:
    """Check an uploaded file's name before it becomes the name of an `.xml` and its folders.

    Reports only: the app carries on with the name as it is, and the user decides whether to
    rename the file and upload it again.
    """
    stem = PurePath(file_name).stem
    unusual = sorted({c for c in stem if c not in SAFE_FILE_NAME_CHARACTERS})
    report = FileNameReport(file_name=file_name, stem=stem, unusual_characters=unusual)
    if report.is_concerning:
        logger.warning(
            f"File name {file_name!r}: {len(stem)} characters, unusual characters {unusual}, "
            f"padded {report.padded}; suggested {report.suggestion!r}"
        )
    return report


def similar_class_names(classes_by_slide: dict[str, list[str]]) -> list[dict[str, list[str]]]:
    """Groups of class names that differ only in capitals, spaces, `_` or `-`.

    The app matches class names exactly — that is what decides which shapes share a well — so
    `Tumor` and `tumor`, or `Immune cells` and `Immune_cells`, are collected as two classes into
    separate wells, and on several slides they do not pool. Usually that is a typo in QuPath, so
    it is worth a warning; it is not merged, because only the user knows.

    Returns:
        One `{class name: [slides holding it]}` per group of two or more spellings.
    """
    groups: dict[str, dict[str, list[str]]] = {}
    for slide, classes in classes_by_slide.items():
        for name in classes:
            groups.setdefault(loose_class_key(name), {}).setdefault(name, []).append(slide)
    similar = [dict(sorted(group.items())) for _key, group in sorted(groups.items()) if len(group) > 1]
    if similar:
        logger.warning(f"Class names that differ only in case, spaces, _ or -: {[list(g) for g in similar]}")
    return similar
