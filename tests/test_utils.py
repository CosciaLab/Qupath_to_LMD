import pytest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.qupath_to_lmd.utils import create_list_of_acceptable_wells, create_default_samples_and_wells

def test_create_list_of_acceptable_wells():
    """
    Tests the create_list_of_acceptable_wells function.
    """
    wells = create_list_of_acceptable_wells()
    assert isinstance(wells, list)
    assert len(wells) == 228  # 12 rows (C-N) * 19 columns (3-21)
    assert wells[0] == 'C3'
    assert wells[-1] == 'N21'

def test_create_default_samples_and_wells():
    """
    Tests the create_default_samples_and_wells function.
    """
    samples = ['sample1', 'sample2']
    wells = ['A1', 'A2', 'A3']
    
    # Test basic functionality
    result = create_default_samples_and_wells(samples, wells)
    assert isinstance(result, dict)
    assert len(result) == 2
    assert result['sample1'] == 'A1'
    assert result['sample2'] == 'A2'

    # Test assertion for more samples than wells
    with pytest.raises(AssertionError):
        create_default_samples_and_wells(['s1', 's2', 's3', 's4'], wells)
