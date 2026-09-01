"""Unit tests for permit_zones.py."""

from __future__ import annotations

import geopandas as gpd
import shapely.geometry

from parking_pipeline.permit_zones import (
    DEFAULT_PERMIT_HOURS,
    PermitZoneIndex,
)


def test_permit_zone_index() -> None:
    poly_1c = shapely.geometry.Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    poly_12a = shapely.geometry.Polygon([(10, 10), (15, 10), (15, 15), (10, 15)])

    gdf = gpd.GeoDataFrame(
        {
            'AREA_LONG_CODE': ['1C', '12A'],
            'AREA_NAME': ['1C', '12A'],
        },
        geometry=[poly_1c, poly_12a],
        crs='EPSG:4326',
    )

    idx = PermitZoneIndex(gdf)

    # Point in 1C
    pt_1c = shapely.geometry.Point(2, 2)
    assert idx.find_permit_area(pt_1c) == '1C'
    tag_1c = idx.tag_feature(pt_1c)
    assert tag_1c['permit_area_id'] == '1C'
    assert tag_1c['permit_parking_active'] is True
    assert tag_1c['permit_hours_default'] == DEFAULT_PERMIT_HOURS

    # Line in 12A
    line_12a = shapely.geometry.LineString([(11, 11), (14, 14)])
    assert idx.find_permit_area(line_12a) == '12A'
    tag_12a = idx.tag_feature(line_12a)
    assert tag_12a['permit_area_id'] == '12A'
    assert tag_12a['permit_parking_active'] is True

    # Point outside
    pt_out = shapely.geometry.Point(50, 50)
    assert idx.find_permit_area(pt_out) is None
    tag_out = idx.tag_feature(pt_out)
    assert tag_out['permit_area_id'] is None
    assert tag_out['permit_parking_active'] is False
    assert tag_out['permit_hours_default'] is None
