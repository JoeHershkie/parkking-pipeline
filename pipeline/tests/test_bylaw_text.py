"""Tests for bylaw_text.preprocess_between."""


import pytest

from parking_pipeline.bylaw_text import preprocess_between  # noqa: E402


def test_preprocess_between_fixes_metres_spacing() -> None:
    assert '98 metres north' in preprocess_between(
        'A point 98metres north of Cottingham Road and a point 75 metres further north',
    )
    assert '3604 metres west' in preprocess_between(
        'A point 3604 metres west of Yonge Street and a point 3604 metres west',
    )


@pytest.mark.parametrize(
    ('between', 'expected_fragment'),
    [
        (
            'A point 10 metres north of Foo Street to a point 20 metres north',
            ' and ',
        ),
        (
            'Adjacent to 123 Main Street between Bar Ave and Baz Rd',
            'Bar Ave',
        ),
    ],
)
def test_preprocess_between_replaces_joiner_to(between: str, expected_fragment: str) -> None:
    assert expected_fragment in preprocess_between(between)


@pytest.mark.parametrize(
    ('raw', 'expected_fragment'),
    [
        ('point 59.4 metres north of Kintyre Avenue', 'a point 59.4'),
        ('and point 62.5 metres north', 'and a point 62.5'),
    ],
)
def test_preprocess_between_point_and_street_fixes(raw: str, expected_fragment: str) -> None:
    assert expected_fragment in preprocess_between(raw)
