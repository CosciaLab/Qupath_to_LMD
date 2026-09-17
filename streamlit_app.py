import sys
import tempfile
import uuid

import streamlit as st
from loguru import logger

from qupath_to_lmd import ui_cells, ui_legacy, ui_packing, ui_shared

####################
## Page settings ###
####################
st.set_page_config(layout="wide")

DEFAULTS = {
    "session_id": None,          # set below, needs a fresh uuid
    "workflow": "legacy",
    "view_mode": "default",
    "gdf": None,
    "geojson_report": None,
    "calibration_points": None,
    "calibs": None,
    "calib_array": None,
    "pixel_size_um": None,
    "selected_classes": None,
    "budget_mode": None,
    "budgets": None,
    "minimum_area_um2": None,
    "region_params": None,
    "saw": None,
    "use_plate_wells": True,
    "file_name": None,
    "plate_df": None,
    "plate_gen_params": None,
    "show_saw_uploader": False,
    "zip_buffer": None,
    "bundle_name": None,
    "collection_image": None,
    "log_file_path": None,
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value
if st.session_state.session_id is None:
    st.session_state.session_id = str(uuid.uuid4())

# Configure logging. The log file ships inside the download bundle, so it is part of the
# support story: a user can send it with a Github issue.
if st.session_state.log_file_path is None:
    st.session_state.log_file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".log").name

logger.remove()
logger.add(st.session_state.log_file_path, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")
logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}", level="DEBUG")

####################
### Introduction ###
####################
st.markdown("# Turn QuPath shapes into a Laser Microdissection cutting file")
st.markdown(
    "[openDVP framework](https://github.com/CosciaLab/openDVP) · "
    "[Report an issue](https://github.com/CosciaLab/Qupath_to_LMD/issues) · "
    "[Glossary](https://github.com/CosciaLab/Qupath_to_LMD/blob/master/GLOSSARY.md)"
)
st.caption(
    f"Session id `{st.session_state.session_id}` — include it, and your .geojson, when "
    "reporting an issue."
)
st.divider()

#################################
### Shared steps, then router ###
#################################

uploaded_file = ui_shared.upload_step()
st.divider()

workflow = ui_shared.workflow_step()
st.divider()

ui_shared.calibration_step()
st.divider()

if workflow == "cells":
    ui_cells.render(uploaded_file)
elif workflow == "regions":
    ui_packing.render(uploaded_file)
else:
    ui_legacy.render(uploaded_file)

st.divider()
st.divider()

#######################
####### EXTRAS ########
#######################

ui_shared.extras_step()
st.divider()
