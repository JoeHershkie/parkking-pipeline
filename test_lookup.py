import geopandas as gpd

print("Loading Intersection Database (This might take a few seconds)...")
# Load the dataset
intersections_gdf = gpd.read_file('tcl_intersections.geojson')
print("Database loaded successfully!\n")


def normalize_street_name(street_name):
    """
    Translates full street names into the City of Toronto's standard abbreviations.
    """
    name = str(street_name).lower().strip()

    # The standard Toronto Centreline dictionary
    replacements = {
        ' street': ' st',
        ' road': ' rd',
        ' avenue': ' ave',
        ' boulevard': ' blvd',
        ' drive': ' dr',
        ' crescent': ' cres',
        ' court': ' ct',
        ' place': ' pl',
        ' square': ' sq',
        ' terrace': ' terr',
        ' trail': ' trl',
        ' west': ' w',
        ' east': ' e',
        ' north': ' n',
        ' south': ' s'
    }

    for full_word, abbreviation in replacements.items():
        name = name.replace(full_word, abbreviation)

    return name


def find_intersection_coordinate(street_1, street_2):
    """
    Searches the local intersection database for the exact point where two streets meet.
    """
    if not street_1 or not street_2:
        return None

    # NORMALIZE the inputs before searching!
    s1 = normalize_street_name(street_1)
    s2 = normalize_street_name(street_2)

    column_to_search = 'INTERSECTION_DESC'

    # We use regex=False for speed, and na=False to drop blank rows safely
    match = intersections_gdf[
        intersections_gdf[column_to_search].str.lower().str.contains(s1, regex=False, na=False) &
        intersections_gdf[column_to_search].str.lower().str.contains(s2, regex=False, na=False)
        ]

    if not match.empty:
        # Get the centroid to make it a single Point
        intersection_point = match.iloc[0].geometry.centroid
        return intersection_point
    else:
        # Print what it actually searched for to help with debugging
        print(f"  [Debug: Searched for '{s1}' & '{s2}']")
        return None


# --- RUNNING THE TESTS ---
if __name__ == "__main__":
    # Test Case 1: A standard intersection
    print("Test 1: Dovercourt Road & Argyle Street")
    pt1 = find_intersection_coordinate("Dovercourt Road", "Argyle Street")
    print(f"Result: {pt1}\n")

    # Test Case 2: Order shouldn't matter (Reversed)
    print("Test 2: Argyle Street & Dovercourt Road")
    pt2 = find_intersection_coordinate("Argyle Street", "Dovercourt Road")
    print(f"Result: {pt2}\n")

    # Test Case 3: An intersection that definitely doesn't exist
    print("Test 3: Fake Street & Moon Boulevard")
    pt3 = find_intersection_coordinate("Fake Street", "Moon Boulevard")
    print(f"Result: {pt3}\n")