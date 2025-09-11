import geopandas
import numpy
import shapely
import streamlit as st
from lmd.lib import Collection
import ast

from loguru import logger

from qupath_to_lmd.geojson_utils import extract_coordinates
from qupath_to_lmd.utils import create_list_of_acceptable_wells, sample_placement_384wp

@st.cache_data
def load_and_QC_geojson_file(
   geojson_path: str, 
   list_of_calibpoint_names: list = ['calib1','calib2','calib3']):
   """
   This function loads a geojson file and checks for common issues that might arise when converting it to xml for LMD

   Parameters:
   -------------
      geojson_path (str): path to the geojson file
      list_of_calibpoint_names (list): list of calibration point names in the geojson file

   Returns:
   -------------
      None
   """

   #load geojson file
   df = geopandas.read_file(geojson_path)
   logger.info(f"Geojson file loaded with shape {df.shape}")
   
   try:
      df['annotation_name'] = df['name']
   except:
      logger.warning('No name column found, meaning no annotation in Qupath was named, at least calibration points should be named')
      st.stop()

   geometry_counts = df.geometry.geom_type.value_counts()
   log_message = ", ".join(f"{count} {geom_type}s" for geom_type, count in geometry_counts.items())
   logger.info(f"Geometries in DataFrame: {log_message}")
   st.write(f"Geometries in DataFrame: {log_message}")

   #check for calibration points
   for point_name in list_of_calibpoint_names:
      if point_name not in df['annotation_name'].unique():
         st.write(f'Your given annotation_name >>{point_name}<< is not present in the file')
         st.write(f'These are the calib points you passed: {list_of_calibpoint_names}')
         st.write(f"These are the calib points found in the geojson: {df[df['geometry'].geom_type == 'Point']['annotation_name'].values}")
         logger.error(f'Your given annotation_name {point_name} is not present in the file')
         logger.error(f'These are the calib points you passed: {list_of_calibpoint_names}')
         logger.error(f"These are the calib points found in the geojson: {df[df['geometry'].geom_type == 'Point']['annotation_name'].values}")
         st.stop()

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in list_of_calibpoint_names])
   
   def polygon_intersects_triangle(polygon, triangle):
      if isinstance(polygon, shapely.Polygon):
         return polygon.intersects(triangle)
      elif isinstance(polygon, shapely.LineString):
         return polygon.intersects(triangle)
      else:
         return False  # Return False for other geometry types

   df['intersects'] = df['geometry'].apply(lambda x: polygon_intersects_triangle(x, shapely.Polygon(calib_np_array)))
   num_of_polygons_and_LineString = df[df['geometry'].geom_type.isin(['Polygon', 'LineString'])].shape[0]

   intersect_fraction = df['intersects'].sum()/num_of_polygons_and_LineString
   logger.info(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")
   st.write(f" {intersect_fraction*100:.2f}% of polygons are within calibration triangle")

   if intersect_fraction < 0.25:
      st.write('WARNING: Less than 25% of the objects intersect with the calibration triangle')
      logger.warning(f"Less than 25% of the objects intersect with the calibration triangle")
      logger.warning(f"Polygons will most likely be warped, consider changing calib points")
   
   #remove points
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")

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

   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)

   st.success('The file QC is complete')

@st.cache_data
def load_and_QC_SamplesandWells(geojson_path, list_of_calibpoint_names, samples_and_wells_input):

   df = geopandas.read_file(geojson_path)
   df = df[df['name'].isin(list_of_calibpoint_names) == False]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']

   df['Name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   samples_and_wells_processed = samples_and_wells_input.replace(" ", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   list_of_acceptable_wells = create_list_of_acceptable_wells()

   logger.debug("Checking if the wells are in the list of acceptable wells")
   warning_limit = 0
   for well in samples_and_wells.values():
      if well not in list_of_acceptable_wells:
         warning_limit +=1
         if warning_limit<10:
            st.write(f'Your well {well} is not in the list of acceptable wells for 384 well plate, please correct it')

   if warning_limit>=10:
      st.write(f'You have received +10 warnings about wells, I let you be, but I hope you know what you are doing!')

   logger.debug("Checking if the names of geometries are in the samples_and_wells dictionary")
   for name in df.Name.unique():
      if name not in samples_and_wells.keys():
         st.write(f'Your name "{name}" is not in the list of samples_and_wells',
         'Option A: change the class name in Qupath',
         'Option B: add it to the samples_and_wells dictionary',
         'Option C: ignore this, and these annotations will not be exported')

   st.success('The samples and wells scheme QC is done!')

@st.cache_data
def create_collection(
   geojson_path : str = None, 
   list_of_calibpoint_names :list = None, 
   samples_and_wells_input : str = None):

   df = geopandas.read_file(geojson_path)

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in list_of_calibpoint_names])

   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   
   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)
   df['Name'] = df['classification'].apply(lambda x: ast.literal_eval(x).get('name') if isinstance(x, str) else x.get('name'))

   if samples_and_wells_input:
      samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
      samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
      samples_and_wells = ast.literal_eval(samples_and_wells_processed)
   else:
      st.write("Please add a samples and wells in step 2")
      logger.error("No samples and wells passed")
   
   #filter out shapes from df that are not in samples_and_wells
   df = df[df['Name'].isin(samples_and_wells.keys())]

   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(df.at[i,'coords'], well = samples_and_wells[df.at[i, "Name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f'./{geojson_path.name.replace("geojson", "xml")}')
   
   df_wp384 = sample_placement_384wp(samples_and_wells)
   df_wp384.to_csv(f'./{geojson_path.name.replace("geojson", "_384_wellplate.csv")}', index=True)
