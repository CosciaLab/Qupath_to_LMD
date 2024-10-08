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

st.title("Convert a GeoJSON polygons to xml for Laser Microdissection, 26.04.2024")
st.subheader("From Jose Nimo, PhD at AG Coscia in the Max Delbrueck Center for Molecular Medicine in Berlin")
st.divider()

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)


def load_and_QC_geojson_file(geojson_path: str, list_of_calibpoint_names: list = ['calib1','calib2','calib3']):
   """
   This function loads a geojson file and checks for common issues that might arise when converting it to xml for LMD

   Parameters:
   geojson_path (str): path to the geojson file
   list_of_calibpoint_names (list): list of calibration point names in the geojson file

   Returns:
   None

   Latest update: 26.04.2024 by Jose Nimo

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

   #save calib points in a list
   caliblist = []
   for point_name in list_of_calibpoint_names:
      if point_name in df['annotation_name'].unique():
         caliblist.append(df.loc[df['annotation_name'] == point_name, 'geometry'].values[0])
      else:
         logger.error(f'Your given annotation_name {point_name} is not present in the file')
         logger.error(f'These are the calib points you passed: {list_of_calibpoint_names}')
         logger.error(f"These are the calib points found in the geojson you gave me: {df[df['geometry'].geom_type == 'Point']['annotation_name']}")
         st.write('Your given annotation_name is not present in the file  \n', 
         f'These are the calib points you passed: {list_of_calibpoint_names}  \n',
         f"These are the calib points found in the geojson you gave me: ")
         st.table(df[df['geometry'].geom_type == 'Point']['annotation_name'])
   
   #remove points
   df = df[df['geometry'].apply(lambda geom: not isinstance(geom, shapely.geometry.Point))]
   logger.info(f"Point geometries have been removed")

   #check and remove empty classifications
   if df['classification'].isna().sum() !=0 :
      st.write(f"you have {df['classification'].isna().sum()} NaNs in your classification column",
            "these are unclassified objects from Qupath, they will be ignored") 
      df = df[df['classification'].notna()]
   
   #get classification name from inside geometry properties
   df['classification_name'] = df['classification'].apply(lambda x: x.get('name'))

   #create coordenate list
   listarray = []
   for point in caliblist:
      listarray.append([point.x, point.y])
   calib_np_array = numpy.array(listarray)

   #now that calibration points are saved, remove them from the dataframe
   df = df[df['annotation_name'].isin(list_of_calibpoint_names) == False]
   
   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      st.write('MultiPolygon objects present:  \n')
      st.table(df[df.geometry.geom_type == 'MultiPolygon'][['annotation_name','classification_name']])
      st.write('these are not supported, please convert them to polygons in Qupath  \n',
      'the script will continue but these objects will be ignored')
      df = df[df.geometry.geom_type != 'MultiPolygon']

   def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
      else:
         st.write(f'Geometry type {geometry.geom_type} not supported, please convert to Polygon or LineString in Qupath')
         st.stop()

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

def load_and_QC_SamplesandWells(samples_and_wells_input, geojson_path, list_of_calibpoint_names):

   df = geopandas.read_file(geojson_path)
   df = df[df['name'].isin(list_of_calibpoint_names) == False]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']

   df['Name'] = numpy.nan
   for i in df.index:
      tmp = df.classification[i].get('name')
      df.at[i,'Name'] = tmp

   # parse common human copy paste formats
   # remove newlines
   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   # remove spaces
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   # parse into python dictionary
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   #create list of acceptable wells, default is using a space in between columns
   list_of_acceptable_wells =[]
   #Allowing columns 2->22, rows B->N
   for row in list(string.ascii_uppercase[1:14]):
      for column in range(2,23):
         list_of_acceptable_wells.append(str(row) + str(column))

   #check for improper wells
   for well in samples_and_wells.values():
      if well not in list_of_acceptable_wells:
            st.write(f'Your well {well} is not in the list of acceptable wells for 384 well plate, please correct it',
            'ask an expert if unsure, the script will continue')
            # st.stop(), let users know, but don't stop the script

   df['Name'] = numpy.nan
   for i in df.index:
      tmp = df.classification[i].get('name')
      df.at[i,'Name'] = tmp

   #check that names in df are all present in the samples and wells
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


def create_collection(geojson_path, list_of_calibpoint_names, samples_and_wells_input ):

   ###### dataframe processing ######

   df = geopandas.read_file(geojson_path)

   caliblist = []
   for point_name in list_of_calibpoint_names:
      if point_name in df['name'].unique():
            caliblist.append(df.loc[df['name'] == point_name, 'geometry'].values[0])
      else:
            st.stop('Your given name is not present in the file')
   
   listarray = []
   for point in caliblist:
      listarray.append([point.x, point.y])
   calib_np_array = numpy.array(listarray)

   df = df[df['name'].isin(list_of_calibpoint_names) == False]
   df = df[df['classification'].notna()]
   df = df[df.geometry.geom_type != 'MultiPolygon']

   def extract_coordinates(geometry):
      if geometry.geom_type == 'Polygon':
         return [list(coord) for coord in geometry.exterior.coords]
      elif geometry.geom_type == 'LineString':
         return [list(coord) for coord in geometry.coords]
   
   df['coords'] = df.geometry.simplify(1).apply(extract_coordinates)

   df['Name'] = numpy.nan
   for i in df.index:
      tmp = df.classification[i].get('name')
      df.at[i,'Name'] = tmp

   ###### samples and wells processing ######

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   ###### create the collection ######

   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(df.at[i,'coords'], well = samples_and_wells[df.at[i, "Name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f'./{uploaded_file.name.replace("geojson", "xml")}')
   
   #create and export dataframe with sample placement in 384 well plate
   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pandas.DataFrame('',columns=columns_1_24, index=rows_A_P)
   #fill in the dataframe with samples and wells
   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i
   #save dataframe as csv
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
