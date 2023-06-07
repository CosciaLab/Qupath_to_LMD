import geopandas
import pandas
import numpy
import datetime
import tifffile
import shapely
import streamlit as st
from python_functions import dataframe_to_xml_v2
from lmd.lib import Collection, Shape
from lmd import tools
from PIL import Image
from lmd.lib import SegmentationLoader
from pathlib import Path

"""
# Welcome to Streamlit!

Edit `/streamlit_app.py` to customize this app to your heart's desire :heart:

If you have any questions, checkout our [documentation](https://docs.streamlit.io) and [community
forums](https://discuss.streamlit.io).

In the meantime, below is an example of what you can do with just a few lines of code:
"""

st.title("Convert a GeoJSON polygons to xml")

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)

calibration_point_1 = st.text_input("Enter the name of the first calibration point: ")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ")


if st.button("Process file"):
   calibration_points = [calibration_point_1, calibration_point_2, calibration_point_3]
   st.write("these are your calibration points: ")
   st.write(calibration_points)
   # Check if both file and text inputs are provided
   if uploaded_file is not None and calibration_points is not None:
      # Run your script or process the inputs
      st.write("Running the script...")
      # Add your script logic here
      dataframe_to_xml_v2(uploaded_file, calibration_points)
      #output = dataframe_to_xml_v2(uploaded_file, calibration_points)
      st.download_button("Download file", Path("./out.xml").read_text(), "out.xml")

   else:
      st.warning("Please provide a file and text input")
   