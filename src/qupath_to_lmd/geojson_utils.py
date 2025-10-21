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
