import itertools
import re
import pandas
import streamlit as st
from loguru import logger
import string
import geopandas
import shapely
import ast
import pandas as pd
import numpy as np
from random import sample

# def QC_geojson_file(geojson_path: str):
#    df = geopandas.read_file(geojson_path)
#    logger.info(f"Geojson file loaded with shape {df.shape} for metadata coloring")
#    df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
#    logger.info(f"Point geometries have been removed")
#    df = df[df['classification'].notna()]
#    df = df[df.geometry.geom_type != 'MultiPolygon']
#    df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))
#    return df

def generate_combinations(list1, list2, num) -> list:
   """Generate dictionary from all combinations of two lists and a range, assigning arbitrary values."""
   assert isinstance(list1, list) and all(isinstance(i, str) for i in list1), "Input 1 must be a list of strings"
   assert isinstance(list2, list) and all(isinstance(i, str) for i in list2), "Input 2 must be a list of strings"
   assert isinstance(num, int) and num > 0, "Input 3 must be a positive integer"
   keys = [f"{a}_{b}_{i}" for a, b, i in itertools.product(list1, list2, range(1, num + 1))]
   return keys

def parse_metadata_csv(csv_path):
   try:
      df = pandas.read_csv(csv_path, sep=None, engine="python", encoding="utf-8-sig")
      return df
   except Exception as e:
      logger.error(f"Error reading CSV file: {e}")
      st.stop()

def create_list_of_acceptable_wells(
      plate:str="384", 
      margins:int=0,
      step_row:int=1,
      step_col:int=1):
   """Creates wells according to user parameters."""
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

# def create_default_samples_and_wells(list_of_samples, list_of_acceptable_wells):
#    assert len(list_of_samples) <= len(list_of_acceptable_wells), "Number of samples must be less than or equal to the number of wells"
#    samples_and_wells = {}
#    for sample,well in zip(list_of_samples, list_of_acceptable_wells):
#       samples_and_wells[sample] = well
#    return samples_and_wells

def sample_placement_384wp(samples_and_wells):

   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)

   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i

   return df_wp384

def create_dataframe_samples_wells(
      # geojson_path:str = None,
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
         st.warning("More classes than allowed wells")
         list_of_classes = list_of_classes[:len(acceptable_wells_list)]

      if randomize:
         acceptable_wells_list = sample(acceptable_wells_list, len(acceptable_wells_list))

      for s, well in zip(list_of_classes, acceptable_wells_list, strict=False):
         row = well[0]
         col = int(well[1:])
         df.at[row, col] = s

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
   content = re.sub(r'#.*', '', content)
   content = content.strip()

   if not content:
      return {}

   try:
      # ast.literal_eval is safe and handles many Python literal formats
      return ast.literal_eval(content)
   except (ValueError, SyntaxError) as e:
      logger.error(f"Failed to parse dictionary from content: {e}")
      return {}



## from geojson utils

import geopandas
import shapely
import streamlit as st
import ast
import itertools
from loguru import logger
import datetime
from qupath_to_lmd.utils import parse_metadata_csv

def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
      else:
         st.write(f'Geometry type {geometry.geom_type} not supported, please convert to Polygon or LineString in Qupath')
         st.stop()

def QC_geojson_file(geojson_path: str):
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape} for metadata coloring")
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))
   return df

def check_ids(shapes, metadata, metadata_name_key):
   logger.debug("Running Check IDS")
   # remove whitespaces
   metadata[metadata_name_key] = metadata[metadata_name_key].astype(str).str.strip()
   shapes['classification_name'] = shapes['classification_name'].astype(str).str.strip()
   logger.debug("metadata names have been stripped")
   logger.debug(f"metadata shape {metadata.shape}")   

   shape_names_set = set(shapes['classification_name'])
   metadata_names_set = set(metadata[metadata_name_key])
   logger.debug(f"First 5 shape names: {list(shape_names_set)[:5]}")
   logger.debug(f"First 5 metadata names: {list(metadata_names_set)[:5]}")

   # all shapes must be in metadata
   if shape_names_set.issubset(metadata_names_set): 
      logger.info("All shape names are found in the metadata")
      st.write("All shape names are found in the metadata")
      return True
   else:
      logger.error(f"{shape_names_set - metadata_names_set} were not found in the metadata")
      logger.error(f"overlapping names: {shape_names_set & metadata_names_set}")
      return False

def process_geojson_with_metadata(path_to_geojson, path_to_csv, metadata_name_key, metadata_variable_key):

   shapes = QC_geojson_file(geojson_path=path_to_geojson)
   metadata = parse_metadata_csv(path_to_csv)
   
   assert check_ids(shapes=shapes, metadata=metadata, metadata_name_key=metadata_name_key)
   
   #add info to shapes
   mapping = dict(zip(metadata[metadata_name_key], metadata[metadata_variable_key])) #assumes class name in first column
   shapes["metadata"] = shapes["classification_name"].map(mapping)
   
   #format info to QuPath readable way
   default_colors = [[31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40], [148, 103, 189]]
   color_cycle = itertools.cycle(default_colors)
   color_dict = dict(zip(metadata[metadata_variable_key].astype("category").cat.categories.astype(str), color_cycle))
   shapes['classification'] = shapes["metadata"].apply(lambda x: {'name': x, 'color': color_dict[x]})
   shapes['name'] = shapes['classification_name']
   shapes.drop(columns=["classification_name", "metadata", "id"], inplace=True)

   #export
   output_name = path_to_geojson.name.replace(".geojson", "")
   shapes.to_file(f"./{output_name}_{metadata_variable_key}_labelled_shapes.geojson", driver= "GeoJSON")

def dataframe_to_saw_dict(df: pd.DataFrame) -> dict:
    """Converts the plate layout DataFrame to a samples-and-wells dictionary."""
    saw_dict = {}
    # The dataframe is indexed by letters and has numbered columns.
    # The values are the sample names.
    for well_row, series in df.iterrows():
        for well_col, sample_name in series.items():
            # Check if the cell contains a sample name (is not None, NaN, or empty string)
            if sample_name and pd.notna(sample_name):
                well_coordinate = f"{well_row}{well_col}"
                saw_dict[sample_name] = well_coordinate
    return saw_dict