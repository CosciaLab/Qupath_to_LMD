// Get all currently selected objects in the viewer
def selectedObjects = getSelectedObjects()

// Filter the list to keep only the detection objects
def detectionsToConvert = selectedObjects.findAll { it.isDetection() }

// Check if any detections were actually selected
if (detectionsToConvert.isEmpty()) {
    print 'No detections selected. Please select one or more detections and run the script again.'
    return // Stop the script if no detections are selected
}

// Use the filtered list to create new annotation objects
def newAnnotations = detectionsToConvert.collect {
    return PathObjects.createAnnotationObject(it.getROI(), it.getPathClass())
}

// Remove the original detections that were converted
removeObjects(detectionsToConvert, true)

// Add the new annotations to the object hierarchy
addObjects(newAnnotations)

print "Successfully converted ${newAnnotations.size()} selected detection(s) to annotation(s)."