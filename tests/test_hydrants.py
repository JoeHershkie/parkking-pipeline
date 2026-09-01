"""Unit tests for hydrants.py."""

from __future__ import annotations

import geopandas as gpd
import shapely.geometry

from parking_pipeline.hydrants import (
    WGS84_CRS,
    FireHydrantIndex,
    project_to_utm,
    project_to_wgs84,
)


def test_fire_hydrant_projection_and_exclusions() -> None:
    # Set up line in UTM coords (approx Toronto UTM 17N: ~630000 E, 4830000 N)
    line_utm = shapely.geometry.LineString([(630000, 4830000), (630100, 4830000)])
    line_wgs84 = project_to_wgs84(line_utm)

    # Place a hydrant at (630050, 4830003) - 3m perpendicular from midpoint
    hydrant_pt_utm = shapely.geometry.Point(630050, 4830003)
    hydrant_pt_wgs84 = project_to_wgs84(hydrant_pt_utm)

    gdf = gpd.GeoDataFrame(
        {
            '_id': [1],
            'FACILITYID': ['HY1001'],
        },
        geometry=[hydrant_pt_wgs84],
        crs=WGS84_CRS,
    )

    idx = FireHydrantIndex(gdf)

    # Tag feature
    tag = idx.tag_feature(line_wgs84)
    assert tag['has_hydrant'] is True
    assert tag['hydrant_count'] == 1
    assert tag['hydrant_facility_ids'] == ['HY1001']
    assert tag['hydrant_setback_m'] == 3.0

    # Compute curb exclusions
    exclusions = idx.compute_curb_exclusions(line_wgs84, setback_m=3.0)
    assert len(exclusions) == 1
    excl_utm = project_to_utm(exclusions[0])
    # The exclusion length should be 6.0m (3m on either side)
    assert abs(excl_utm.length - 6.0) < 1e-3


def test_fire_hydrant_out_of_range() -> None:
    line_utm = shapely.geometry.LineString([(630000, 4830000), (630100, 4830000)])
    line_wgs84 = project_to_wgs84(line_utm)

    # Place a hydrant far away (630050, 4830050) -> 50m away
    hydrant_pt_wgs84 = project_to_wgs84(shapely.geometry.Point(630050, 4830050))

    gdf = gpd.GeoDataFrame(
        {
            '_id': [2],
            'FACILITYID': ['HY2002'],
        },
        geometry=[hydrant_pt_wgs84],
        crs=WGS84_CRS,
    )

    idx = FireHydrantIndex(gdf)
    tag = idx.tag_feature(line_wgs84, max_snap_m=12.0)
    assert tag['has_hydrant'] is False
    assert tag['hydrant_count'] == 0
    assert idx.compute_curb_exclusions(line_wgs84, max_snap_m=12.0) == []
