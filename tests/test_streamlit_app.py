import os
from streamlit.testing.v1 import AppTest

def test_app_loads_and_processes_geojson():
    """
    Test that the Streamlit app loads, accepts a GeoJSON upload,
    and correctly populates the calibration point selectors.
    """
    # Initialize the AppTest with the main script
    at = AppTest.from_file("streamlit_app.py")
    at.run()

    # Ensure no exceptions on startup
    assert not at.exception

    # Define path to the test file
    # Assuming tests are run from the project root
    test_file_path = "tests/data/test.geojson"
    
    # Check if file exists to avoid confusing errors
    assert os.path.exists(test_file_path), f"Test file not found at {test_file_path}"

    # Find the file uploader
    # Based on the app structure, there is one file uploader for the geojson
    file_uploader = at.file_uploader(key=None) # We didn't assign a key, so we search by type
    
    # In case there are multiple, we can check labels or index. 
    # The app has `st.file_uploader(label="Choose a file", ...)`
    # and later `st.file_uploader` for custom saw.
    # The first one should be the geojson one.
    target_uploader = file_uploader[0]
    assert "Choose a file" in target_uploader.label

    # Upload the file
    target_uploader.set_value(test_file_path).run()

    # Assertions after upload
    assert not at.exception

    # 1. Check for the success message from core.load_and_QC_geojson_file
    # "The file QC is complete"
    success_messages = [s.value for s in at.success]
    assert "The file QC is complete" in success_messages

    # 2. Check that session state is updated
    assert at.session_state.file_name == "test.geojson"
    assert at.session_state.gdf is not None
    assert at.session_state.available_points_dict is not None

    # 3. Check for Calibration Point Selectboxes
    # They are created dynamically after upload
    selectboxes = at.selectbox
    calib_labels = [sb.label for sb in selectboxes]
    
    assert "Select calibration point 1" in calib_labels
    assert "Select calibration point 2" in calib_labels
    assert "Select calibration point 3" in calib_labels
