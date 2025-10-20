App will check geojson

check for duplicate class names

if duplicate class names

    check if class name has "scDVP" in it 
    else: WARNING


check user plate setting

    check expected number of wells needed = classes + (scDVP * rows of scDVP)

    if too many samples: WARNING, but allow

fill samples at random in plate:

    first non scDVP

    then fill remaining with scDVP samples

create a geojson where scDVP class is exploded and numbers are given to replicates, this will be needed for spatialdata stuff.

show user expected setup, allow to download as csv or png :)

allow optional samples and wells

WARNING about potential 384-well plate shift.
