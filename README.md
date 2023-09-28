# Introduction

This repository includes all the code necesary to easily transforms a Qupath geojson file to LMD-compatible .xml file.

You can go through the Jupyter notebook locally in your own machine, or you can go to a webapp version that simply requires a file upload.

## Jupyter notebook

To run the Jupyter notebook you have to create a local environment with the right packages.  
Please follow the instructions at https://github.com/MannLabs/py-lmd. (thanks to them this is possible)  
Then please install these extra packages with the following command:  
`conda install geojson geopandas shapely ipykernel`

There are three inputs for the jupyter notebook.
1. The path to your geoJSON file
2. The directory to save your output files
3. The samples_and_wells dictionary (see below for instructions)


## Streamlit webapp

Go to (https://qupathtolmd-hpqovu7trk3fob2xz5uefy.streamlit.app/)  
Upload your geojson file.  
Write down your calibration points  
Paste in your samples_and_wells dictionary, you do not have to paste the "samples_and_wells = " part, just paste the curly bracket part to it.  

Click Run the script, and download your output file.


## The samples and wells dictionary

Is the object that allows us to inform the LMD to which wells do we want which contours.
It is a python dictionary that follows the following structure:

{   
"Class_name_1" : "C3",  
"Class_name_2" : "C5",  
"Class_name_3" : "C7",  
}  

Each "Class_name_" is the exact name of the class of annotation found in Qupath.  
And the following string is the target well. works for both 384-well plates and 96-well plates


# Youtube Tutorial 

https://www.youtube.com/watch?v=tyvHjttjSBE


# FAQ

(1) I have an error what do I do? 

Create a gihtub issue explaning what are you doing and pasting the Traceback (the code that is trying to tell you what went wrong)

(2) I have a KeyError type of error, what do I do? 

KeyError is usually because your samples_and_wells does not match your geojson file.
Check them, they have to be exactly the same.
