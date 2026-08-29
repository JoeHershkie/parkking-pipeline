"""Tests for clean_data helpers."""


import pandas as pd
import pytest

from parking_pipeline.clean_data import (  # noqa: E402
    deduplicate_rules,
    main,
    prohibited_times,
    unpack_bylaw_table,
)


def test_unpack_ckan_double_quoted_list() -> None:
    raw = (
        '"[{\'key\': \'Highway\', \'linearId\': None, \'value\': \'Main St\'}, '
        '{\'key\': \'Side\', \'linearId\': None, \'value\': \'North\'}]"'
    )
    result = unpack_bylaw_table(raw)
    assert result.ok
    assert result.fields['Highway'] == 'Main St'
    assert result.fields['Side'] == 'North'


def test_unpack_plain_python_list_string() -> None:
    raw = "[{'key': 'Highway', 'value': 'Queen St'}, {'key': 'Side', 'value': 'South'}]"
    result = unpack_bylaw_table(raw)
    assert result.ok
    assert result.fields['Highway'] == 'Queen St'


def test_prohibited_times_anytime_from_between() -> None:
    fields = {
        'Prohibited Times and/or Days': None,
        'Times and/or Days': None,
        'Between': 'Southport Street and Windermere Avenue Anytime',
    }
    assert prohibited_times(fields) == 'Anytime'


def test_deduplicate_does_not_write_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('parking_pipeline.failure_ledger.data_path', lambda name: tmp_path / name)
    df = pd.DataFrame([
        {
            '_id': 10,
            'Highway': 'Main St',
            'Side': 'north',
            'Between': 'A and B',
            'Prohibited Times and/or Days': 'Anytime',
            'Maximum Period Permitted': '',
            'schedule_category': 'no_parking',
        },
        {
            '_id': 20,
            'Highway': 'Main St',
            'Side': 'north',
            'Between': 'A and B',
            'Prohibited Times and/or Days': 'Anytime',
            'Maximum Period Permitted': '',
            'schedule_category': 'no_parking',
        },
    ])
    kept, dropped = deduplicate_rules(df)
    assert len(kept) == 1
    assert dropped == 1
    assert not (tmp_path / 'failure_ledger.csv').exists()


def test_main_requests_opendata_refresh(monkeypatch) -> None:
    seen: dict[str, bool] = {}

    def fake_ensure(*, force: bool = False, skip: bool = False):
        seen['force'] = force
        seen['skip'] = skip
        raise RuntimeError('stop before csv')

    monkeypatch.setattr('parking_pipeline.clean_data.ensure_raw_parking_dump', fake_ensure)
    with pytest.raises(RuntimeError, match='stop before csv'):
        main(['--force-refresh'])
    assert seen == {'force': True, 'skip': False}


def test_main_skip_refresh(monkeypatch) -> None:
    seen: dict[str, bool] = {}

    def fake_ensure(*, force: bool = False, skip: bool = False):
        seen['force'] = force
        seen['skip'] = skip
        raise RuntimeError('stop before csv')

    monkeypatch.setattr('parking_pipeline.clean_data.ensure_raw_parking_dump', fake_ensure)
    with pytest.raises(RuntimeError, match='stop before csv'):
        main(['--skip-refresh'])
    assert seen == {'force': False, 'skip': True}


def test_load_active_rules_filters_repealed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr('parking_pipeline.clean_data.data_path', lambda name: tmp_path / name)
    (tmp_path / 'toronto_raw_parking_dump.csv').write_text(
        '_id,Latest_Action,scheduleName,ByLaw_Table\n'
        '1,Enacted,Schedule 13: No Parking,[]\n'
        '2,Repealed,Schedule 13: No Parking,[]\n',
        encoding='utf-8',
    )
    from parking_pipeline.clean_data import load_active_rules

    df = load_active_rules()
    assert list(df['_id']) == [1]
