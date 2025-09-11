import pytest
import os
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.qupath_to_lmd.st_cached import create_collection, load_and_QC_geojson_file, load_and_QC_SamplesandWells
from src.qupath_to_lmd.mock_streamlit import patch_streamlit

# Patch streamlit before tests
patch_streamlit()

@pytest.fixture
def geojson_path():
    return "tests/data/test.geojson"

@pytest.fixture
def low_intersection_geojson_path():
    return "tests/data/test_low_intersection.geojson"

@patch('matplotlib.pyplot.show')
def test_create_collection(mock_show, geojson_path):
    """
    Tests the create_collection function.
    """
    calib_points = ['calib1', 'calib2', 'calib3']
    samples_and_wells = "{'Tumor': 'C3', 'Stroma': 'D4'}"

    # Ensure the output files don't exist before running
    xml_output = f'./{Path(geojson_path).name.replace("geojson", "xml")}'
    csv_output = f'./{Path(geojson_path).name.replace("geojson", "_384_wellplate.csv")}'
    if os.path.exists(xml_output):
        os.remove(xml_output)
    if os.path.exists(csv_output):
        os.remove(csv_output)

    create_collection(Path(geojson_path), calib_points, samples_and_wells)

    # Check if the output files were created
    assert os.path.exists(xml_output)
    assert os.path.exists(csv_output)

    # Clean up the created files
    os.remove(xml_output)
    os.remove(csv_output)
    if os.path.exists('./TheCollection.png'):
        os.remove('./TheCollection.png')

@pytest.fixture
def low_intersection_geojson_path():
    return "tests/data/test_low_intersection.geojson"

def test_load_and_QC_geojson_file_success(geojson_path):
    """
    Tests a successful run of load_and_QC_geojson_file.
    """
    # This test just checks that the function runs without raising an exception
    load_and_QC_geojson_file(geojson_path, list_of_calibpoint_names=['calib1', 'calib2', 'calib3'])

def test_load_and_QC_geojson_file_missing_calib_points(geojson_path):
    """
    Tests that load_and_QC_geojson_file raises SystemExit when calibration points are missing.
    """
    with pytest.raises(SystemExit):
        load_and_QC_geojson_file(geojson_path, list_of_calibpoint_names=['calib4', 'calib5', 'calib6'])

def test_load_and_QC_geojson_file_low_intersection(low_intersection_geojson_path, capsys):
    """
    Tests that load_and_QC_geojson_file prints a warning for low intersection.
    """
    load_and_QC_geojson_file(low_intersection_geojson_path, list_of_calibpoint_names=['calib1', 'calib2', 'calib3'])
    captured = capsys.readouterr()
    assert "WARNING: Less than 25% of the objects intersect with the calibration triangle" in captured.out

def test_load_and_QC_SamplesandWells_success(geojson_path):
    """
    Tests a successful run of load_and_QC_SamplesandWells.
    """
    samples_and_wells = "{'Tumor': 'C3', 'Stroma': 'D4'}"
    load_and_QC_SamplesandWells(geojson_path, ['calib1', 'calib2', 'calib3'], samples_and_wells)

def test_load_and_QC_SamplesandWells_invalid_well(geojson_path, capsys):
    """
    Tests that load_and_QC_SamplesandWells prints a warning for invalid well names.
    """
    samples_and_wells = "{'Tumor': 'A1', 'Stroma': 'D4'}"
    load_and_QC_SamplesandWells(geojson_path, ['calib1', 'calib2', 'calib3'], samples_and_wells)
    captured = capsys.readouterr()
    assert "Your well A1 is not in the list of acceptable wells for 384 well plate, please correct it" in captured.out

def test_load_and_QC_SamplesandWells_missing_name(geojson_path, capsys):
    """
    Tests that load_and_QC_SamplesandWells prints a warning for missing geometry names.
    """
    samples_and_wells = "{'Tumor': 'C3'}"
    load_and_QC_SamplesandWells(geojson_path, ['calib1', 'calib2', 'calib3'], samples_and_wells)
    captured = capsys.readouterr()
    assert 'Your name "Stroma" is not in the list of samples_and_wells' in captured.out
