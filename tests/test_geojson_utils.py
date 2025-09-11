import pytest
import sys
import os
import geopandas as gpd
import pandas as pd
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.qupath_to_lmd.geojson_utils import QC_geojson_file, check_ids, process_geojson_with_metadata
from src.qupath_to_lmd.mock_streamlit import patch_streamlit

# Patch streamlit before tests
patch_streamlit()

@pytest.fixture
def multipolygon_geojson_path():
    return Path("tests/data/test_multipolygon.geojson")

@pytest.fixture
def point_geojson_path():
    return Path("tests/data/test_point.geojson")

@pytest.fixture
def missing_classification_geojson_path():
    return Path("tests/data/test_missing_classification.geojson")

@pytest.fixture
def geojson_for_processing_path():
    return Path("tests/data/test.geojson")

@pytest.fixture
def csv_for_processing_path():
    return Path("tests/data/test.csv")

def test_QC_geojson_file_removes_multipolygons(multipolygon_geojson_path):
    """
    Tests that QC_geojson_file removes MultiPolygon geometries.
    """
    df = QC_geojson_file(str(multipolygon_geojson_path))
    assert 'MultiPolygon' not in df.geom_type.unique()

def test_QC_geojson_file_removes_points(point_geojson_path):
    """
    Tests that QC_geojson_file removes Point geometries.
    """
    df = QC_geojson_file(str(point_geojson_path))
    assert 'Point' not in df.geom_type.unique()

def test_QC_geojson_file_removes_missing_classification(missing_classification_geojson_path):
    """
    Tests that QC_geojson_file removes geometries with missing classification.
    """
    df = QC_geojson_file(str(missing_classification_geojson_path))
    assert df['classification'].notna().all()

def test_check_ids():
    """
    Tests the check_ids function.
    """
    # Case 1: All shape names are in metadata
    shapes1 = gpd.GeoDataFrame({'classification_name': ['Tumor', 'Stroma']})
    metadata1 = pd.DataFrame({'name': ['Tumor', 'Stroma', 'Other']})
    assert check_ids(shapes1, metadata1, 'name') == True

    # Case 2: Some shape names are not in metadata
    shapes2 = gpd.GeoDataFrame({'classification_name': ['Tumor', 'Stroma', 'Immune']})
    metadata2 = pd.DataFrame({'name': ['Tumor', 'Stroma']})
    assert check_ids(shapes2, metadata2, 'name') == False

    # Case 3: Shape names have leading/trailing whitespaces
    shapes3 = gpd.GeoDataFrame({'classification_name': [' Tumor ', 'Stroma']})
    metadata3 = pd.DataFrame({'name': ['Tumor', 'Stroma']})
    assert check_ids(shapes3, metadata3, 'name') == True

def test_process_geojson_with_metadata(geojson_for_processing_path, csv_for_processing_path):
    """
    Tests the process_geojson_with_metadata function.
    """
    output_path = f"./{geojson_for_processing_path.name.replace('.geojson', '')}_{'group'}_labelled_shapes.geojson"
    process_geojson_with_metadata(geojson_for_processing_path, csv_for_processing_path, 'name', 'group')

    assert os.path.exists(output_path)

    output_gdf = gpd.read_file(output_path)
    assert not output_gdf.empty
    assert 'name' in output_gdf.columns
    assert 'classification' in output_gdf.columns

    os.remove(output_path)
