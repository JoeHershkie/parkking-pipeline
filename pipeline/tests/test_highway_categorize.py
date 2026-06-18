"""Tests for highway categorization buckets."""


from parking_pipeline.highway_categorize import categorize_highway  # noqa: E402


def test_generic_lane():
    assert categorize_highway('Lane') == 'generic_lane_highway'


def test_lane_position():
    assert categorize_highway('Lane first north of Bloor Street West') == (
        'lane_position_in_highway'
    )


def test_laneway():
    assert categorize_highway('Laneway east of Yonge Street') == 'laneway_phrase'


def test_parenthetical():
    assert categorize_highway('Grenadier Heights (north end)') == 'parenthetical_qualifier'
