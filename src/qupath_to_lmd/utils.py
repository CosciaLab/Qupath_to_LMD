import ast
import itertools
import re
import string
from random import sample

import geopandas
import numpy as np
import pandas
import pandas as pd
import streamlit as st
from loguru import logger

def generate_combinations(list1, list2, num) -> list:
   """Generate dictionary from all combinations of two lists and a range, assigning arbitrary values."""
   logger.info("Created combinations")
   assert isinstance(list1, list) and all(isinstance(i, str) for i in list1), "Input 1 must be a list of strings"
   assert isinstance(list2, list) and all(isinstance(i, str) for i in list2), "Input 2 must be a list of strings"
   assert isinstance(num, int) and num > 0, "Input 3 must be a positive integer"
   keys = [f"{a}_{b}_{i}" for a, b, i in itertools.product(list1, list2, range(1, num + 1))]
   return keys

def create_list_of_acceptable_wells(
      plate:str="384", 
      margins:int=0,
      step_row:int=1,
      step_col:int=1):
   """Creates wells according to user parameters."""
   logger.info("Creating list of accetable wells")
   if plate not in ["384","96"]:
      raise ValueError("Plate must be either 384 or 96")
   if not isinstance(margins,int):
      raise ValueError("margins must be an integer")

   min_row = 1
   min_col = 1

   if plate == "384":
      max_row = 16
      max_col = 24
   else:
      max_row = 8
      max_col = 12

   if margins>0 :
      max_col += -(margins)
      max_row += -(margins)
      min_col += margins
      min_row += margins

   list_of_acceptable_wells = []
   for row in list(string.ascii_uppercase[min_row-1 : max_row : step_row]):
      for column in range(min_col , max_col+1, step_col):
         list_of_acceptable_wells.append(str(row) + str(column))

   return list_of_acceptable_wells

def sample_placement():
   """Sample placement into plate csv."""
   logger.info("Sample placement for 384wp")

   plate_type = "384"  # Default to 384-well plate
   if 'plate_gen_params' in st.session_state and isinstance(st.session_state.plate_gen_params, dict):
      plate_type = st.session_state.plate_gen_params.get('plate_type', "384")

   if plate_type == "384":
      max_row = 16
      max_col = 24
   else:
      max_row = 8
      max_col = 12

   rows= [i for i in string.ascii_uppercase[:max_row]]
   columns = [str(i) for i in range(1,max_col+1)]
   df = pandas.DataFrame('',columns=columns, index=rows)

   for i in st.session_state.saw.keys():
      location = st.session_state.saw[i]
      df.at[location[0],location[1:]] = i

   logger.success("Sample placement done")
   return df

def create_dataframe_samples_wells(
      acceptable_wells_list:list = None,
      randomize:bool = False,
      plate_string:str = "384",
      ):
   """Creates a dataframe to be displayed."""
   if plate_string == "384":
      rows, cols = 16, 24
   elif plate_string == "96":
      rows, cols = 8, 12

   if st.session_state.view_mode == "default":
      row_labels = list(string.ascii_uppercase[:rows])
      col_labels = list(range(1, cols + 1))

      plate_data = []
      for r in row_labels:
         row_data = [f"{r}{c}" for c in col_labels]
         plate_data.append(row_data)
      df = pd.DataFrame(plate_data, index=row_labels, columns=col_labels)

   elif st.session_state.view_mode == "samples": 
      if st.session_state.gdf is None:
         st.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
         st.stop()

      list_of_classes = set(st.session_state.gdf['classification_name'].values)
      df = pd.DataFrame(np.nan, index=list(string.ascii_uppercase[:rows]), columns=range(1, cols + 1), dtype=object)

      if len(list_of_classes) > len(acceptable_wells_list):
         logger.warning("More classes than allowed wells")
         st.warning("More classes than allowed wells")
         # list_of_classes = list_of_classes[:len(acceptable_wells_list)]

      if randomize:
         logger.info("Randomizing sample locations")
         acceptable_wells_list = sample(acceptable_wells_list, len(acceptable_wells_list))

      for s, well in zip(list_of_classes, acceptable_wells_list, strict=False):
         row = well[0]
         col = int(well[1:])
         df.at[row, col] = s

   logger.success("Created dataframe for viewing samples and wells")
   return df

def provide_highlighting_for_df(acceptable_wells_set:set = None):
   """Creates map to color dataframe."""
   if st.session_state.view_mode == "default":
      def highlight_selected(well_name):
         if well_name in acceptable_wells_set:
            return 'background-color: #77dd77; color: black;' # Green
         else:
            return 'background-color: #f0f2f6;' # Light gray
      return highlight_selected

   elif st.session_state.view_mode == "samples":
      list_of_classes = set(st.session_state.gdf['classification_name'].values)
      def highlight_selected(well_name):
            if well_name in list_of_classes:
               return 'background-color: #77dd77; color: black;' # Green
            else:
               return 'background-color: #f0f2f6;' # Light gray
      return highlight_selected

def parse_dictionary_from_file(file_input) -> dict:
   """Reads a file supposed to contain a Python dictionary and parses it."""
   logger.info("Parse external txt file to python dictionary")
   content = ""
   try:
      # Check if it's a string path (from Jupyter/testing)
      if isinstance(file_input, str):
         with open(file_input, 'r', encoding='utf-8-sig') as f:
            content = f.read()
      # Check if it's a file-like object (from st.file_uploader)
      elif hasattr(file_input, 'read'):
         # read() returns bytes, so we need to decode it to a string
         content = file_input.read().decode('utf-8-sig')
      else:
         logger.error(f"Unsupported input type for parsing: {type(file_input)}")
         return {}

   except Exception as e:
      logger.error(f"Error reading file content: {e}")
      return {}

   # Remove comments and strip whitespace
   # content = re.sub(r'#.*', '', content)
   # content = content.strip()

   if not content:
      return {}

   try:
      # ast.literal_eval is safe and handles many Python literal formats
      return ast.literal_eval(content)
   except (ValueError, SyntaxError) as e:
      logger.error(f"Failed to parse dictionary from content: {e}")
      return {}

def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
      else:
         st.write(f'Geometry type {geometry.geom_type} not supported, please convert to Polygon or LineString in Qupath')
         st.stop()

def dataframe_to_saw_dict(df: pd.DataFrame) -> dict:
    """Converts the plate layout DataFrame to a samples-and-wells dictionary."""
    logger.info("Converting desired dataframe to samples and wells dictionary")
    saw_dict = {}
    for well_row, series in df.iterrows():
        for well_col, sample_name in series.items():
            if sample_name and pd.notna(sample_name):
                well_coordinate = f"{well_row}{well_col}"
                saw_dict[sample_name] = well_coordinate
    return saw_dict

def update_classification_column(gdf:geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
   """Updates the 'classification' dictionary for every row.

   It replaces the value associated with the 'name' key inside the
   'classification' dictionary with the string value from the
   'classification_name' column.
   """
   logger.info("Updating classification of objects according to class split")

   def update_row_dict(row):
      if isinstance(row['classification'],dict):
         class_dict = row['classification']
      elif isinstance(row['classification'],str):
         class_dict = ast.literal_eval(row['classification'])

      class_dict['name'] = row['classification_name']

      row['classification'] = str(class_dict)

      return row['classification']

   gdf['classification'] = gdf.apply(update_row_dict, axis=1)

   return gdf


def sanitize_gdf(gdf):
   """Ensure compatibility with QuPath."""
   logger.info("Dropping columns with NaNs in the geodataframe")
   #check for NaNs (they cause error with QuPath)
   # drop columns if they exist
   gdf = gdf.dropna(axis="columns")

   #ensure critical columns there
   cols_to_keep = ['id',"objectType","classification","geometry"]
   # Check that all critical columns are present
   missing = [col for col in cols_to_keep if col not in gdf.columns]
   if missing:
      logger.error(f"Missing critical columns: {missing}")
      raise ValueError(f"Missing critical columns: {missing}")

   return gdf[cols_to_keep]

