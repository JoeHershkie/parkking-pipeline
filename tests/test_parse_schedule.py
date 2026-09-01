"""Tests for schedule_format / parse_schedule."""

import json

import pandas as pd
import pytest

from parking_pipeline.parse_schedule import (  # noqa: E402
    SCHEDULE_EMPTY,
    empty_times_default,
    parse_rows,
)
from parking_pipeline.schedule_format import (  # noqa: E402
    overlaps_membership,
    parse_max_minutes,
    parse_schedule,
    schedule_from_json,
    schedule_to_json,
    time_to_minutes,
)


@pytest.mark.parametrize(
    ('hour', 'minute', 'mer', 'expected'),
    [
        (8, 0, 'a.m.', 480),
        (6, 0, 'p.m.', 1080),
        (12, 0, 'a.m.', 0),
        (12, 0, 'p.m.', 720),
        (12, 1, 'a.m.', 1),
        (12, 0, 'noon', 720),
        (12, 0, 'midnight', 0),
    ],
)
def test_time_to_minutes(hour, minute, mer, expected) -> None:
    assert time_to_minutes(hour, minute, mer) == expected


@pytest.mark.parametrize(
    ('text', 'minutes'),
    [
        ('1 hour', 60),
        ('2 hours', 120),
        ('30 mins.', 30),
        ('15 mins.', 15),
        (None, None),
    ],
)
def test_parse_max_minutes(text, minutes) -> None:
    assert parse_max_minutes(text) == minutes


def test_anytime() -> None:
    s = parse_schedule('Anytime')
    assert s['status'] == 'anytime'
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 900, 'month': 4, 'dayOfMonth': 15})


def test_weekday_range() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 1
    w = s['windows'][0]
    assert w['days'] == [1, 2, 3, 4, 5]
    assert w['startMinute'] == 480
    assert w['endMinute'] == 1080
    assert overlaps_membership(
        s, {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10},
    )
    assert not overlaps_membership(
        s, {'dayOfWeek': 0, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10},
    )


def test_all_days_no_suffix() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m.')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == list(range(7))


def test_multiple_ranges_same_days() -> None:
    s = parse_schedule('7:00 a.m. to 9:00 a.m. and 4:00 p.m. to 6:00 p.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert overlaps_membership(
        s, {'dayOfWeek': 3, 'minuteOfDay': 480, 'month': 1, 'dayOfMonth': 1},
    )
    assert not overlaps_membership(
        s, {'dayOfWeek': 3, 'minuteOfDay': 720, 'month': 1, 'dayOfMonth': 1},
    )


def test_semicolon_clauses() -> None:
    text = (
        '7:00 p.m. of one day to 7:00 a.m. of the next following day, Mon to Fri.; '
        'Anytime, Sat. and Sun.'
    )
    s = parse_schedule(text)
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert s['windows'][0]['crossesMidnight'] is True
    assert overlaps_membership(
        s, {'dayOfWeek': 6, 'minuteOfDay': 1200, 'month': 5, 'dayOfMonth': 1},
    )


def test_semicolon_anytime_sat_sun_no_comma() -> None:
    text = '6:00 p.m. of one day to 8:00 a.m. of the next following day, Mon. to Fri.; Anytime Sat. and Sun.'
    s = parse_schedule(text)
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2


def test_overnight() -> None:
    s = parse_schedule('6:00 a.m. of one day to 2:00 a.m. of the next day')
    assert s['status'] == 'ok'
    assert s['windows'][0]['crossesMidnight'] is True


def test_except_holidays_flag() -> None:
    s = parse_schedule('4:00 p.m. to 6:00 p.m., Mon. to Fri., except public holidays')
    assert s['status'] == 'ok'
    assert s['flags']['exceptPublicHolidays'] is True
    weekday = {'dayOfWeek': 2, 'minuteOfDay': 1020, 'month': 6, 'dayOfMonth': 10, 'year': 2025}
    christmas = {'dayOfWeek': 4, 'minuteOfDay': 1020, 'month': 12, 'dayOfMonth': 25, 'year': 2025}
    assert overlaps_membership(s, weekday)
    assert not overlaps_membership(s, christmas)


def test_seasonal_anytime_winter() -> None:
    text = 'Anytime, from Dec. 1 of one year to Mar. 31 of the next following year, inclusive'
    s = parse_schedule(text)
    assert s['status'] == 'ok'
    assert s['windows'][0]['calendar']['monthRanges']
    slot = {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 12, 'dayOfMonth': 15, 'year': 2024}
    assert overlaps_membership(s, slot)
    assert not overlaps_membership(
        s, {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 7, 'dayOfMonth': 15, 'year': 2024},
    )


def test_monthly_split_apr_nov() -> None:
    text = '16th day to the last day of each month, from Apr. 1 to Nov. 30, inclusive'
    s = parse_schedule(text)
    assert s['status'] == 'ok'
    cal = s['windows'][0]['calendar']
    assert cal['dayOfMonthRanges'] == [{'start': 16, 'end': 'last'}]
    assert cal['monthRanges'][0]['startMonth'] == 4
    slot_in = {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 5, 'dayOfMonth': 20, 'year': 2024}
    slot_out = {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 5, 'dayOfMonth': 10, 'year': 2024}
    assert overlaps_membership(s, slot_in)
    assert not overlaps_membership(s, slot_out)


def test_month_list_only() -> None:
    s = parse_schedule('May, Jul., Sep. and Nov.')
    assert s['status'] == 'anytime'
    assert s['calendar']['months'] == [5, 7, 9, 11]


def test_each_weekday_with_season() -> None:
    s = parse_schedule('Each Thu., Apr. 1 to Nov. 30, inclusive')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [4]
    assert overlaps_membership(
        s,
        {'dayOfWeek': 4, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 1, 'year': 2024},
    )
    assert not overlaps_membership(
        s,
        {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 1, 'year': 2024},
    )


def test_inverted_except_weekday_mornings() -> None:
    s = parse_schedule('Anytime, except 7:00 a.m. to 9:00 a.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert s['inverted'] is True
    assert overlaps_membership(
        s, {'dayOfWeek': 2, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10},
    )
    assert not overlaps_membership(
        s, {'dayOfWeek': 2, 'minuteOfDay': 480, 'month': 6, 'dayOfMonth': 10},
    )


def test_inverted_except_sunday() -> None:
    s = parse_schedule('Anytime, except Sun. and public holidays')
    assert s['status'] == 'ok'
    assert s['inverted'] is True
    assert not overlaps_membership(
        s, {'dayOfWeek': 0, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 1, 'year': 2025},
    )
    assert overlaps_membership(
        s, {'dayOfWeek': 1, 'minuteOfDay': 600, 'month': 6, 'dayOfMonth': 10, 'year': 2025},
    )
    # Christmas 2025 (Thursday): except window covers public holidays
    assert not overlaps_membership(
        s, {'dayOfWeek': 4, 'minuteOfDay': 600, 'month': 12, 'dayOfMonth': 25, 'year': 2025},
    )


def test_parenthetical_strip() -> None:
    s = parse_schedule('Anytime (buses excepted)')
    assert s['status'] == 'anytime'


def test_noon_time_range() -> None:
    s = parse_schedule('8:30 a.m. to 9:00 a.m., 9:30 a.m. to 11:00 a.m. and 12:00 noon to 5:00 p.m., Mon. to Fri.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 3


def test_time_with_nov_apr_season() -> None:
    s = parse_schedule('2:00 a.m. to 6:00 a.m., Nov. 1 to Apr. 30')
    assert s['status'] == 'ok'
    assert s['windows'][0]['calendar']['monthRanges']


def test_json_roundtrip() -> None:
    s = parse_schedule('2:00 a.m. to 6:00 a.m.')
    raw = schedule_to_json(s)
    back = schedule_from_json(raw)
    assert back == s
    assert json.loads(raw)['v'] == 1


def test_day_tail_without_comma() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m. Mon. to Fri.')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [1, 2, 3, 4, 5]


def test_split_day_groups() -> None:
    s = parse_schedule('4:00 p.m. to 6:00 p.m., Mon. to Fri. and 8:00 a.m. to 6:00 p.m., Sat. and Sun.')
    assert s['status'] == 'ok'
    assert len(s['windows']) == 2
    assert overlaps_membership(
        s, {'dayOfWeek': 0, 'minuteOfDay': 540, 'month': 3, 'dayOfMonth': 1},
    )


def test_single_weekday_tail() -> None:
    s = parse_schedule('9:00 a.m. to 12:00 p.m., Tue.')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [2]


@pytest.mark.parametrize(
    'category',
    ['no_parking', 'no_stopping', 'no_standing'],
)
def test_empty_times_default_prohibition_schedules(category: str) -> None:
    assert empty_times_default(pd.Series({'schedule_category': category})) == 'Anytime'


def test_empty_times_default_not_restricted_periods() -> None:
    assert empty_times_default(pd.Series({'schedule_category': 'restricted_periods'})) is None


def test_parse_rows_empty_prohibition_schedules_become_anytime() -> None:
    df = pd.DataFrame([
        {
            '_id': 12771,
            'schedule_category': 'no_parking',
            'Highway': 'Gracedale Boulevard',
            'Between': 'A point south',
            'Prohibited Times and/or Days': None,
            'Maximum Period Permitted': None,
        },
        {
            '_id': 18303,
            'schedule_category': 'no_stopping',
            'Highway': 'Queen Street West',
            'Between': 'Noble Street',
            'Prohibited Times and/or Days': '',
            'Maximum Period Permitted': None,
        },
        {
            '_id': 25292,
            'schedule_category': 'restricted_periods',
            'Highway': 'Roselawn Avenue',
            'Between': 'Chaplin Crescent',
            'Prohibited Times and/or Days': '',
            'Maximum Period Permitted': None,
        },
    ])
    parsed, failures = parse_rows(df)
    assert failures[SCHEDULE_EMPTY] == 1
    assert len(parsed) == 2
    assert set(parsed['_id']) == {12771, 18303}
    assert (parsed['schedule_status'] == 'anytime').all()


def test_full_month_and_weekday_names() -> None:
    s = parse_schedule('8:00 a.m. to 6:00 p.m., Monday to Friday, from June 1 to August 31, inclusive')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [1, 2, 3, 4, 5]
    mr = s['windows'][0]['calendar']['monthRanges'][0]
    assert mr['startMonth'] == 6 and mr['endMonth'] == 8


def test_explicit_calendar_year_range() -> None:
    s = parse_schedule('7:00 a.m. to 9:00 p.m. from and including Mon., June 6, 2022 to and including Sun., June 12, 2022')
    assert s['status'] == 'ok'
    assert s['windows'][0]['startMinute'] == 420
    assert s['windows'][0]['endMinute'] == 1260
    mr = s['windows'][0]['calendar']['monthRanges'][0]
    assert mr['startMonth'] == 6 and mr['startDay'] == 6 and mr['endMonth'] == 6 and mr['endDay'] == 12


def test_day_of_month_with_seasonal_tail() -> None:
    s = parse_schedule('16th day to the last day of each month, from Apr. 1 to Nov. 30, inclusive')
    assert s['status'] == 'ok'
    cal = s['windows'][0]['calendar']
    assert cal['dayOfMonthRanges'][0] == {'start': 16, 'end': 'last'}
    assert cal['monthRanges'][0] == {'startMonth': 4, 'startDay': 1, 'endMonth': 11, 'endDay': 30}


def test_except_sat_and_sun_and_holidays() -> None:
    s = parse_schedule('4:00 p.m. to 6:00 p.m., except Sat., Sun. and public holidays')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [1, 2, 3, 4, 5]
    assert s['flags']['exceptPublicHolidays'] is True


def test_each_weekday_time_range() -> None:
    s = parse_schedule('8:00 a.m. to 4:00 p.m., each Thurs., Apr. 1 to Nov. 30, inclusive')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [4]
    assert s['windows'][0]['startMinute'] == 480
    assert s['windows'][0]['calendar']['monthRanges'][0]['startMonth'] == 4


def test_ordinal_weekday_schedule() -> None:
    s = parse_schedule('1st and 3rd Wed. of each month, Apr. 1st to Nov. 30th, inclusive')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [3]
    assert s['windows'][0]['calendar']['monthRanges'][0] == {'startMonth': 4, 'startDay': 1, 'endMonth': 11, 'endDay': 30}


def test_except_months_and_holidays() -> None:
    s = parse_schedule('8:00 a.m. to 4:00 p.m. Monday to Friday except July, August & Public Holidays')
    assert s['status'] == 'ok'
    assert s['windows'][0]['days'] == [1, 2, 3, 4, 5]
    assert s['flags']['exceptPublicHolidays'] is True
    assert 7 not in s['windows'][0]['calendar']['months']
    assert 8 not in s['windows'][0]['calendar']['months']
    assert 6 in s['windows'][0]['calendar']['months']


def test_all_times_and_standalone_month_list() -> None:
    s1 = parse_schedule('All times')
    assert s1['status'] == 'anytime'

    s2 = parse_schedule('Anytime, May, Jul., Sep. and Nov.')
    assert s2['status'] == 'ok'
    assert s2['windows'][0]['calendar']['months'] == [5, 7, 9, 11]


def test_typo_normalizations() -> None:
    s = parse_schedule('Anyitme')
    assert s['status'] == 'anytime'

    s2 = parse_schedule('7:00 a.m. to 9:00 a.m., Mon. to Frii, except pubic holidays')
    assert s2['status'] == 'ok'
    assert s2['windows'][0]['days'] == [1, 2, 3, 4, 5]
    assert s2['flags']['exceptPublicHolidays'] is True

    s3 = parse_schedule('2 a.m. to 6 a.m., Mon. to Fri.')
    assert s3['status'] == 'ok'
    assert s3['windows'][0]['startMinute'] == 120


def test_snow_route_conditional_schedule() -> None:
    s = parse_schedule('Major Snow Storm Conditions')
    assert s['status'] == 'conditional'
    assert s['is_snow_route'] is True
    assert s['condition'] == 'major_snowstorm_declared'
    # Without snowstorm active in slot -> False
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 900, 'month': 1, 'dayOfMonth': 15}) is False
    # With majorSnowStorm active in slot -> True
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 900, 'month': 1, 'dayOfMonth': 15, 'majorSnowStorm': True}) is True


def test_empty_times_default_categories() -> None:
    winter_row = pd.Series({'schedule_category': 'winter_maintenance'})
    assert empty_times_default(winter_row) == '2:00 a.m. to 6:00 a.m. from Dec. 1 to Mar. 31'

    snow_row = pd.Series({'schedule_category': 'snow_route'})
    assert empty_times_default(snow_row) == 'Major Snow Storm Conditions'

    streetcar_row = pd.Series({'schedule_category': 'snow_streetcar'})
    assert empty_times_default(streetcar_row) == 'Major Snow Storm Conditions'

    car_share_row = pd.Series({'schedule_category': 'car_share'})
    assert empty_times_default(car_share_row) == 'Anytime'


def test_winter_maintenance_dec_to_mar() -> None:
    s = parse_schedule('2:00 a.m. to 6:00 a.m. from Dec. 1 to Mar. 31')
    assert s['status'] == 'ok'
    # Jan 15 at 3am (minute 180) -> True
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 180, 'month': 1, 'dayOfMonth': 15, 'year': 2026}) is True
    # Jan 15 at 12pm (minute 720) -> False
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 720, 'month': 1, 'dayOfMonth': 15, 'year': 2026}) is False
    # Jul 15 at 3am (minute 180) -> False
    assert overlaps_membership(s, {'dayOfWeek': 2, 'minuteOfDay': 180, 'month': 7, 'dayOfMonth': 15, 'year': 2026}) is False


