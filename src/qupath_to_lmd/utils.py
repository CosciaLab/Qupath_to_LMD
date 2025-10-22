import itertools
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

def QC_geojson_file(geojson_path: str):
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape} for metadata coloring")
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))
   return df

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

def create_default_samples_and_wells(list_of_samples, list_of_acceptable_wells):
   assert len(list_of_samples) <= len(list_of_acceptable_wells), "Number of samples must be less than or equal to the number of wells"
   samples_and_wells = {}
   for sample,well in zip(list_of_samples, list_of_acceptable_wells):
      samples_and_wells[sample] = well
   return samples_and_wells

def sample_placement_384wp(samples_and_wells):

   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)

   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i

   return df_wp384

def create_dataframe_samples_wells(
      geojson_path:str = None, 
      randomize:bool = False, 
      plate_string:str = "384", 
      acceptable_wells_list:list = None):
   
   if plate_string == "384":
      rows, cols = 16, 24
   elif plate_string == "96":
      rows, cols = 8, 12

   if geojson_path is None:

      row_labels = list(string.ascii_uppercase[:rows])
      col_labels = list(range(1, cols + 1))

      plate_data = []
      for r in row_labels:
         row_data = [f"{r}{c}" for c in col_labels]
         plate_data.append(row_data)
      df = pd.DataFrame(plate_data, index=row_labels, columns=col_labels)

   elif geojson_path is not None: 

      gdf = QC_geojson_file(geojson_path=geojson_path)
      list_of_classes = set(gdf['classification_name'].values)
      df = pd.DataFrame(np.nan, index=list(string.ascii_uppercase[:rows]), columns=range(1, cols + 1))

      if len(list_of_classes) > len(acceptable_wells_list):
         st.warning("More classes than allowed wells")
         list_of_classes = list_of_classes[:len(acceptable_wells_list)]

      if randomize:
         acceptable_wells_list = sample(acceptable_wells_list, len(acceptable_wells_list))

      for s, well in zip(list_of_classes, acceptable_wells_list):
         row = well[0]
         col = int(well[1:])
         df.at[row, col] = s
      
   return df

def provide_highlighting_for_df(
      geojson_path:str = None,
      acceptable_wells_set:set = None):

   if geojson_path is None:
      print("geojson path is None")
      print(f"{geojson_path}")

      def highlight_selected(well_name):
         if well_name in acceptable_wells_set:
            return 'background-color: #77dd77; color: black;' # Green
         else:
            return 'background-color: #f0f2f6;' # Light gray
         
      return highlight_selected
   
   elif geojson_path is not None:

      gdf = QC_geojson_file(geojson_path=geojson_path)
      list_of_classes = set(gdf['classification_name'].values)

      def highlight_selected(well_name):
            if well_name in list_of_classes:
               return 'background-color: #77dd77; color: black;' # Green
            else:
               return 'background-color: #f0f2f6;' # Light gray
            
      return highlight_selected