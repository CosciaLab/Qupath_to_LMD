import geopandas
import pandas
import numpy
import datetime
import tifffile
import shapely
import streamlit as st
from python_functions import dataframe_to_xml_v2
from lmd.lib import SegmentationLoader
from lmd.lib import Collection, Shape
from lmd import tools
from PIL import Image
from pathlib import Path
import ast



st.title("Convert a GeoJSON polygons to xml")
st.subheader("From Jose Nimo, PhD at AG Coscia in the Max Delbrueck Center for Molecular Medicine in Berlin")
st.subheader("Thanks to Florian Wuenemman for inspiration, and Sophia Madler for the lmd-py package")
st.subheader("Contact jose.nimo@mdc-berlin.de for questions and suggestions")
st.divider()

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)

calibration_point_1 = st.text_input("Enter the name of the first calibration point: ",  placeholder ="calib1")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ", placeholder ="calib2")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ",  placeholder ="calib3")

samples_and_wells_input = st.text_area("Enter the desired samples and wells scheme, this is required!")

@st.cache(allow_output_mutation=True)
def run_script(uploaded_file, calibration_points, samples_and_wells):
   # Add your script logic here
   dataframe_to_xml_v2(uploaded_file, calibration_points, samples_and_wells)

if st.button("Run the script"):
   #load samples and wells
   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)
   #load calibration points
   calibration_points = [calibration_point_1, calibration_point_2, calibration_point_3]
   st.write("these are your calibration points: ")
   st.write(calibration_points)
   # Run your script or process the inputs
   st.write("Running the script...")
   # Add your script logic here
   run_script(uploaded_file, calibration_points, samples_and_wells)
   #Running is done
   st.write("Please download the file now")
   st.download_button("Download file", Path("./out.xml").read_text(), "out.xml")