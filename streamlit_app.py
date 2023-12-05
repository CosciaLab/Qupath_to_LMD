import geopandas
import pandas
import numpy
import datetime
datetime = datetime.datetime.today().strftime("%Y%m%d")
import tifffile
import shapely
import streamlit as st
from python_functions import dataframe_to_xml_v2
from lmd.lib import SegmentationLoader
from lmd.lib import Collection, Shape
from lmd import tools
from PIL import Image
from pathlib import Path
import ast
import string


st.title("Convert a GeoJSON polygons to xml for Laser Microdissection")
st.subheader("From Jose Nimo, PhD at AG Coscia in the Max Delbrueck Center for Molecular Medicine in Berlin")
st.divider()

uploaded_file = st.file_uploader("Choose a file", accept_multiple_files=False)


def load_and_QC_geojson_file(geojson_path: str, list_of_calibpoint_names: list = ['calib1','calib2','calib3']):

   #load geojson file
   df = geopandas.read_file(geojson_path)

   #save calib points in a list
   caliblist = []
   for point_name in list_of_calibpoint_names:
      if point_name in df['name'].unique():
            caliblist.append(df.loc[df['name'] == point_name, 'geometry'].values[0])
      else:
            st.write('Your given name is not present in the file', 
            f'These are the calib points you passed: {list_of_calibpoint_names}',
            f"These are the calib points found in the geojson you gave me: {df['name'].unique()}")
   #create coordenate list
   listarray = []
   for point in caliblist:
      listarray.append([point.x, point.y])
   calib_np_array = numpy.array(listarray)

   #now that calibration points are saved, remove them from the dataframe
   df = df[df['name'].isin(list_of_calibpoint_names) == False]

   #check and remove empty classifications 
   if df['classification'].isna().sum() !=0 :
      st.write(f"you have {df['classification'].isna().sum()} NaNs in your classification column",
            "these are unclassified objects from Qupath, they will be ignored") 
      df = df[df['classification'].notna()]

   #check for MultiPolygon objects
   if 'MultiPolygon' in df.geometry.geom_type.value_counts().keys():
      st.write('MultiPolygon objects present:',
      #print out the classification name of the MultiPolygon objects
      f"{df[df.geometry.geom_type == 'MultiPolygon']['classification']}", 
      'these are not supported, please convert them to polygons in Qupath',
      'the script will continue but these objects will be ignored')
      #remove MultiPolygon objects
      df = df[df.geometry.geom_type != 'MultiPolygon']

   # reformat shape coordenate list
   df['coords'] = numpy.nan
   df['coords'] = df['coords'].astype('object')
   # simplify to reduce number of points
   df['simple'] = df.geometry.simplify(1)
   df['coords'] = df['simple'].apply(lambda geom: numpy.array(list(geom.exterior.coords)))

   #extract classification name into a new column
   df['Name'] = numpy.nan
   for i in df.index:
      tmp = df.classification[i].get('name')
      df.at[i,'Name'] = tmp

   st.write('The file loading is complete')

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

def load_and_QC_SamplesandWells(samples_and_wells_input, geojson_path):

   df = geopandas.read_file(geojson_path)

   # parse common human copy paste formats
   # remove newlines
   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   # remove spaces
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   #parse into python dictionary
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   #create list of acceptable wells, default is using a space in between columns
   list_of_acceptable_wells =[]
   for row in list(string.ascii_uppercase[2:14]):
      for column in range(2,22):
         list_of_acceptable_wells.append(str(row) + str(column))

   #check for improper wells
   for well in samples_and_wells.values():
      if well not in list_of_acceptable_wells:
            st.write(f'Your well {well} is not in the list of acceptable wells, please correct it',
            'the LMD is not able to collect into this well, the script will stop here')
            st.stop()

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

   st.write('The samples and wells scheme QC is done!')

if st.button("Check the samples and wells"):
   samples_and_wells = load_and_QC_SamplesandWells(samples_and_wells_input=samples_and_wells_input, df_csv=f"./{datetime}_QCed_geojson.csv")


def create_collection(geojson_path, list_of_calibpoint_names, samples_and_wells_input ):

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
   df['simple'] = df.geometry.simplify(1)
   df['coords'] = numpy.nan
   df['coords'] = df['coords'].astype('object')
   df['coords'] = df['simple'].apply(lambda geom: numpy.array(list(geom.exterior.coords)))

   df['Name'] = numpy.nan
   for i in df.index:
      tmp = df.classification[i].get('name')
      df.at[i,'Name'] = tmp

   samples_and_wells_processed = samples_and_wells_input.replace("\n", "")
   samples_and_wells_processed = samples_and_wells_processed.replace(" ", "")
   samples_and_wells = ast.literal_eval(samples_and_wells_processed)

   #create the collection of py-lmd-env package
   #uses caliblist passed on the function, order matters
   #orientation vector is for QuPath coordenate system
   the_collection = Collection(calibration_points = calib_np_array)
   the_collection.orientation_transform = numpy.array([[1,0 ], [0,-1]])
   for i in df.index:
      the_collection.new_shape(df.at[i,'coords'], well = samples_and_wells[df.at[i, "Name"]])

   the_collection.plot(save_name= "./TheCollection.png")
   st.image("./TheCollection.png", caption='Your Contours', use_column_width=True)
   st.write(the_collection.stats())
   the_collection.save(f"./{datetime}_LMD_ready_contours.xml")
   
   #create and export dataframe with sample placement in 384 well plate
   rows_A_P= [i for i in string.ascii_uppercase[:16]]
   columns_1_24 = [str(i) for i in range(1,25)]
   df_wp384 = pd.DataFrame('',columns=columns_1_24, index=rows_A_P)
   #fill in the dataframe with samples and wells
   for i in samples_and_wells:
      location = samples_and_wells[i]
      df_wp384.at[location[0],location[1:]] = i
   #save dataframe as csv
   df_wp384.to_csv(f"./{datetime}_384_wellplate.csv", index=True)

if st.button("Process geojson and create the contours"):
   create_collection(geojson_path=uploaded_file,
                     list_of_calibpoint_names= list_of_calibpoint_names,
                     samples_and_wells_input=samples_and_wells_input)
   st.download_button("Download contours file", Path(f"./{datetime}_LMD_ready_contours.xml").read_text(), f"./{datetime}_LMD_ready_contours.xml")
   st.download_button("Download 384 plate scheme", Path(f"./{datetime}_384_wellplate.csv").read_text(), f"./{datetime}_384_wellplate.csv")
