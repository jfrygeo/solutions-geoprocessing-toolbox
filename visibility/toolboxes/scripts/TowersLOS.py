
#------------------------------------------------------------------------------
# Copyright 2014 Esri
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#------------------------------------------------------------------------------

# Import arcpy module
import os, sys, traceback, math, decimal
import arcpy
from arcpy import env
from arcpy import sa
def zfactor(dataset):
    desc = arcpy.Describe(dataset)
    # if it's not geographic return 1.0
    if desc.spatialReference.type != "Geographic":
        return 1.0
    extent = desc.Extent
    extent_split = [extent.xmin,extent.ymin,extent.xmax,extent.ymax]

    top = float(extent_split[3])
    bottom = float(extent_split[1])

    #find the mid-latitude of the dataset
    if (top > bottom):
        height = (top - bottom)
        mid = (height/2) + bottom
    elif (top < bottom):  # Unlikely, but just in case
        height = bottom - top
        mid = (height/2) + top
    else: # top == bottom
        mid = top

    # convert degrees to radians
    mid = math.radians(mid)

    # Find length of degree at equator based on spheroid's semi-major axis
    spatial_reference = desc.SpatialReference
    semi_major_axis = spatial_reference.semiMajorAxis # in meters
    equatorial_length_of_degree = ((2.0 * math.pi * float(semi_major_axis))/360.0)

    # function:
    # Z-Factor = 1.0/(111320 * cos(mid-latitude in radians))
    decimal.getcontext().prec = 28
    decimal.getcontext().rounding = decimal.ROUND_UP
    a = decimal.Decimal("1.0")
    b = decimal.Decimal(str(equatorial_length_of_degree))
    c = decimal.Decimal(str(math.cos(mid)))
    zfactor = a/(b * c)
    zfactor = "%06f" % (zfactor.__abs__())
    return zfactor

def updateValue(fc, field, value):
    cursor = arcpy.UpdateCursor(fc)
    for row in cursor:
        row.setValue(field, value)
        cursor.updateRow(row)
    return

DEBUG = True

desktopVersion = ["10.2.2","10.3","10.3.1"]
proVersion = ["1.0"]

# Get the parameters
input_surface = arcpy.GetParameterAsText(0) #Input Surface
RADIUS2_to_infinity = arcpy.GetParameterAsText(1) #Force visibility to infinity
if RADIUS2_to_infinity == 'true':
    arcpy.AddMessage("RLOS to infinity will use horizon for calculation.")
    RADIUS2_to_infinity = True
else:
    arcpy.AddMessage("RLOS will use local RADIUS2 values for calculation.")
    RADIUS2_to_infinity = False
towerFC = arcpy.GetParameterAsText(2) #Defensive Position Feature Class
# The name of the field within towerFC that contains a plain name for the tower
towerNameField = arcpy.GetParameterAsText(3) #Defensive Position Description Field
# The name of the field within towerFC that contains a height value for the tower
towerHeightField = arcpy.GetParameterAsText(4) #Defensive Position Height Field
# The name of the workspace in which the features should be stored
outWorkspace = arcpy.GetParameterAsText(5) #Output Workspace
# An optional prefix to add to the names of the feature classes generated by this script.
outFeatureClassPrefix = arcpy.GetParameterAsText(6) #Output Prefix

if outFeatureClassPrefix == '#' or not outFeatureClassPrefix:
    outFeatureClassPrefix = "LOS"
scrubbedFeatureClassPrefix = ''.join(e for e in outFeatureClassPrefix if (e.isalnum() or e == " " or e == "_"))
scrubbedFeatureClassPrefix = scrubbedFeatureClassPrefix.replace(" ", "_")
if scrubbedFeatureClassPrefix[0].isdigit():
    scrubbedFeatureClassPrefix = "LOS_" + scrubbedFeatureClassPrefix

if DEBUG == True:
    arcpy.AddMessage("Input surface is " + input_surface)
    arcpy.AddMessage("Force visibility is " + str(RADIUS2_to_infinity))
    arcpy.AddMessage("Tower Feature Class is " + towerFC)
    arcpy.AddMessage("Tower Name Field is " + towerNameField)
    arcpy.AddMessage("Tower Height Field is " + towerHeightField)
    arcpy.AddMessage("Output Visibility is " + outWorkspace)
    arcpy.AddMessage("Output Feature Class Prefix is " + outFeatureClassPrefix)
    arcpy.AddMessage("Scrubbed Feature Class Prefix is " + scrubbedFeatureClassPrefix)
    arcpy.AddMessage("Setting snap raster to: " + input_surface)
env.snapRaster = input_surface

arcpy.SelectLayerByAttribute_management(towerFC, "CLEAR_SELECTION")
counter = 0
outputBaseName = scrubbedFeatureClassPrefix
sr = arcpy.SpatialReference()
sr.factoryCode = 4326
sr.create()
GCS_WGS_1984 = sr
#GCS_WGS_1984 = arcpy.SpatialReference(r"WGS 1984")
env.overwriteOutput = True
terrestrial_refractivity_coefficient = 0.13
polygon_simplify = "SIMPLIFY"
delete_me = []
allOutputVizFeatures = []

# Process: sourceRLOSscript
 # get/set initial environment
installInfo = arcpy.GetInstallInfo("desktop")
installDirectory = installInfo["InstallDir"]
try:

    # Loop through the tower feature class and get each height out to use for the calculations
    towerCount = arcpy.GetCount_management(towerFC).getOutput(0)
    cursor = arcpy.SearchCursor(towerFC)

    for row in cursor:
        counter += 1
        
        arcpy.AddMessage("Processing tower " + str(counter) + " of " + str(towerCount))
        
        towerHeight = row.getValue(towerHeightField)
        towerName = row.getValue(towerNameField)
        # Remove special characters and replace spaces with underscores
        scrubbedTowerName = ''.join(e for e in towerName if (e.isalnum() or e == " " or e == "_"))
        scrubbedTowerName = scrubbedTowerName.replace(" ", "_")
        if DEBUG == True:
            arcpy.AddMessage("...towerName: " + towerName)
            arcpy.AddMessage("...scrubbedTowerName: " + scrubbedTowerName)

        # Just the name of the feature class to create
        thisOutputFeatureClassName = outputBaseName + "_" +  scrubbedTowerName
        # The full path to the feature class, including the feature class itself
        thisOutputFeatureClass = os.path.join(outWorkspace, outputBaseName + "_" +  scrubbedTowerName)

        # get observer's vibility modifier maximums
        obsMaximums = {'SPOT':None,'OFFSETA':towerHeight, 'RADIUS2':4000, 'REMOVE_SPOT':False}

        # Copy defensive position to its own feature layer for calculations
        tower = arcpy.MakeFeatureLayer_management(towerFC, scrubbedTowerName, "OBJECTID = " + str(row.getValue("OBJECTID")))

        # Do a Minimum Bounding Geometry (MBG) on the input tower
        observers_mbg = os.path.join(env.scratchWorkspace,"observers_mbg_towerlos_" + scrubbedTowerName)
        delete_me.append(observers_mbg)
        arcpy.AddMessage("...Finding observer's minimum bounding envelope ...")
        arcpy.MinimumBoundingGeometry_management(tower,observers_mbg)

        # Now find the center of the (MBG)
        arcpy.AddMessage("...Finding center of tower ...")
        mbgCenterPoint = os.path.join(env.scratchWorkspace,"mbgCenterPoint_towerlos_" + scrubbedTowerName)
        mbgExtent = arcpy.Describe(observers_mbg).extent
        mbgSR = arcpy.Describe(observers_mbg).spatialReference
        mbgCenterX = mbgExtent.XMin + (mbgExtent.XMax - mbgExtent.XMin)
        mbgCenterY = mbgExtent.YMin + (mbgExtent.YMax - mbgExtent.YMin)
        arcpy.CreateFeatureclass_management(os.path.dirname(mbgCenterPoint),os.path.basename(mbgCenterPoint),"POINT","#","DISABLED","DISABLED",mbgSR)
        mbgShapeFieldName = arcpy.Describe(mbgCenterPoint).ShapeFieldName
        rows = arcpy.InsertCursor(mbgCenterPoint)
        feat = rows.newRow()
        feat.setValue(mbgShapeFieldName,arcpy.Point(mbgCenterX,mbgCenterY))
        rows.insertRow(feat)
        del rows
        delete_me.append(mbgCenterPoint)

        # Get the proper distance radius, tower height (offset), and z_factor
        maxRad = obsMaximums['RADIUS2']
        maxOffset = obsMaximums['OFFSETA']
        z_factor = float(zfactor(tower))
        horizonDistance = 0.0

        if RADIUS2_to_infinity == True:
            # if going to infinity what we really need is the distance to the horizon based on height/elevation
            arcpy.AddMessage("...Finding horizon distance ...")
            result = arcpy.GetCellValue_management(input_surface, str(mbgCenterX) + " " + str(mbgCenterY))
            centroid_elev = result.getOutput(0)
            R2 = float(centroid_elev) + float(maxOffset)
            R = 6378137.0 # length, in meters, of semimajor axis of WGS_1984 spheroid.
            horizonDistance = math.sqrt(math.pow((R + R2),2) - math.pow(R,2))
            arcpy.AddMessage("..." + str(horizonDistance) + " meters.")
            horizonExtent = str(mbgCenterX - horizonDistance) + " " + str(mbgCenterY - horizonDistance) + " " + str(mbgCenterX + horizonDistance) + " " + str(mbgCenterY + horizonDistance)
        else:
            pass

        # reset center of AZED using Lat/Lon of MBG center point
        # Project point to WGS 84
        arcpy.AddMessage("...Recentering Azimuthal Equidistant to centroid ...")
        mbgCenterWGS84 = os.path.join(env.scratchWorkspace,"mbgCenterWGS84")
        arcpy.Project_management(mbgCenterPoint,mbgCenterWGS84,GCS_WGS_1984)
        arcpy.AddXY_management(mbgCenterWGS84)
        pointx = 0.0
        pointy = 0.0
        shapeField = arcpy.Describe(mbgCenterWGS84).ShapeFieldName
        rows = arcpy.SearchCursor(mbgCenterWGS84)
        for row2 in rows:
            feat = row2.getValue(shapeField)
            pnt = feat.getPart()
            pointx = pnt.X
            pointy = pnt.Y
        del row2
        del rows

        # write new central meridian and latitude of origin...
        strAZED = 'PROJCS["World_Azimuthal_Equidistant",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Azimuthal_Equidistant"],PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",' + str(pointx) + '],PARAMETER["Latitude_Of_Origin",' + str(pointy) + '],UNIT["Meter",1.0],AUTHORITY["ESRI",54032]]'
        delete_me.append(mbgCenterWGS84)

        # Determine the proper buffer distance based on whether visibility should be generated to Infinity
        bufferDistance = obsMaximums['RADIUS2']
        if RADIUS2_to_infinity == True:
            arcpy.AddMessage(r"...Running viewshed to infinity...")
            bufferDistance = horizonDistance
            if DEBUG == True:
                arcpy.AddMessage(r"...Creating a buffer from the observer points to the horizion distance.  infinity (horizon): " + str(bufferDistance))
        else:
            arcpy.AddMessage(r"...Running viewshed to the maximum observer radius...")
            if DEBUG == True:
                arcpy.AddMessage(r"...Creating a buffer from the observer points to the horizion distance.  maximum observer radius: " + str(bufferDistance))

        # Create the processing buffer to the appropriate distance
        mbgBuffer = os.path.join(env.scratchWorkspace,"mbgBuffer_towerslos")
        arcpy.Buffer_analysis(observers_mbg,mbgBuffer,obsMaximums['RADIUS2'])
        delete_me.append(mbgBuffer)
        if DEBUG == True:
            arcpy.AddMessage(r"...Projecting the buffer to Azimuthal Equidistant")
        mbgBufferPrj = os.path.join(env.scratchWorkspace,"mbgBuffersPrj_towerlos_" + scrubbedTowerName)
        arcpy.Project_management(mbgBuffer,mbgBufferPrj,strAZED)
        delete_me.append(mbgBufferPrj)
        mbgBufferPrjExtent = arcpy.Describe(mbgBufferPrj).extent
        if DEBUG == True:
            arcpy.AddMessage(r"...Setting procesing extent to: " + str(mbgBufferPrjExtent))
        env.extent = mbgBufferPrjExtent

        # Project surface to the new AZED
        extract_prj = os.path.join(env.scratchWorkspace,"extract_prj_towerlos_" + scrubbedTowerName)
        arcpy.AddMessage(r"...Projecting surface ...")
        arcpy.ProjectRaster_management(input_surface,extract_prj,strAZED)
        delete_me.append(extract_prj)

        # Project tower to the new AZED

        obs_prj = os.path.join(env.scratchWorkspace,"obs_prj_towerlos")
        arcpy.AddMessage(r"...Projecting tower ...")
        arcpy.Project_management(tower,obs_prj,strAZED)

        #Add viewshed-utilized fields
        if DEBUG == True: arcpy.AddMessage(r"...Adding OFFSETA field to: " + str(obs_prj))
        arcpy.AddField_management(obs_prj, "OFFSETA", "DOUBLE", "", "", "", "Observer Offset", "NULLABLE", "NON_REQUIRED", "")
        arcpy.CalculateField_management(obs_prj, "OFFSETA", maxOffset, "PYTHON", "")
        if DEBUG == True: arcpy.AddMessage(r"...Adding RADIUS2 field to: " + str(obs_prj))
        arcpy.AddField_management(obs_prj, "RADIUS2", "DOUBLE", "", "", "", "Farthest distance", "NULLABLE", "NON_REQUIRED", "")
        arcpy.CalculateField_management(obs_prj, "RADIUS2", maxRad, "PYTHON", "")
        delete_me.append(obs_prj)

        # make a layer
        obs_prjTowerName = "tower" + str(counter)
        obs_prjTower = arcpy.MakeFeatureLayer_management(obs_prj, obs_prjTowerName, "OBJECTID = " + str(row.getValue("OBJECTID")))


        # Project the MBG buffer to AZED
        obs_buf = os.path.join(env.scratchWorkspace,"obs_buf_towerlos_" + scrubbedTowerName)
        arcpy.Project_management(mbgBufferPrj,obs_buf,strAZED)
        delete_me.append(obs_buf)

        # Finally ... run Viewshed
        arcpy.AddMessage(r"...Calculating Viewshed ...")
        vshed = os.path.join(env.scratchWorkspace,"vshed_towerlos")
        delete_me.append(vshed)
        #Use the visibility tool instead of the viewshed. This will allow is to set the observation height offset for the tower
        #outVshed = sa.Visibility(extract_prj,obs_prjTower,z_factor=z_factor,curvature_correction="CURVED_EARTH",refractivity_coefficient=terrestrial_refractivity_coefficient,observer_offset=maxOffset)
        outVshed = sa.Viewshed(extract_prj,obs_prjTower,z_factor,"CURVED_EARTH",terrestrial_refractivity_coefficient)
        outVshed.save(vshed)

        # Raster To Polygon
        arcpy.AddMessage(r"...Converting to polygons ...")
        ras_poly = os.path.join(env.scratchWorkspace,"ras_poly_towerlos_" + scrubbedTowerName)
        arcpy.RasterToPolygon_conversion(vshed,ras_poly,polygon_simplify)
        delete_me.append(ras_poly)

        # clip output polys to buffer
        if RADIUS2_to_infinity != True:
            out_buf = os.path.join(env.scratchWorkspace,"out_buf_towerlos_" + scrubbedTowerName)
            arcpy.Buffer_analysis(obs_prjTower,out_buf,obsMaximums['RADIUS2'])
            delete_me.append(out_buf)
            arcpy.Clip_analysis(ras_poly,out_buf,thisOutputFeatureClass)
        else:
            arcpy.CopyFeatures_management(ras_poly, thisOutputFeatureClass)

        # Process: Add Field
        arcpy.AddField_management(thisOutputFeatureClass, "visibility", "DOUBLE", "", "", "", "Observer Visibility", "NULLABLE", "NON_REQUIRED", "")
        arcpy.CalculateField_management(thisOutputFeatureClass, "visibility", "!gridcode!", "PYTHON", "")

        # Add the layer to the map
        layerSymFolder = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'layers'))
        if DEBUG == True: arcpy.AddMessage(r"...layerSymFolder dirname: " + str(os.path.dirname(__file__)))
        
        # Apply proper symbology file type by version
        gisVersion = arcpy.GetInstallInfo()["Version"]
        if DEBUG == True: arcpy.AddMessage(r"...gisVersion: " + str(gisVersion))
        if gisVersion in desktopVersion: #This is ArcMap 10.3 or 10.2.2
            mdoc = arcpy.mapping.MapDocument
            mxd = arcpy.mapping.MapDocument('CURRENT')
            df = arcpy.mapping.ListDataFrames(mxd)[0]
            layerToAdd = arcpy.mapping.Layer(thisOutputFeatureClass)
            layerFile = os.path.join(layerSymFolder,r"Radial Line Of Sight Output.lyr")
            arcpy.ApplySymbologyFromLayer_management(layerToAdd, layerFile)
            arcpy.mapping.AddLayer(df, layerToAdd, "AUTO_ARRANGE")
            
        elif gisVersion in proVersion: #This Is  ArcGIS Pro  1.0+
            aprx = arcpy.mp.ArcGISProject(r"current")
            m = aprx.listMaps()[0]
            sourceLayerFilePath = os.path.join(layerSymFolder,r"Radial Line Of Sight Output.lyrx") # might need LYRX for this one.
            sourceLayerFile = arcpy.mp.LayerFile(sourceLayerFilePath)
            sourceLayer = sourceLayerFile.listLayers()[0]
            sourceLayer.dataSource = thisOutputFeatureClass
            sourceLayer.name = sourceLayer.name + r": " + towerName
            lyrInMap = m.addLayer(sourceLayer,"AUTO_ARRANGE")[0]
            del lyrInMap, m, aprx

        else:
            arcpy.AddWarning(r"...Could not determine version.\n   Looking for ArcMap " + str(desktopVersion) + ", or ArcGIS Pro " + str(proVersion) + ".\n   Found " + str(gisVersion))
            arcpy.AddWarning(r"..." + str(thisOutputFeatureClass) + " will be added to Output Workspace, but will not be added to the map.")
            
    
        #Add to list of output feature classes
        allOutputVizFeatures.append(thisOutputFeatureClass)    
        
    #Set output to list of feature classes.
    if DEBUG == True: arcpy.AddMessage("allOutputVisFeatures: " + str(allOutputVizFeatures))
    arcpy.SetParameter(7,allOutputVizFeatures)

except arcpy.ExecuteError:
    # Get the tool error messages
    msgs = arcpy.GetMessages()
    arcpy.AddError(msgs)
    #print msgs #UPDATE
    print(msgs)

except:
    # Get the traceback object
    tb = sys.exc_info()[2]
    tbinfo = traceback.format_tb(tb)[0]

    # Concatenate information together concerning the error into a message string
    pymsg = "PYTHON ERRORS:\nTraceback info:\n" + tbinfo + "\nError Info:\n" + str(sys.exc_info()[1])
    msgs = "\nArcPy ERRORS:\n" + arcpy.GetMessages() + "\n"

    # Return python error messages for use in script tool or Python Window
    arcpy.AddError(pymsg)
    arcpy.AddError(msgs)

    # Print Python error messages for use in Python / Python Window
    #print pymsg + "\n" #UPDATE
    print(pymsg + "\n")
    #print msgs #UPDATE
    print(msgs)

finally:
    # cleanup intermediate datasets
    if DEBUG == True: arcpy.AddMessage("Removing intermediate datasets...")
    #for i in delete_me:
     #   if DEBUG == True: arcpy.AddMessage("Removing: " + str(i))
      #  arcpy.Delete_management(i)
    if DEBUG == True: arcpy.AddMessage("Done")
