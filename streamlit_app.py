import io
import json
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import streamlit as st
from loguru import logger

import qupath_to_lmd.core as core
import qupath_to_lmd.utils as utils

####################
## Page settings ###
####################
st.set_page_config(layout="wide")
if 'session_id' not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if 'view_mode' not in st.session_state:
   st.session_state.view_mode = 'default'
if 'gdf' not in st.session_state:
   st.session_state.gdf = None
if 'calibs' not in st.session_state:
   st.session_state.calibs = None
if 'calib_array' not in st.session_state:
   st.session_state.calib_array = None
if 'saw' not in st.session_state:
   st.session_state.saw = None
if "use_plate_wells" not in st.session_state:
   st.session_state.use_plate_wells = True
if 'file_name' not in st.session_state:
   st.session_state.file_name = None
if 'xml_content' not in st.session_state:
   st.session_state.xml_content = None
if 'csv_content' not in st.session_state:
   st.session_state.csv_content = None
if 'zip_buffer' not in st.session_state:
    st.session_state.zip_buffer = None
if 'plate_df' not in st.session_state:
   st.session_state.plate_df = None
if 'plate_gen_params' not in st.session_state:
   st.session_state.plate_gen_params = None
if 'show_saw_uploader' not in st.session_state:
   st.session_state.show_saw_uploader = False

# Configure logging
if "log_file_path" not in st.session_state or st.session_state.log_file_path is None:
   temp_log_file = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
   st.session_state.log_file_path = temp_log_file.name

logger.remove()
logger.add(st.session_state.log_file_path, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")
logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}", level="DEBUG")

####################
### Introduction ###
####################
st.markdown("""
            # Convert a GeoJSON polygons for Laser Microdissection
            ## Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework
            ### For help, post issue on [Github](https://github.com/CosciaLab/Qupath_to_LMD) with .geojson file and session id
            """)
st.write(f" Session id: {st.session_state.session_id}")
st.divider()

############################
## Step 1: Geojson upload ##
############################

st.markdown("""
            ## Step 1: Upload and check .geojson file from Qupath
            Upload your .geojson file from qupath, order of calibration points is important
            """)

uploaded_file = st.file_uploader(label="Choose a file", type="geojson", accept_multiple_files=False)
calibration_point_1 = st.text_input("Enter the name of the first calibration point: ",  placeholder ="first_calib")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ", placeholder ="second_calib")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ",  placeholder ="third_calib")
list_of_calibpoint_names = [calibration_point_1, calibration_point_2, calibration_point_3]

if st.button("Load and check the geojson file"):
   logger.info("Load and check geojson file button clicked")
   if uploaded_file:
      # process calibs
      st.session_state.calibs = [calibration_point_1, calibration_point_2, calibration_point_3]
      st.session_state.file_name = uploaded_file.name
      logger.debug(f"File name: {st.session_state.file_name}")
      # process and QC geojson
      st.session_state.gdf = core.load_and_QC_geojson_file(geojson_path=uploaded_file)
   else:
      st.warning("Please upload a geojson file.")
      logger.warning("No geojson file uploaded")

st.divider()

##########################################################
## Step 1.1 (Optional): Split a class into many classes ##
##########################################################

if st.session_state.gdf is not None:
   st.markdown("## Step 1.1 (Optional): Split a class into many classes")
   st.markdown(
      "For one or more classes below. For every shape belonging to a selected class, "
      "a unique, numbered ID will be created (e.g., 'T-Cell' -> 'T-Cell_001', 'T-Cell_002'). "
      "This is useful for single-cell collection."
   )

   all_classes = st.session_state.gdf['classification_name'].unique().tolist()
   classes_to_make_unique = st.multiselect("Select classes to make unique:", options=all_classes)

   if st.button("Generate Unique Names"):
      logger.info("Generate Unique Names button clicked")
      if not classes_to_make_unique:
         st.warning("Please select at least one class to make unique.")
         logger.warning("No classes selected to make unique")
      else:
         logger.debug(f"Classes to make unique: {classes_to_make_unique}")
         # This function modifies st.session_state.gdf
         core.make_classes_unique(classes_to_make_unique)
         st.session_state.saw = None
         st.session_state.plate_df = None
         st.info("Chosen classes were split up, check below.")

st.divider()

########################################
## Step 2: Choose collection settings ##
########################################

st.markdown("""
            ## Step 2: Decide which plate to collect into, either 384 or 96 well plate.  
            Decide how many wells to make unavailable as a margin (for 384wp we suggest a margin of 2).  
            Decide how many wells to leave blank in between, for easier pipetting.  
            """)

st.write("You can increase plate size by dragging bottom right corner")

plate, margin, step_row, step_col = st.columns(4)
with plate:
   plate_string = st.selectbox('Select a plate type',('384 well plate', '96 well plate'))
with margin:
   margin_int = st.number_input('Margin (integer)', min_value=0, max_value=10, value=1)
with step_row:
   step_row_int = st.number_input('Space between rows', min_value=1, max_value=10, value=1)
with step_col:
   step_col_int = st.number_input('Space between columns', min_value=1, max_value=10, value=1)

plate_type = plate_string.split(' ')[0]
acceptable_wells_list = utils.create_list_of_acceptable_wells(
   plate=plate_type, margins=margin_int, step_row=step_row_int, step_col=step_col_int)
acceptable_wells_set = set(acceptable_wells_list)

#####################################
## Step 2.1: User inputs for plate ##
#####################################

col1, col2, col3 = st.columns(3)
with col1:
   if st.button("Show plate format with default wells"):
      st.session_state.view_mode = 'default'
      logger.info("Show plate format with default wells -- ButtonPress")
with col2:
   if st.button("Show plate format with samples from geojson"):
      logger.info("Show plate format with samples from geojson -- ButtonPress")
      if uploaded_file is None:
         st.warning("Please upload a file first.")
      else:
         st.session_state.view_mode = 'samples'
with col3:
   randomize_toggle = st.toggle("Randomize samples", value=False)

# --- Logic to decide if we need to regenerate the plate dataframe ---
plate_gen_params = {
    "plate_type": plate_type,
    "margins": margin_int,
    "step_row": step_row_int,
    "step_col": step_col_int,
    "randomize": randomize_toggle,
}

# Check if parameters have changed since the last run, or if the df is missing
params_have_changed = st.session_state.get('plate_gen_params') != plate_gen_params
df_missing = 'plate_df' not in st.session_state or st.session_state.plate_df is None

##############################
## Step 2.2 Plot dataframe ##
##############################

if st.session_state.view_mode == 'default':
   df = utils.create_dataframe_samples_wells(plate_string=plate_type)
   mapping = utils.provide_highlighting_for_df(acceptable_wells_set=acceptable_wells_set)
   st.dataframe(df.style.map(mapping), width="stretch" )

elif st.session_state.view_mode == 'samples':
   if uploaded_file is None:
      st.warning("File no longer available. Please upload a file or switch to the default view.")
      st.session_state.view_mode = 'none'
   else:
      # Only regenerate the dataframe if parameters have changed or it doesn't exist
      if (params_have_changed or df_missing) and st.session_state.gdf is not None:
         st.session_state.plate_gen_params = plate_gen_params # Store the new params
         st.session_state.plate_df = utils.create_dataframe_samples_wells(
            randomize = randomize_toggle,
            plate_string = plate_type,
            acceptable_wells_list = acceptable_wells_list)
      if st.session_state.plate_df is not None:
         mapping = utils.provide_highlighting_for_df()
         st.dataframe(st.session_state.plate_df.style.map(mapping), width="stretch")

if st.button("Confirm and use this plate layout"):
   logger.info("Confirm and ue this plate layout -- ButtonPress")
   if st.session_state.view_mode == 'samples' and st.session_state.plate_df is not None:
      saw_from_df = utils.dataframe_to_saw_dict(st.session_state.plate_df)
      st.session_state.saw = saw_from_df
      core.load_and_QC_SamplesandWells(st.session_state.saw)
      st.session_state.use_plate_wells = True # To indicate we are using a plate layout
      st.success("Samples and wells layout confirmed, you are ready for Step 3!")
      with st.expander("View Samples and Wells Dictionary", expanded=False):
         st.write(st.session_state.saw) # Show the user the resulting dictionary
   else:
      st.warning("Please generate and view a plate layout with samples from your GeoJSON first.")

#################################################
### Step 2.3 : Upload Custom Samples and Wells ##
#################################################

# button to upload custom samples and wells
if st.button("Upload custom samples and wells dictionary, will override"):
   logger.info("Upload custom samples and wells dictionary -- ButtonPress")
   st.session_state.show_saw_uploader = True

# If the button has been clicked, show the uploader and process the file
if st.session_state.show_saw_uploader:
   uploaded_saw = st.file_uploader(
      label = "Choose a custom samples-and-wells file (.txt or .json)",
      type = ["txt", "json"],
      accept_multiple_files=False,
      key="saw_uploader"
   )
   if uploaded_saw is not None:
      st.session_state.saw = utils.parse_dictionary_from_file(uploaded_saw)
      logger.debug(uploaded_saw)
      core.load_and_QC_SamplesandWells(st.session_state.saw)
      st.session_state.use_plate_wells = False
      st.success("Custom samples and wells dictionary loaded and checked.")
      st.session_state.show_saw_uploader = False

###############################
### Step 3: Process contours ##
###############################

st.markdown("""
            ## Step 3: Process to create .xml file for LMD
            Here we create the .xml file from your geojson.  
            Please download the QC image, and plate scheme for future reference.  
            """)

if st.button("Process files"):
   logger.info("Process files button clicked")
   if st.session_state.gdf is not None and st.session_state.saw is not None:
      logger.debug(st.session_state.gdf)
      logger.debug(st.session_state.saw)
      logger.debug(st.session_state.calibs)
      xml_content, csv_content, image_path = core.create_collection()
      st.session_state.xml_content = xml_content
      st.session_state.csv_content = csv_content

      zip_buffer = io.BytesIO()
      with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
         zip_file.writestr(f'{Path(st.session_state.file_name).stem}.xml', xml_content)
         zip_file.writestr(f'{Path(st.session_state.file_name).stem}_384_wellplate.csv', csv_content)
         with tempfile.NamedTemporaryFile(suffix=".geojson") as tmp_geojson:
            tmp_gdf = utils.sanitize_gdf(st.session_state.gdf)
            tmp_gdf.to_file(tmp_geojson.name, driver="GeoJSON")
            zip_file.write(tmp_geojson.name, f'{Path(st.session_state.file_name).stem}_processed.geojson')
         with open(image_path, "rb") as f:
            zip_file.writestr("collection.png", f.read())
         if "log_file_path" in st.session_state and st.session_state.log_file_path:
            zip_file.write(st.session_state.log_file_path, f"log_{st.session_state.session_id}.log")

      st.session_state.zip_buffer = zip_buffer
      st.image(image_path, caption='Your Contours', width='content')
      st.success("All files have been processed and are ready for download.")
      logger.info("All files processed and zipped successfully")
   else:
      st.warning("Please ensure you have loaded a GeoJSON and provided a samples-and-wells scheme.")
      logger.warning("GeoJSON or samples-and-wells scheme not found")

if st.session_state.zip_buffer:
   st.download_button(
      label="Download files",
      data=st.session_state.zip_buffer.getvalue(),
      file_name=f"{Path(st.session_state.file_name).stem}_collection.zip",
      mime="application/zip"
   )

st.divider()
st.divider()
st.divider()
st.divider()

#######################
####### EXTRAS ########
#######################

st.markdown("""
            # Extras to make your life easier :D
             - Create Qupath classes
            """)
st.divider()


#################################
## EXTRA 1: Classes for QuPath ##
#################################
st.markdown("""
            ## Extra #1 : Create QuPath classes from categoricals
            Creating many QuPath classes can be tedious, and is very error prone, especially for large projects.  
            This tool takes in two lists of categoricals, and a number for replicates, and create a class for every permutation.  

            Afterwards you must:
            1. Create a new QuPath project 
            2. Close QuPath window
            3. Delete `<QuPath project>/classifiers/annotations/classes.json`
            4. Replace with newly created file
            5. Rename it as `classes.json`
            6. Reopen QuPath with project, and you should see classes
            """)


color_map = {"red": 0xFF0000,"green": 0x00FF00,"blue": 0x0000FF,
            "magenta": 0xFF00FF,"cyan": 0x00FFFF,"yellow": 0xFFFF00}
java_colors = [-(0x1000000 - rgb) for rgb in color_map.values()]

input1 = st.text_area("Enter first categorical (comma-separated)", placeholder="example: celltype_A, celltype_B")
input2 = st.text_area("Enter second categorical (comma-separated)", placeholder="example: control, drug_treated")
input3 = st.number_input("Enter number of replicates", min_value=1, step=1, value=2)
list1 = [i.strip() for i in input1.split(",") if i.strip()]
list2 = [i.strip() for i in input2.split(",") if i.strip()]

if st.button("Create class names for QuPath"):
   list_of_samples = utils.generate_combinations(list1, list2, input3)
   json_data = {"pathClasses": []}
   for i, name in enumerate(list_of_samples):
      json_data["pathClasses"].append({
         "name": name,
         "color": java_colors[i % len(java_colors)]
      })
   with open("classes.json", "w") as f:
      json.dump(json_data, f, indent=2)

   st.download_button("Download Samples and Wells file for Qupath", 
                     data=Path('./classes.json').read_text(), 
                     file_name="classes.json")

st.image(image="./assets/sample_names_example.png",
         caption="Example of class names for QuPath")
st.divider()
