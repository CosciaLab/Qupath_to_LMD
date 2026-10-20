// Export every image of a QuPath project for QuPath-to-LMD, one .geojson per image.
//
// Run with: Automate > Script editor > Run for project (select the images to export).
// Writes <project folder>/lmd_export/<image name>.geojson. Zip that folder and upload the zip,
// or upload the files themselves. Each file becomes one slide in the app.
//
// Everything is exported: shapes, the named calibration points, and measurements (which let the
// app estimate the image scale). Name three calibration points on every image before running.

import qupath.lib.common.GeneralTools

def entry = getProjectEntry()
def imageName = GeneralTools.getNameWithoutExtension(entry.getImageName())
def safeName = imageName.replaceAll('[^A-Za-z0-9._-]', '_')

def folder = buildFilePath(PROJECT_BASE_DIR, 'lmd_export')
mkdirs(folder)
def path = buildFilePath(folder, safeName + '.geojson')

def objects = getAllObjects(false)
exportObjectsToGeoJson(objects, path, "FEATURE_COLLECTION")

def points = objects.findAll { it.getROI() != null && it.getROI().isPoint() && it.getName() }
print "${imageName}: ${objects.size()} objects, ${points.size()} named points -> ${path}"
if (points.size() < 3) {
    print "WARNING: ${imageName} has fewer than 3 named points; the app needs 3 calibration points per slide."
}
