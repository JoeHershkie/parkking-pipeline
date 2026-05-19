import pandas as pd
import geopandas as gpd
import ast
from shapely.geometry import LineString, MultiLineString
from shapely.ops import substring, transform, linemerge
import pyproj

print("1. Loading Local Intersection Database...")
intersections_gdf = gpd.read_file('tcl_intersections.geojson')

print("2. Loading Local Street Database (This might take a moment)...")
streets_gdf = gpd.read_file('tcl_streets.geojson')  # Update filename if needed


# --- HELPERS ---

def normalize_intersection_street(street_name):
    """Only used for the intersection lookup, since that database is abbreviated."""
    name = str(street_name).lower().strip()
    replacements = {
        ' street': ' st', ' road': ' rd', ' avenue': ' ave',
        ' boulevard': ' blvd', ' drive': ' dr', ' crescent': ' cres',
        ' court': ' ct', ' place': ' pl', ' square': ' sq',
        ' terrace': ' terr', ' trail': ' trl', ' west': ' w',
        ' east': ' e', ' north': ' n', ' south': ' s'
    }
    for full, abbr in replacements.items():
        name = name.replace(full, abbr)
    return name


def find_intersection(street_1, street_2):
    if not street_1 or not street_2: return None
    s1 = normalize_intersection_street(street_1)
    s2 = normalize_intersection_street(street_2)

    match = intersections_gdf[
        intersections_gdf['INTERSECTION_DESC'].str.lower().str.contains(s1, regex=False, na=False) &
        intersections_gdf['INTERSECTION_DESC'].str.lower().str.contains(s2, regex=False, na=False)
        ]
    if not match.empty:
        return match.iloc[0].geometry.centroid
    return None


def get_local_street_geometry(street_name):
    """Queries your local hard drive for the street shape instead of the internet."""
    s_name = str(street_name).strip().lower()

    # Match against the full legal name column we found!
    street_chunks = streets_gdf[streets_gdf['LINEAR_NAME_FULL_LEGAL'].str.lower() == s_name]

    if street_chunks.empty:
        return None

    # Extract geometries and handle MultiLineStrings safely
    geoms = []
    for g in street_chunks.geometry:
        if g.geom_type == 'LineString':
            geoms.append(g)
        elif g.geom_type == 'MultiLineString':
            geoms.extend(list(g.geoms))

    if not geoms:
        return None

    # Stitch the street fragments together
    merged = linemerge(MultiLineString(geoms))

    if merged.geom_type == 'MultiLineString':
        return max(merged.geoms, key=lambda x: x.length)

    return merged


# --- THE GEOMETRY ENGINE ---

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
project_to_gps = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform


def slice_street(highway, parsed_data):
    rule_type = parsed_data.get('rule_type')

    # Get street geometry from local data
    street_line_gps = get_local_street_geometry(highway)
    if not street_line_gps: return None

    street_line_m = transform(project_to_meters, street_line_gps)

    if rule_type == 'entire_length':
        return street_line_gps

    if rule_type in ['intersect_extension', 'block']:
        start_intersection = parsed_data.get('start_intersection')

        # Get starting Point A coordinate
        start_pt_gps = find_intersection(highway, start_intersection)
        if not start_pt_gps: return None

        start_pt_m = transform(project_to_meters, start_pt_gps)
        start_dist_along_line = street_line_m.project(start_pt_m)

        if rule_type == 'intersect_extension':
            distance = float(parsed_data.get('distance', 0))
            end_dist_along_line = start_dist_along_line + distance

        elif rule_type == 'block':
            # For blocks, find Point B coordinate
            end_intersection = parsed_data.get('end_intersection')
            end_pt_gps = find_intersection(highway, end_intersection)
            if not end_pt_gps: return None

            end_pt_m = transform(project_to_meters, end_pt_gps)
            end_dist_along_line = street_line_m.project(end_pt_m)

        # Slice it!
        sliced_line_m = substring(street_line_m, start_dist_along_line, end_dist_along_line)
        return transform(project_to_gps, sliced_line_m)

    return None


# --- EXECUTION ---
if __name__ == "__main__":
    print("3. Loading Parsed Successes CSV...")
    df = pd.read_csv('parsed_successes.csv')

    # Test on the first 50 rows now that it's instant!
    test_df = df.head(13750).copy()

    results = []
    print("4. Slicing Streets Locally...")

    for index, row in test_df.iterrows():
        try:
            parsed = ast.literal_eval(row['parsed_data'])
            final_geom = slice_street(row['Highway'], parsed)

            if final_geom and not final_geom.is_empty:
                results.append({
                    'Highway': row['Highway'],
                    'Rule': row['Prohibited Times and/or Days'],
                    'geometry': final_geom
                })

        except Exception as e:
            pass  # Keep moving for the batch test

    print(f"\n5. Exporting {len(results)} zones to GeoJSON...")
    if results:
        gdf = gpd.GeoDataFrame(results, geometry='geometry')
        gdf.set_crs(epsg=4326, inplace=True)
        gdf.to_file("final_parking_map.geojson", driver="GeoJSON")
        print("Done! Open 'final_parking_map.geojson' to see your local work.")