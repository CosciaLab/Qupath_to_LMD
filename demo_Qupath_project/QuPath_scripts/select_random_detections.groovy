// Define the desired number of random detections to select
// You will be prompted to enter this value when the script runs
def numToSelect = Dialogs.showIntegerInputDialog(
    'Random Detection Selection',
    'Enter the number of detections to randomly select:',
    100
)

// Check if the user cancelled the input
if (numToSelect == null) {
    Dialogs.showMessageDialog('Random Detection Selection', 'Script cancelled by user.')
    return
}

// Get the current PathData object
def pathData = getCurrentPathData()
if (pathData == null) {
    Dialogs.showMessageDialog('Random Detection Selection', 'No image open.')
    return
}

// Get the currently selected annotation
def annotation = getCurrentAnnotation()
if (annotation == null) {
    Dialogs.showMessageDialog('Random Detection Selection', 'Please select an annotation first.')
    return
}

// 1. Get all detections within the selected annotation
// The "getChildren" method with a filter can find all detections (DetectionObjects)
// that are immediate children of the annotation or contained within its hierarchy.
// Using PathObject.Class.DETECTION filters for detection type objects.
def allDetections = pathData.getObjects(PathObject.Class.DETECTION).findAll {
    annotation.getROI().contains(it.getROI().getCentroidX(), it.getROI().getCentroidY())
}

if (allDetections.isEmpty()) {
    Dialogs.showMessageDialog('Random Detection Selection', 'No detections found inside the selected annotation.')
    return
}

// Determine the actual number of detections to select (limit to total available)
def actualNumToSelect = Math.min(numToSelect, allDetections.size())

// 2. Randomly select the desired number of detections
// Create a shuffled list of the detections and take the first 'actualNumToSelect' elements.
// This is an efficient way to get a random sample without replacement.
def randomDetections = allDetections.asType(List).with {
    Collections.shuffle(it)
    it.take(actualNumToSelect)
}

// 3. Update the object hierarchy (if needed, this step ensures we're on the latest data)
// and set the 'Selected' property for the random sample.
PathData.executeInBackground(pathData, 'Randomly selecting detections') {
    // Clear the 'Selected' status from all detections first (optional, but good practice)
    allDetections.each { it.setSelected(false) }

    // Set the randomly selected detections as 'Selected'
    randomDetections.each { it.setSelected(true) }

    // Fire an event to update the display
    pathData.fireHierarchyUpdate()

    // Log the result
    println "Successfully selected ${actualNumToSelect} detections out of ${allDetections.size()} found within the annotation."
}