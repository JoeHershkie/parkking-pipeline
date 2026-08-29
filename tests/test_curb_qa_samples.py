"""Stratified visual-QA sampling helpers (inspection bins, not production gates)."""

from __future__ import annotations

from curb_qa_sample_util import (
    CONFIDENCE_BANDS,
    compound_vocabulary,
    confidence_band,
    enrich_qa_row,
    select_stratified_samples,
    stratum_inventory,
    write_qa_sample_export,
)
from shapely.geometry import LineString


def test_confidence_bands_are_inspection_bins_not_cutoffs():
    assert CONFIDENCE_BANDS[0][0] == '0.00-0.25'
    assert confidence_band(0.0) == '0.00-0.25'
    assert confidence_band(0.49) == '0.25-0.50'
    assert confidence_band(0.75) == '0.75-1.00'
    assert confidence_band(None) == 'unknown'


def test_compound_vocabulary_classes():
    assert compound_vocabulary('North') == 'simple'
    assert compound_vocabulary('North and east') == 'adjacent_compound'
    assert compound_vocabulary('Both') == 'opposing_or_both'
    assert compound_vocabulary('North and south') == 'opposing_or_both'
    assert compound_vocabulary('Odd') == 'parity'
    assert compound_vocabulary('Inner Perimeter') == 'perimeter'
    assert compound_vocabulary('West side of traffic island') == 'specialized'


def test_stratified_sample_covers_each_observed_stratum(tmp_path):
    line = LineString([(0, 0), (1, 0)])
    rows = [
        enrich_qa_row({
            '_id': '1',
            'Highway': 'A',
            'Side': 'North',
            'side_mode': 'single',
            'centreline_ids': [11],
            'curb_geometry_method': 'road_edge',
            'curb_confidence': 0.9,
            'curb_coverage': 0.8,
            'curb_override': False,
            'curb_warnings': [],
            'centreline_construction': 'block_path',
            'geometry': line,
        }),
        enrich_qa_row({
            '_id': '2',
            'Highway': 'B',
            'Side': 'Both',
            'side_mode': 'multi',
            'centreline_ids': [22],
            'curb_geometry_method': 'offset_fallback',
            'curb_confidence': 0.3,
            'curb_coverage': 0.1,
            'curb_override': False,
            'curb_warnings': ['ROAD_EDGE_NO_MATCH'],
            'centreline_construction': 'distance_merge',
            'geometry': line,
        }),
        enrich_qa_row({
            '_id': '3',
            'Highway': 'C',
            'Side': 'North and east',
            'side_mode': 'wrapping',
            'centreline_ids': [33],
            'curb_geometry_method': 'centerline_unresolved',
            'curb_confidence': 0.05,
            'curb_coverage': 0.0,
            'curb_override': True,
            'curb_warnings': ['SIDE_AMBIGUOUS'],
            'centreline_construction': 'block_path',
            'geometry': line,
        }),
    ]
    rows[0]['road_class'] = 'Local'
    rows[1]['road_class'] = 'Collector'
    rows[2]['road_class'] = 'Local'

    sampled = select_stratified_samples(rows, per_stratum=1)
    sampled_ids = {row['row_id'] for row in sampled}
    assert sampled_ids == {'1', '2', '3'}

    inventory = stratum_inventory(rows)
    assert set(inventory['method']) == {
        'road_edge', 'offset_fallback', 'centerline_unresolved',
    }
    assert inventory['override']['override'] == 1
    assert inventory['compound_vocabulary']['adjacent_compound'] == 1

    geojson_path, summary_path, selected = write_qa_sample_export(
        rows,
        geojson_path=tmp_path / 'samples.geojson',
        summary_path=tmp_path / 'summary.json',
        per_stratum=1,
        sampled=sampled,
    )
    assert geojson_path.exists()
    assert summary_path.exists()
    assert len(selected) == 3
    text = summary_path.read_text(encoding='utf-8')
    assert 'not production' in text
