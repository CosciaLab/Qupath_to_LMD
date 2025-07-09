# Introduction

QuPath-to-LMD is the easiest way to go from QuPath annotations to LMD collection!
With more than 60 unique users, we try to help everyone collect their tissues.

## Qupath Annotations

0. Create a Qupath Project (optional)
1. Load images of interest
2. Draw annotations
3. Classify annotations using QuPath classes
4. Add at least 3 calibration points using point tool
5. Export annotations as a **FeatureCollection** in the **.geojson** format
6. Load into webapp or jupyter notebook (see below)

## Streamlit webapp

Go to [Streamlit Webapp Link](https://qupath-to-lmd-mdcberlin.streamlit.app/)
1. Upload your geojson file.  
2. Write down your calibration points  
3. Paste in your samples_and_wells dictionary (see below)
4. Click Run the script, and download your output file.


# Youtube Tutorial 

Version 1: https://www.youtube.com/watch?v=tyvHjttjSBE


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



# Citation
Please cite the following work when using this package.
Makhmut, A. et al. A framework for ultra-low-input spatial tissue proteomics. Cell Syst. 14, 1002-1014.e5 (2023).

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

(4) I have different number of replicates per category of samples?

Either you create a set of classes that includes unnecessary classes and remove the ones you don't need from the samples and wells, or you create a set of classes that includes most samples, and then add the samples that have more replicates.

(5) Can I somehow set a threshold of how much area to annotate per class?

Not algorithmically. Options are: You manually sum the area per class as you annotate, QuPath has measurements per annotation that you can filter by class. OR, you can limit the collection at the LMD7 software (>8).

(6) What if I want to collect various slides of tissue into the same 384wp

I suggest you create a set of QuPath classes that include all slides, make sure they are unique (Slide1_celltypeA_control_1). Then annotate as normal and export a .geojson file per slide. 
Then you should create a samples and wells scheme that includes all classes from all slides. Process each .geojson file with the same samples and wells scheme, and collect one slide at a time. 

(7) How should I position my calibration points?

The closer the three calibration points are to the annotations the less distortion you are going to suffer.
Your tolerance for distortion depends on the size of your annotations (single cells will suffer greatly, mini-bulk less so).

For example:

<img width="300" alt="bad_calibpoints" src="https://github.com/user-attachments/assets/887f7afc-fedb-438b-b00c-bbbd2a524f6f" />

In this image the small contours at the top will likely suffer distortion leading the collection to be of different tissue than the one annotated for.

The solution is to separate into two different sets of contours with closer calibration points:
![better_calib_points_1](https://github.com/user-attachments/assets/4eb068b8-afbb-4cd7-9ed8-790dd7622950)
![better_calib_points_2](https://github.com/user-attachments/assets/6111132c-72dd-48fb-ae9f-0b04a01ede86)

