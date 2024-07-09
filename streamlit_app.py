import geopandas
import pandas
import numpy
import tifffile
import shapely
import streamlit as st
from lmd.lib import SegmentationLoader
from lmd.lib import Collection, Shape
from lmd import tools
from PIL import Image
from pathlib import Path
import ast
import string

from loguru import logger
import sys
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")

st.title("Convert a GeoJSON polygons to xml for Laser Microdissection")
st.subheader("From Jose Nimo, PhD at AG Coscia in the Max Delbrueck Center for Molecular Medicine in Berlin")
st.divider()

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)

def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
      else:
         st.write(f'Geometry type {geometry.geom_type} not supported, please convert to Polygon or LineString in Qupath')
         st.stop()

def load_and_QC_geojson_file(geojson_path: str, list_of_calibpoint_names: list = ['calib1','calib2','calib3']):
   """
   This function loads a geojson file and checks for common issues that might arise when converting it to xml for LMD

   Parameters:
   geojson_path (str): path to the geojson file
   list_of_calibpoint_names (list): list of calibration point names in the geojson file

   Returns:
   None

   Latest update: 29.04.2024 by Jose Nimo
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
         logger.error(f'Your given annotation_name {point_name} is not present in the file')
         logger.error(f'These are the calib points you passed: {list_of_calibpoint_names}')
         logger.error(f"These are the calib points found in the geojson you gave me: {df[df['geometry'].geom_type == 'Point']['annotation_name']}")

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
   # df['classification_name'] = df['classification'].apply(lambda x: x.get('name'))
   #get classification name from inside geometry properties
   df['classification_name'] = df['classification'].apply(lambda x: x.get('name') if isinstance(x, dict) else logger.warning(f"Classification is not a dictionary: {x}"))
   
   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      st.write('MultiPolygon objects present:  \n')
      st.table(df[df.geometry.geom_type == 'MultiPolygon'][['annotation_name','classification_name']])
      st.write('these are not supported, please convert them to polygons in Qupath  \n',
      'the script will continue but these objects will be ignored')
      df = df[df.geometry.geom_type != 'MultiPolygon']

   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)

   st.success('The file QC is complete')

calibration_point_1 = st.text_input("Enter the name of the first calibration point: ",  placeholder ="calib1")
calibration_point_2 = st.text_input("Enter the name of the second calibration point: ", placeholder ="calib2")
calibration_point_3 = st.text_input("Enter the name of the third calibration point: ",  placeholder ="calib3")
list_of_calibpoint_names = [calibration_point_1, calibration_point_2, calibration_point_3]

if st.button("Load and check the geojson file"):
   if uploaded_file is not None:
      load_and_QC_geojson_file(geojson_path=uploaded_file, list_of_calibpoint_names=list_of_calibpoint_names)
   else:
      st.warning("Please upload a file first.")

samples_and_wells_input = st.text_area("Enter the desired samples and wells scheme")

def create_list_of_acceptable_wells():
   list_of_acceptable_wells =[]
   for row in list(string.ascii_uppercase[1:14]):
      for column in range(2,22):
         list_of_acceptable_wells.append(str(row) + str(column))
   return list_of_acceptable_wells

def create_default_samples_and_wells(df):
   list_of_acceptable_wells = create_list_of_acceptable_wells()
   samples_and_wells = {}
   for sample in df["Name"]:
      samples_and_wells[sample] = list_of_acceptable_wells.pop(0)
   return samples_and_wells

def load_and_QC_SamplesandWells(samples_and_wells_input):

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   # check if the wells are in the list of acceptable wells
   for key, value in samples_and_wells.items():
      if value not in create_list_of_acceptable_wells():
         logger.error(f"Your well {value} for sample {key} is not in the list of acceptable wells")
         logger.error(f"Please choose wells that are not rows (A,O) not columns (1,22,23,24)")
         sys.exit()

def load_and_QC_SamplesandWells(geojson_path, list_of_calibpoint_names, samples_and_wells_input):

   df = geopandas.read_file(geojson_path)
   df = df[df['name'].isin(list_of_calibpoint_names) == False]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']

   df['Name'] = df['classification'].apply(lambda x: x.get('name'))

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   list_of_acceptable_wells = create_list_of_acceptable_wells()

   logger.debug("Checking if the wells are in the list of acceptable wells")
   for well in samples_and_wells.values():
      if well not in list_of_acceptable_wells:
         st.write(f'Your well {well} is not in the list of acceptable wells, please correct it',
         'the LMD is not able to collect into this well, the script will stop here')
         st.stop()

   logger.debug("Checking if the names of geometries are in the samples_and_wells dictionary")
   for name in df.Name.unique():
      if name not in samples_and_wells.keys():
         st.write(f'Your name {name} is not in the list of samples_and_wells, please correct either',
         'please change the class name in Qupath or add it to the samples_and_wells dictionary',
         'and then rerun the web app')
         st.stop()

   st.success('The samples and wells scheme QC is done!')

if st.button("Check the samples and wells"):
   samples_and_wells = load_and_QC_SamplesandWells(samples_and_wells_input=samples_and_wells_input, 
                                                   geojson_path=uploaded_file, 
                                                   list_of_calibpoint_names=list_of_calibpoint_names)

def sample_placement_384wp(samples_and_wells):

   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)

   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i

   return df_wp384

def create_collection(geojson_path, list_of_calibpoint_names, samples_and_wells_input):

   df = geopandas.read_file(geojson_path)

   calib_np_array = numpy.array(
      [[ df.loc[df['name'] == point_name, 'geometry'].values[0].x,
         df.loc[df['name'] == point_name, 'geometry'].values[0].y] 
         for point_name in list_of_calibpoint_names])

   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']
   
   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)
   df['Name'] = df['classification'].apply(lambda x: x.get('name'))


   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(df.at[i,'coords'], well = samples_and_wells[df.at[i, "Name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f'./{uploaded_file.name.replace("geojson", "xml")}')
   
   df_wp384 = sample_placement_384wp(samples_and_wells)
   df_wp384.to_csv(f'./{uploaded_file.name.replace("geojson", "_384_wellplate.csv")}', index=True)

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
