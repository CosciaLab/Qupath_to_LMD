import json
import sys
import uuid
import tempfile
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from loguru import logger

import qupath_to_lmd.core as core
import qupath_to_lmd.utils as utils

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")

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
# dataframe to display and potentially use for samples and wells
if 'plate_df' not in st.session_state:
   st.session_state.plate_df = None
# parameters for creating dataframe with samples or defaults
if 'plate_gen_params' not in st.session_state:
   st.session_state.plate_gen_params = None

if 'show_saw_uploader' not in st.session_state:
   st.session_state.show_saw_uploader = False
if 'unique_classes_csv' not in st.session_state:
   st.session_state.unique_classes_csv = None

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
   if uploaded_file:
      # process calibs
      st.session_state.calibs = [calibration_point_1, calibration_point_2, calibration_point_3]
      st.session_state.file_name = uploaded_file.name
      # process and QC geojson
      st.session_state.gdf = core.load_and_QC_geojson_file(geojson_path=uploaded_file)
   else:
      st.warning("Please upload a geojson file.")

st.divider()

# --- New section for unique classes ---
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
      if not classes_to_make_unique:
         st.warning("Please select at least one class to make unique.")
      else:
         # This function modifies st.session_state.gdf
         core.make_classes_unique(classes_to_make_unique)
         st.session_state.saw = None
         st.session_state.plate_df = None
         st.info("Chosen classes were split up, check below.")

         # Prepare CSV for download
         csv_data = st.session_state.gdf.to_csv(index=False)
         st.session_state.unique_classes_csv = csv_data

   if st.session_state.unique_classes_csv is not None:
      st.download_button(
         label="Download Unique Names CSV",
         data=st.session_state.unique_classes_csv,
         file_name=f"{Path(st.session_state.file_name).stem}_unique_names.csv",
         mime="text/csv",
      )

   st.markdown("---")
   st.markdown("#### Download Processed GeoDataFrame")

   @st.cache_data
   def get_geojson_download_data(_gdf):
      # Sanitize the dataframe by removing any column with NA values
      # This prevents 'null' values in the final GeoJSON properties
      _gdf_sanitized = _gdf.dropna(axis='columns')

      # Create a temporary file path
      fd, path = tempfile.mkstemp(suffix=".geojson")
      
      try:
         # Write the sanitized dataframe to the file
         _gdf_sanitized.to_file(path, driver="GeoJSON")
         
         # Read the content back from the path
         with open(path, 'r') as f:
               return f.read()
      finally:
         # Clean up the file descriptor and the file itself
         os.close(fd)
         os.remove(path)
   geojson_data = get_geojson_download_data(st.session_state.gdf)

   st.download_button(
      label="Download Processed GeoJSON",
      data=geojson_data,
      file_name=f"{Path(st.session_state.file_name).stem}_processed.geojson",
      mime="application/json"
   )
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

# --- Setup the single row of inputs ---
plate, margin, step_row, step_col = st.columns(4)
with plate:
   plate_string = st.selectbox('Select a plate type',('384 well plate', '96 well plate'))
with margin:
   margin_int = st.number_input('Margin (integer)', min_value=0, max_value=10, value=1)
with step_row:
   step_row_int = st.number_input('Space between rows', min_value=1, max_value=10, value=1)
with step_col:
   step_col_int = st.number_input('Space between columns', min_value=1, max_value=10, value=1)

# ---  Parse user plate inputs ---
try:
   plate_type = plate_string.split(' ')[0]
   acceptable_wells_list = utils.create_list_of_acceptable_wells(
         plate=plate_type, margins=margin_int, step_row=step_row_int, step_col=step_col_int)
   acceptable_wells_set = set(acceptable_wells_list)

except ValueError as e:
    st.error(f"Error Parsing plate inputs: {e}")

##########################################
## Step 3: Plot collection with samples ##
##########################################

# --- Setup single row with two buttons ---
st.subheader(f"Visualization for {plate_type}-Well Plate")
col1, col2, col3 = st.columns(3)
with col1:
   if st.button("Show plate format with default wells"):
      st.session_state.view_mode = 'default'
with col2:
   if st.button("Show plate format with samples from geojson"):
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


# --- plot dataframes ---
if st.session_state.view_mode == 'default':
   st.subheader(f"Visualization for {plate_type}-Well Plate (Default)")
   try:
      df = utils.create_dataframe_samples_wells(plate_string=plate_type)
      mapping = utils.provide_highlighting_for_df(
         acceptable_wells_set=acceptable_wells_set)
      st.dataframe(df.style.map(mapping), width="stretch" )
   except ValueError as e:
      st.error(f"Error plotting defaults: {e}")

elif st.session_state.view_mode == 'samples':
   st.subheader(f"Visualization for {plate_type}-Well Plate (Samples from GeoJSON)")

   if uploaded_file is None:
      st.warning("File no longer available. Please upload a file or switch to the default view.")
      st.session_state.view_mode = 'none'
   else:
      try:
         # Only regenerate the dataframe if parameters have changed or it doesn't exist
         if (params_have_changed or df_missing) and st.session_state.gdf is not None:
            st.session_state.plate_gen_params = plate_gen_params # Store the new params
            st.session_state.plate_df = utils.create_dataframe_samples_wells(
               randomize = randomize_toggle,
               plate_string = plate_type,
               acceptable_wells_list = acceptable_wells_list
            )

         # Always display the current dataframe from session state
         if st.session_state.plate_df is not None:
            mapping = utils.provide_highlighting_for_df()
            st.dataframe(st.session_state.plate_df.style.map(mapping), width="stretch")
         else:
            st.info("Generate a plate layout to see it here.")

      except ValueError as e:
         st.error(f"Error: {e}")

# --- New button to confirm layout ---
if st.button("Confirm and use this plate layout"):
    if st.session_state.view_mode == 'samples' and st.session_state.plate_df is not None:
        # Convert dataframe to dictionary
        saw_from_df = utils.dataframe_to_saw_dict(st.session_state.plate_df)

        # Store in session state
        st.session_state.saw = saw_from_df

        # Run QC
        core.load_and_QC_SamplesandWells(st.session_state.saw)
        st.session_state.use_plate_wells = True # To indicate we are using a plate layout
        st.success("Samples and wells layout confirmed and loaded!")
        st.write(st.session_state.saw) # Show the user the resulting dictionary
    else:
        st.warning("Please generate and view a plate layout with samples from your GeoJSON first.")

# button to upload custom samples and wells
if st.button("Upload custom samples and wells dictionary, will override"):
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
### Step 4: Process contours ##
###############################

st.markdown("""
            ## Step 3: Process to create .xml file for LMD
            Here we create the .xml file from your geojson.  
            Please download the QC image, and plate scheme for future reference.  
            """)

if st.button("Process geojson and create the contours"):
   if st.session_state.gdf is not None and st.session_state.saw is not None:
      xml_content, csv_content = core.create_collection()
      st.session_state.xml_content = xml_content
      st.session_state.csv_content = csv_content
      st.success("Contours created successfully!")
   else:
      st.warning("Please ensure you have loaded a GeoJSON and provided a samples-and-wells scheme.")

if st.session_state.xml_content:
   st.download_button(
      "Download contours file",
      st.session_state.xml_content,
      f'{Path(st.session_state.file_name).stem}.xml'
   )
if st.session_state.csv_content:
   st.download_button(
      "Download 384 plate scheme",
      st.session_state.csv_content,
      f'{Path(st.session_state.file_name).stem}_384_wellplate.csv'
   )
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
