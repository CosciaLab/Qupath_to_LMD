# Introduction

This repository includes all the code necesary to easily transform a Qupath geojson file to the Leica LMD7 compatible .xml file.
You can go through the Jupyter notebook locally on your own machine, or use the webapp version that simply requires a file upload.

## Qupath Annotations

0. Create a Qupath Project (optional)
1. Load images of interest
2. Draw annotations
3. Classify annotations using QuPath classes
4. Add at least 3 calibration points using point tool
5. Export annotations as a **FeatureCollection** in the **.geojson** format
6. Load into webapp or jupyter notebook (see below)

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

Go to ([https://qupathtolmd-hpqovu7trk3fob2xz5uefy.streamlit.app/](https://qupathtolmdv2-mgnybwsyhjxuenqj8c3gzm.streamlit.app/))  
1. Upload your geojson file.  
2. Write down your calibration points  
3. Paste in your samples_and_wells dictionary (see below)
4. Click Run the script, and download your output file.


## What is the "samples_and_wells" scheme

It is the text, written in the format of a python dictionary, that allows the code to understand to which well will the countours be cut into. 

This is an example:
```
{   
"Class_name_1" : "C3",  
"Class_name_2" : "C5",  
"Class_name_3" : "C7",  
}  
```
Each "Class_name_" is the exact name of the class of annotation found in Qupath.
The "C3", "C5", "C7" strings determine into which well are the contours going to be collected.
Works for both 384-well plates and 96-well plates
Note: For 384 well plate users, in the LMD7 system, do not use rows A or B, nor columns 1 or 2. The system struggles to collect to these wells.

# Youtube Tutorial 

https://www.youtube.com/watch?v=tyvHjttjSBE

# Citation
Please cite the following work when using this package.

Makhmut, A. et al. A framework for ultra-low input spatial tissue proteomics. bioRxiv 2023.05.13.540426 (2023) doi:10.1101/2023.05.13.540426.   

Please use the APA format for Github repositories:   
Nimo, J. (2023). Qupath_to_LMD: A tool to transform QuPath annotations to LMD coordenates. GitHub. [https://github.com/CosciaLab/Qupath_to_LMD](https://github.com/CosciaLab/Qupath_to_LMD/)

# FAQ


(1) I have a KeyError type of error, what do I do? 

KeyError is usually because your samples_and_wells does not match your geojson file.
Check them, they have to be exactly the same.

(2) Not sure if your .geojson file is the correct format? 

Check the example_input folder in the repository to see how they should look like. 

(3) I have an error what do I do? 

Create a gihtub issue explaning what are you doing and pasting the Traceback (the code that is trying to tell you what went wrong)
