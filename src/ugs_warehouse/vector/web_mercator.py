"""EPSG:3857 as PROJJSON, for the `crs` of the tile-ready GeoParquet levels (sink_archive.py).

GeoParquet wants the CRS as PROJJSON. This is pyproj's `CRS.from_epsg(3857).to_json_dict()`
without its `$schema`, kept here so the vector ingest needs no pyproj.
"""
import json

PROJJSON = json.loads(
    '{"type":"ProjectedCRS","name":"WGS 84 / '
    'Pseudo-Mercator","base_crs":{"type":"GeographicCRS","name":"WGS '
    '84","datum_ensemble":{"name":"World Geodetic System 1984 ensemble","members":[{"name":"World'
    ' Geodetic System 1984 (Transit)","id":{"authority":"EPSG","code":1166}},{"name":"World '
    'Geodetic System 1984 (G730)","id":{"authority":"EPSG","code":1152}},{"name":"World Geodetic '
    'System 1984 (G873)","id":{"authority":"EPSG","code":1153}},{"name":"World Geodetic System '
    '1984 (G1150)","id":{"authority":"EPSG","code":1154}},{"name":"World Geodetic System 1984 '
    '(G1674)","id":{"authority":"EPSG","code":1155}},{"name":"World Geodetic System 1984 '
    '(G1762)","id":{"authority":"EPSG","code":1156}},{"name":"World Geodetic System 1984 '
    '(G2139)","id":{"authority":"EPSG","code":1309}},{"name":"World Geodetic System 1984 '
    '(G2296)","id":{"authority":"EPSG","code":1383}}],"ellipsoid":{"name":"WGS 84","semi_major_ax'
    'is":6378137,"inverse_flattening":298.257223563},"accuracy":"2.0","id":{"authority":"EPSG","c'
    'ode":6326}},"coordinate_system":{"subtype":"ellipsoidal","axis":[{"name":"Geodetic '
    'latitude","abbreviation":"Lat","direction":"north","unit":"degree"},{"name":"Geodetic longit'
    'ude","abbreviation":"Lon","direction":"east","unit":"degree"}]},"id":{"authority":"EPSG","co'
    'de":4326}},"conversion":{"name":"Popular Visualisation '
    'Pseudo-Mercator","method":{"name":"Popular Visualisation Pseudo '
    'Mercator","id":{"authority":"EPSG","code":1024}},"parameters":[{"name":"Latitude of natural '
    'origin","value":0,"unit":"degree","id":{"authority":"EPSG","code":8801}},{"name":"Longitude '
    'of natural '
    'origin","value":0,"unit":"degree","id":{"authority":"EPSG","code":8802}},{"name":"False '
    'easting","value":0,"unit":"metre","id":{"authority":"EPSG","code":8806}},{"name":"False nort'
    'hing","value":0,"unit":"metre","id":{"authority":"EPSG","code":8807}}]},"coordinate_system":'
    '{"subtype":"Cartesian","axis":[{"name":"Easting","abbreviation":"X","direction":"east","unit'
    '":"metre"},{"name":"Northing","abbreviation":"Y","direction":"north","unit":"metre"}]},"scop'
    'e":"Web mapping and visualisation.","area":"World between 85.06\\u00b0S and 85.06\\u00b0N.","b'
    'box":{"south_latitude":-85.06,"west_longitude":-180,"north_latitude":85.06,"east_longitude":'
    '180},"id":{"authority":"EPSG","code":3857}}'
)
