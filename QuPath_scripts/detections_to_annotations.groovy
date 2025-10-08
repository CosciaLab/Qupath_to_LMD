def detections = getDetectionObjects()
def newAnnotations = detections.collect {
    return PathObjects.createAnnotationObject(it.getROI(), it.getPathClass())
}
removeObjects(detections, true)
addObjects(newAnnotations)