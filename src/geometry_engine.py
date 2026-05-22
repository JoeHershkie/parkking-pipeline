import ast
from collections import Counter
from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
import pyproj
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge, substring, transform

from failure_ledger import clear_stage, record_failure
from paths import data_path

STREET_NOT_FOUND = 'STREET_NOT_FOUND'
INTERSECTION_NOT_FOUND = 'INTERSECTION_NOT_FOUND'
UNSUPPORTED_RULE_TYPE = 'UNSUPPORTED_RULE_TYPE'
GEOMETRY_ERROR = 'GEOMETRY_ERROR'

UNSUPPORTED_RULE_TYPES = frozenset({
    'perfect_offset',
    'intersect_to_offset',
    'offset_to_intersect',
    'relative_extension',
})

SUPPORTED_RULE_TYPES = frozenset({
    'entire_length',
    'intersect_extension',
    'block',
})

print("1. Loading Local Intersection Database...")
intersections_gdf = gpd.read_file(data_path('tcl_intersections.geojson'))

print("2. Loading Local Street Database (This might take a moment)...")
streets_gdf = gpd.read_file(data_path('tcl_streets.geojson'))


@dataclass
class SliceResult:
    geometry: LineString | MultiLineString | None
    reason_code: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.reason_code is None and self.geometry is not None


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
    if not street_1 or not street_2:
        return None
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

    street_chunks = streets_gdf[streets_gdf['LINEAR_NAME_FULL_LEGAL'].str.lower() == s_name]

    if street_chunks.empty:
        return None

    geoms = []
    for g in street_chunks.geometry:
        if g.geom_type == 'LineString':
            geoms.append(g)
        elif g.geom_type == 'MultiLineString':
            geoms.extend(list(g.geoms))

    if not geoms:
        return None

    merged = linemerge(MultiLineString(geoms))

    if merged.geom_type == 'MultiLineString':
        return max(merged.geoms, key=lambda x: x.length)

    return merged


# --- THE GEOMETRY ENGINE ---

project_to_meters = pyproj.Transformer.from_crs(4326, 32617, always_xy=True).transform
project_to_gps = pyproj.Transformer.from_crs(32617, 4326, always_xy=True).transform


def slice_street(highway, parsed_data) -> SliceResult:
    rule_type = parsed_data.get('rule_type')

    if rule_type in UNSUPPORTED_RULE_TYPES:
        return SliceResult(None, UNSUPPORTED_RULE_TYPE, rule_type)

    if rule_type not in SUPPORTED_RULE_TYPES:
        detail = f"rule_type={rule_type!r}"
        return SliceResult(None, UNSUPPORTED_RULE_TYPE, detail)

    street_line_gps = get_local_street_geometry(highway)
    if not street_line_gps:
        return SliceResult(None, STREET_NOT_FOUND, str(highway))

    try:
        street_line_m = transform(project_to_meters, street_line_gps)

        if rule_type == 'entire_length':
            return SliceResult(street_line_gps)

        if rule_type in ('intersect_extension', 'block'):
            start_intersection = parsed_data.get('start_intersection')
            start_pt_gps = find_intersection(highway, start_intersection)
            if not start_pt_gps:
                return SliceResult(
                    None,
                    INTERSECTION_NOT_FOUND,
                    f"start_intersection={start_intersection}",
                )

            start_pt_m = transform(project_to_meters, start_pt_gps)
            start_dist_along_line = street_line_m.project(start_pt_m)

            if rule_type == 'intersect_extension':
                distance = float(parsed_data.get('distance', 0))
                end_dist_along_line = start_dist_along_line + distance

            else:
                end_intersection = parsed_data.get('end_intersection')
                end_pt_gps = find_intersection(highway, end_intersection)
                if not end_pt_gps:
                    return SliceResult(
                        None,
                        INTERSECTION_NOT_FOUND,
                        f"end_intersection={end_intersection}",
                    )

                end_pt_m = transform(project_to_meters, end_pt_gps)
                end_dist_along_line = street_line_m.project(end_pt_m)

            sliced_line_m = substring(street_line_m, start_dist_along_line, end_dist_along_line)
            return SliceResult(transform(project_to_gps, sliced_line_m))

    except Exception as e:
        return SliceResult(None, GEOMETRY_ERROR, str(e)[:500])

    return SliceResult(None, UNSUPPORTED_RULE_TYPE, f"rule_type={rule_type!r}")


# --- EXECUTION ---
if __name__ == "__main__":
    print("3. Loading Parsed Successes CSV...")
    df = pd.read_csv(data_path('parsed_successes.csv'))

    test_df = df.head(13750).copy()

    clear_stage('geo')
    results = []
    failure_counts = Counter()
    print("4. Slicing Streets Locally...")

    for index, row in test_df.iterrows():
        row_id = row['_id']
        highway = row['Highway']
        between = row['Between']

        try:
            parsed = ast.literal_eval(row['parsed_data'])
        except (ValueError, SyntaxError) as e:
            detail = f"invalid parsed_data: {e}"
            record_failure(row_id, 'geo', GEOMETRY_ERROR, detail, highway, between)
            failure_counts[GEOMETRY_ERROR] += 1
            continue

        try:
            result = slice_street(highway, parsed)
        except Exception as e:
            detail = str(e)[:500]
            record_failure(row_id, 'geo', GEOMETRY_ERROR, detail, highway, between)
            failure_counts[GEOMETRY_ERROR] += 1
            continue

        if result.ok and not result.geometry.is_empty:
            results.append({
                'Highway': highway,
                'Rule': row['Prohibited Times and/or Days'],
                'geometry': result.geometry,
            })
        elif result.reason_code:
            record_failure(
                row_id, 'geo', result.reason_code, result.detail, highway, between,
            )
            failure_counts[result.reason_code] += 1
        else:
            detail = 'empty geometry'
            record_failure(row_id, 'geo', GEOMETRY_ERROR, detail, highway, between)
            failure_counts[GEOMETRY_ERROR] += 1

    print(f"\n5. Exporting {len(results)} zones to GeoJSON...")
    print(f"   Successes: {len(results)}")
    if failure_counts:
        print("   Geo failures by reason:")
        for code, count in failure_counts.most_common():
            print(f"     {code}: {count}")

    if results:
        gdf = gpd.GeoDataFrame(results, geometry='geometry')
        gdf.set_crs(epsg=4326, inplace=True)
        out_path = data_path('final_parking_map.geojson')
        gdf.to_file(out_path, driver="GeoJSON")
        print(f"Done! Open '{out_path}' to see your local work.")
