import ast
import os
import tempfile

import geopandas
import numpy
import shapely
import streamlit as st
from lmd.lib import Collection
from loguru import logger

import qupath_to_lmd.utils as utils

@st.cache_data
def load_and_QC_geojson_file(geojson_path: str) -> geopandas.GeoDataFrame:
   """Checks and load the geojson."""
   logger.info(f"Starting load_and_QC_geojson_file for path: {geojson_path}")
   #check for streamlit variables
   if st.session_state.calibs is None:
      logger.error("Calibration points were not accessible directly in session state.")
      st.error("Calibration points were not accesible directly")
      st.stop()

   # 1 Digestion
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape}")
   logger.debug(f"Initial GeoDataFrame columns:\n{df.columns}")
   if df.empty:
      st.warning("The uploaded geojson file is empty.")
      logger.warning("The uploaded geojson file is empty.")
      st.stop()
   if "name" not in df.columns:
      st.warning("No calibration points in file")
      logger.warning("No 'name' column found in GeoJSON, indicating missing calibration points.")
      st.stop()

   # describe geodataframe
   geometry_counts = df.geometry.geom_type.value_counts()
   log_message = ", ".join(f"{count} {geom_type}s" for geom_type, count in geometry_counts.items())
   logger.info(f"Geometries in DataFrame: {log_message}")
   st.write(f"Geometries in DataFrame: {log_message}")

   #check for calibration points
   for point_name in st.session_state.calibs:
      if point_name not in df['name'].unique():
         st.write(f'Your given annotation_name >>{point_name}<< is not present in the file')
         st.write(f'These are the calib points you passed: {st.session_state.calibs}')
         st.write(f"These are the calib points found in the geojson: {df[df['geometry'].geom_type == 'Point']['name'].values}")
         logger.error(f'Your given annotation_name {point_name} is not present in the file')
         logger.error(f'These are the calib points you passed: {st.session_state.calibs}')
         logger.error(f"These are the calib points found in the geojson: {df[df['geometry'].geom_type == 'Point']['name'].values}")
         st.stop()

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in st.session_state.calibs])

   st.session_state.calib_array = calib_np_array
   logger.info(f"Calib_array set to {calib_np_array}")

   def polygon_intersects_triangle(polygon, triangle):
      if isinstance(polygon, shapely.Polygon):
         return polygon.intersects(triangle)
      elif isinstance(polygon, shapely.LineString):
         return polygon.intersects(triangle)
      else:
         return False  # for other geometry types

   # how many polygons intersect the triangle made by calibs?
   df['intersects'] = df['geometry'].apply(lambda x: polygon_intersects_triangle(x, shapely.Polygon(calib_np_array)))
   num_of_polygons_and_LineString = df[df['geometry'].geom_type.isin(['Polygon', 'LineString'])].shape[0]

   intersect_fraction = df['intersects'].sum()/num_of_polygons_and_LineString
   logger.info(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")
   st.write(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")

   if intersect_fraction < 0.25:
      st.write('WARNING: Less than 25% of the objects intersect with the calibration triangle')
      logger.warning("Less than 25% of the objects intersect with the calibration triangle")
      logger.warning("Polygons could be warped, consider changing calib points")

   #remove points
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info("Point geometries have been removed")

   #check and remove empty classifications
   if df['classification'].isna().sum() !=0 :
      logger.debug(f"you have {df['classification'].isna().sum()} NaNs in your classification column")
      st.write(f"you have {df['classification'].isna().sum()} NaNs in your classification column",
            "these are unclassified objects from Qupath, they will be ignored")
      df = df[df['classification'].notna()]

   #get classification name from inside geometry properties
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      logger.debug(f"{df[df.geometry.geom_type == 'MultiPolygon'].shape[0]} MultiPolygons found")
      st.write('MultiPolygon objects present:')
      st.table(df[df.geometry.geom_type == 'MultiPolygon'][['annotation_name','classification_name']])
      st.write('these are not supported, please convert them to polygons in Qupath',
      'the script will continue but these objects will be ignored')
      df = df[df.geometry.geom_type != 'MultiPolygon']

   st.success('The file QC is complete')
   logger.success("GeoJSON file QC performed")
   return df

def load_and_QC_SamplesandWells(samples_and_wells: dict):
   """Loads and checks for common errors.

   Checks for:
   If it is empty
   gdf samples are in saw
   Provided wells inside normal 384 well plate
   """
   logger.info("Checking samples and wells")
   if st.session_state.gdf is None:
      logger.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
      st.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
      st.stop()
   if st.session_state.calibs is None:
      logger.error("Calibration points were not accesible directly")
      st.error("Calibration points were not accesible directly")
      st.stop()

   gdf_samples = st.session_state['gdf'].classification_name.unique().tolist()
   allowed_wells = utils.create_list_of_acceptable_wells(plate="384", margins=0)

   samples = samples_and_wells.keys()
   wells = samples_and_wells.values()

   # Is it empty?
   if not isinstance(samples_and_wells,dict):
      raise ValueError("samples and wells is not a dict")
   if not samples_and_wells: #if empty
      raise ValueError("dictionary is empty")

   # gdf samples in saw
   missing = set(gdf_samples) - samples
   if missing:
      logger.error(f"Classes in geodataframe, but not in samples and wells: {missing}")
      st.error(f"Classes in geodataframe, but not in samples and wells: {missing}")
      # st.stop()

   # wells inside allowable wells
   crazy_wells = set(wells) - set(allowed_wells)
   if crazy_wells:
      logger.error(f"Wells not existing in 384wp: {crazy_wells}")
      st.error(f"Wells not existing in 384wp: {crazy_wells}")
      st.stop()

   logger.success('The samples and wells scheme QC is done!')
   st.success('The samples and wells scheme QC is done!')

def make_classes_unique(classes_to_modify: list):
   """Modifies the GeoDataFrame in session state to make specified class names unique.

   For each row of a specified class, a unique suffix is added to its 'classification_name'.
   """
   logger.info("Splitting specified classes into replicates")
   if 'gdf' not in st.session_state or st.session_state.gdf is None:
      logger.error("GeoDataFrame not found. Please load a GeoJSON file first.")
      st.error("GeoDataFrame not found. Please load a GeoJSON file first.")
      st.stop()

   gdf = st.session_state.gdf.copy()

   # Keep track of original names for the download
   if 'original_classification_name' not in gdf.columns:
      gdf['original_classification_name'] = gdf['classification_name']

   for class_name in classes_to_modify:
      # Find all rows that match the original class_name
      # Important to match on the original name in case of multiple runs
      matching_indices = gdf[gdf['original_classification_name'] == class_name].index

      if not matching_indices.empty:
         # Generate new unique names for these rows
         for i, idx in enumerate(matching_indices):
               new_name = f"{class_name}_{str(i+1).zfill(3)}"
               gdf.loc[idx, 'classification_name'] = new_name

   #replace old classification name inside classification dict
   gdf = utils.update_classification_column(gdf=gdf)

   # Update the session state
   st.session_state.gdf = gdf

   logger.success("GeoDataFrame updated with unique class names.")
   st.success("GeoDataFrame updated with unique class names.")


def create_collection():
   """Creates XML from geojson and returns file contents."""
   logger.info("Creating collection")
   # streamlit checks
   if st.session_state.gdf is None:
      st.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
      st.stop()
   if st.session_state.calibs is None:
      st.error("Calibration points were not accesible directly")
      st.stop()
   if st.session_state.saw is None:
      st.error("Samples and wells were not accesible")
      st.stop()

   df = st.session_state.gdf.copy()
   df['coords'] = df.geometry.simplify(1).apply(utils.extract_coordinates)
   logger.info("Simplified geometries")

   the_collection = Collection(calibration_points = st.session_state.calib_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])

   # for i in df.index:
   #    the_collection.new_shape(
   #       df.at[i,'coords'],
   #       well = st.session_state.saw[df.at[i, "classification_name"]])

   for i in df.index:
      classification = df.at[i, "classification_name"]
      if classification in st.session_state.saw:
         the_collection.new_shape(
               df.at[i, 'coords'],
               well=st.session_state.saw[classification]
         )

   logger.debug("Added shapes to collection")

   image_path = "./TheCollection.png"
   the_collection.plot(save_name=image_path)
   logger.info(f"{the_collection.stats()}")
   st.write(the_collection.stats())

   xml_content = ""
   # Use a temporary file for the XML output
   fd, path = tempfile.mkstemp(suffix=".xml", text=True)
   try:
       the_collection.save(path)
       with os.fdopen(fd, 'r') as tmpfile:
           xml_content = tmpfile.read()
   finally:
       os.remove(path)

   df_wp384 = utils.sample_placement()
   csv_content = df_wp384.to_csv(index=True)

   logger.success("Collection created, exporting XML and QC")
   return xml_content, csv_content, image_path
