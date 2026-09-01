"""Unit tests for municipal_rules.py."""

from __future__ import annotations

import geopandas as gpd
import shapely.geometry

from parking_pipeline.municipal_rules import (
    REGIONAL_WINTER_RULES,
    MunicipalBoundaryIndex,
)


def test_municipal_boundary_index() -> None:
    # Create test boundaries for Scarborough, North York, Etobicoke
    poly_scarborough = shapely.geometry.Polygon([(10, 10), (20, 10), (20, 20), (10, 20)])
    poly_north_york = shapely.geometry.Polygon([(0, 10), (10, 10), (10, 20), (0, 20)])
    poly_etobicoke = shapely.geometry.Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])

    gdf = gpd.GeoDataFrame(
        {
            'AREA_NAME': ['SCARBOROUGH', 'NORTH YORK', 'ETOBICOKE'],
        },
        geometry=[poly_scarborough, poly_north_york, poly_etobicoke],
        crs='EPSG:4326',
    )

    idx = MunicipalBoundaryIndex(gdf)

    # Test point in Scarborough
    pt_sc = shapely.geometry.Point(15, 15)
    assert idx.find_municipality(pt_sc) == 'SCARBOROUGH'
    tag_sc = idx.tag_feature(pt_sc)
    assert tag_sc['former_municipality'] == 'SCARBOROUGH'
    assert tag_sc['regional_winter_rule'] == REGIONAL_WINTER_RULES['SCARBOROUGH']['prohibited_times']
    assert tag_sc['regional_winter_bylaw'] == 'Scarborough Code § 214-34'

    # Test line in North York
    line_ny = shapely.geometry.LineString([(2, 12), (8, 18)])
    assert idx.find_municipality(line_ny) == 'NORTH YORK'
    tag_ny = idx.tag_feature(line_ny)
    assert tag_ny['former_municipality'] == 'NORTH YORK'
    assert 'Dec. 1 to Mar. 31' in tag_ny['regional_winter_rule']

    # Test point outside all boundaries
    pt_out = shapely.geometry.Point(100, 100)
    assert idx.find_municipality(pt_out) is None
    tag_out = idx.tag_feature(pt_out)
    assert tag_out['former_municipality'] is None
    assert tag_out['regional_winter_rule'] is None
