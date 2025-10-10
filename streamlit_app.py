import streamlit as st
from pathlib import Path
import json

from loguru import logger
import sys
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")

from qupath_to_lmd.utils import generate_combinations, create_list_of_acceptable_wells, create_default_samples_and_wells
from qupath_to_lmd.geojson_utils import process_geojson_with_metadata
from qupath_to_lmd.st_cached import load_and_QC_geojson_file, load_and_QC_SamplesandWells, create_collection

####################
### Introduction ###
####################
st.markdown("""
            # Convert a GeoJSON polygons for Laser Microdissection
            ## Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework
            ### For help, post issue on [Github](https://github.com/CosciaLab/Qupath_to_LMD) with .geojson file
            """)
st.divider()


############################
## Step 1: Geojson upload ##
############################

st.markdown("""
            ## Step 1: Upload and check .geojson file from Qupath
            Upload your .geojson file from qupath, order of calibration points is important
            """)

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)
calibration_point_1 = st.text_input("Enter the name of the first calibration point: ",  placeholder ="first_calib")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ", placeholder ="second_calib")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ",  placeholder ="third_calib")
list_of_calibpoint_names = [calibration_point_1, calibration_point_2, calibration_point_3]

if st.button("Load and check the geojson file"):
   if uploaded_file is not None:
      load_and_QC_geojson_file(geojson_path=uploaded_file, list_of_calibpoint_names=list_of_calibpoint_names)
   else:
      st.warning("Please upload a file first.")
st.divider()

########################################
## Step 2: Choose collection settings ##
########################################

st.markdown("""
            ## Step 2: Decide which plate to collect into, either 384 or 96 well plate
            Decide how many wells to make unavailable as a margin (for 384wp we suggest a margin of 2)
            Decide how many wells to leave blank in between, for easier pipetting.
            """)

# --- Setup the single row of inputs ---
plate, margin, step_row, step_col = st.columns(4)
with plate:
   selected_option = st.selectbox('Select an item',('384 well plate', '96 well plate'),key='dropdown_key')
with margin:
   int_input_1 = st.number_input('Margin (integer)', min_value=0, max_value=10, value=0, key='margin_key')
with step_row:
   int_input_2 = st.number_input('Step rows (integer)', min_value=0, max_value=10, value=0, key='step_row_key')
with step_col:
   int_input_3 = st.number_input('Step columns (integer)', min_value=0, max_value=10, value=0, key='step_col_key')


# Check format button

# Prints out format


############################################
## Step 2: Input custom samples and wells ##
############################################

st.markdown("""
            ## Step 2: Copy/Paste and check samples and wells scheme
            Sample names will be checked against the uploaded geojson file.  
            Using default is **not** possible, I am nudging users to save their samples_and_wells.  

            Samples and wells have this pattern:
            ```python  
            {"sample_1" : "C3",  
            "sample_2" : "C5",  
            "sample_3" : "C7"}  
            ```

            If you have many samples and this is very laborious go to Step 5, I offer you a shortcut :)  
            """)

samples_and_wells_input = st.text_area("Enter the desired samples and wells scheme")

if st.button("Check the samples and wells"):
   samples_and_wells = load_and_QC_SamplesandWells(samples_and_wells_input=samples_and_wells_input, 
                                                   geojson_path=uploaded_file, 
                                                   list_of_calibpoint_names=list_of_calibpoint_names)
st.divider()

###############################
### Step 3: Process contours ##
###############################

st.markdown("""
            ## Step 3: Process to create .xml file for LMD
            Here we create the .xml file from your geojson.  
            Please download the QC image, and plate scheme for future reference.  
            """)

# Dropdown: use collection setup, or use custom samples and wells

if st.button("Process geojson and create the contours"):
   create_collection(geojson_path=uploaded_file,
                     list_of_calibpoint_names=list_of_calibpoint_names,
                     samples_and_wells_input=samples_and_wells_input)
   st.success("Contours created successfully!")
   st.download_button("Download contours file", 
                     Path(f'./{uploaded_file.name.replace("geojson", "xml")}').read_text(), 
                     f'./{uploaded_file.name.replace("geojson", "xml")}')
   st.download_button("Download 384 plate scheme", 
                     Path(f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}').read_text(),
                     f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}')
st.divider()


#######################
####### EXTRAS ########
#######################

st.markdown("""
            # Extras to make your life easier :D
             - Create Qupath classes
             - Create default samples and wells
             - Color shapes with categorical
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
   list_of_samples = generate_combinations(list1, list2, input3)
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

###############################################
## EXTRA 2: Create default samples and wells ##
###############################################

st.markdown("""
            ## Extra 2: Create samples and wells  
            ### To designate which samples go to which wells in the collection device
            Every QuPath class represents one sample, therefore each class needs one designated well for collection.  
            Default wells are spaced (C3, C5, C7) for easier pipetting, modify at your discretion.  
            The file can be opened by any text reader Word, Notepad, etc.  
            """)

input4 = st.text_area("Enter first categorical:", placeholder="example: celltype_A, celltype_B")
input5 = st.text_area("Enter second categorical:", placeholder="example: control, drug_treated")
input6 = st.number_input("Enter number of reps", min_value=1, step=1, value=2)
list3 = [i.strip() for i in input4.split(",") if i.strip()]
list4 = [i.strip() for i in input5.split(",") if i.strip()]

if st.button("Create Samples and wells scheme with default wells"):
   spaced_list_of_acceptable_wells = create_list_of_acceptable_wells()[::2]
   list_of_samples = generate_combinations(list3, list4, input6)
   samples_and_wells = create_default_samples_and_wells(list_of_samples, spaced_list_of_acceptable_wells)
   with open("samples_and_wells.json", "w") as f:
      json.dump(samples_and_wells, f, indent=4)
   st.download_button("Download Samples and Wells file",
                     data=Path('./samples_and_wells.json').read_text(),
                     file_name="samples_and_wells.txt")
   
st.image(image="./assets/samples_and_wells_example.png",
         caption="Example of samples and wells scheme")
st.divider()

###############################################
### EXTRA 3: Color contours with categorical ##
###############################################

st.markdown("""
            ## Extra 3: Color contours with categorical  
            This tool was born to let you check which shapes have been collected based on a simple excel sheet table.
            It can also color the shapes based on any categorical values.

            Instructions: 
             - You must upload the .geojson file with classified annotations.
             - You must upload a csv with two columns:
               - Class name, this has to match the annotation classification names exactly.  
               - Categorical column name, this can be any set of categoricals, For example: collection status. max 6 categories or colors repeat.
            """)


st.write("Upload geojson file you would like to color with metadata")
geojson_file = st.file_uploader("Choose a geojson file", accept_multiple_files=False)

st.write("Upload table as csv with two columns")
metadata_file = st.file_uploader("Choose a csv file", accept_multiple_files=False)
metadata_name_key = st.text_input("Column header with shape class names", placeholder="Class name")
metadata_variable_key = st.text_input("Column header to color shapes with", placeholder="Categorical column name")

if st.button("Process metadata and geojson, for labelled shapes"):
   process_geojson_with_metadata(
      path_to_geojson=geojson_file,
      path_to_csv=metadata_file,
      metadata_name_key=metadata_name_key,
      metadata_variable_key=metadata_variable_key)
   
   st.download_button("Download lablled shapes", 
                     Path(f"./{geojson_file.name.replace("geojson", metadata_variable_key + "_labelled_shapes.geojson")}").read_text(),
                     f"./{geojson_file.name.replace("geojson", metadata_variable_key + "_labelled_shapes.geojson")}")