import ast

import geopandas
import numpy
import shapely
import streamlit as st
from lmd.lib import Collection
from loguru import logger

from qupath_to_lmd.geojson_utils import extract_coordinates
# from qupath_to_lmd.utils import create_list_of_acceptable_wells, sample_placement_384wp
import qupath_to_lmd.utils as utils

@st.cache_data
def load_and_QC_geojson_file(geojson_path: str, simplify:int=1) -> geopandas.GeoDataFrame:
   """Checks and load the geojson.

   This function loads a geojson file and checks for common issues that might arise when converting it to xml for LMD

   Parameters:
   -------------
      geojson_path (str): path to the geojson file
      list_of_calibpoint_names (list): list of calibration point names in the geojson file

   Returns:
   -------------
      None
   """
   #check for streamlit variables
   if st.session_state.calibs is None:
      st.error("Calibration points were not accesible directly")
      st.stop()
   #load geojson file
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape}")

   if df.empty:
      st.warning("The uploaded geojson file is empty.")
      logger.warning("The uploaded geojson file is empty.")
      st.stop()

   if "name" not in df.columns:
      st.warning("No calibration points in file")
      st.stop()

   # describe geodataframe
   geometry_counts = df.geometry.geom_type.value_counts()
   log_message = ", ".join(f"{count} {geom_type}s" for geom_type, count in geometry_counts.items())
   logger.info(f"Geometries in DataFrame: {log_message}")
   st.write(f"Geometries in DataFrame: {log_message}")

   #check for calibration points
   for point_name in st.session_state.calibs:
      if point_name not in df['classification_name'].unique():
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
      st.write(f"you have {df['classification'].isna().sum()} NaNs in your classification column",
            "these are unclassified objects from Qupath, they will be ignored")
      df = df[df['classification'].notna()]

   #get classification name from inside geometry properties
   df['classification_name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      st.write('MultiPolygon objects present:')
      st.table(df[df.geometry.geom_type == 'MultiPolygon'][['annotation_name','classification_name']])
      st.write('these are not supported, please convert them to polygons in Qupath',
      'the script will continue but these objects will be ignored')
      df = df[df.geometry.geom_type != 'MultiPolygon']

   df['coords'] = df.geometry.simplify(simplify).apply(extract_coordinates)

   st.success('The file QC is complete')
   return df

def load_and_QC_SamplesandWells(samples_and_wells: dict):
   """Loads and checks for common errors.

   Checks for:
   If it is empty
   gdf samples are in saw
   Provided wells inside normal 384 well plate
   """
   if st.session_state.gdf is None:
      st.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
      st.stop()
   if st.session_state.calibs is None:
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
      st.error(f"Missing classes from gdf: {missing}")
      st.stop()

   # wells inside allowable wells
   crazy_wells = set(wells) - set(allowed_wells)
   if crazy_wells:
      st.error(f"Crazy wells: {crazy_wells}")
      st.stop()

   st.success('The samples and wells scheme QC is done!')

@st.cache_data
def create_collection(
   # geojson_path : str = None,
   # list_of_calibpoint_names :list = None, 
   samples_and_wells_input : str = None):

   if st.session_state.gdf is None:
      st.error("GeoDataFrame not found in session state. Please upload and process a GeoJSON file first.")
      st.stop()
   if st.session_state.calibs is None:
      st.error("Calibration points were not accesible directly")
      st.stop()
   if st.session_state.saw is None:
      st.error("Samples and wells were not accesible")
      st.stop()

   # df = geopandas.read_file(geojson_path)

   df = st.session_state.gdf.copy()

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in st.session_state.calibs])

   # df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   # df = df[df['classification'].notna()]
   # df = df[df.geometry.geom_type != 'MultiPolygon']

   # df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)
   # df['Name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   # if samples_and_wells_input:
   #    samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   #    samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   #    samples_and_wells = ast.literal_eval(samples_and_wells_processed)
   # else:
   #    st.write("Please add a samples and wells in step 2")
   #    logger.error("No samples and wells passed")

   #filter out shapes from df that are not in samples_and_wells
   # df = df[df['Name'].isin(samples_and_wells.keys())]

   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(
         df.at[i,'coords'],
         well = st.session_state.saw[df.at[i, "classification_name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f'./{geojson_path.name.replace("geojson", "xml")}')
   
   df_wp384 = sample_placement_384wp(samples_and_wells)
   df_wp384.to_csv(f'./{geojson_path.name.replace("geojson", "_384_wellplate.csv")}', index=True)
