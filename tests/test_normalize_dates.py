from datetime import date

from intro_paths.dates import education_end, overlap_status, parse_date
from intro_paths.models import Period
from intro_paths.normalize import normalize_org

TODAY = date(2026, 11, 15)


def test_org_legal_forms_removed():
    assert normalize_org('ООО "Ромашка"') == "ромашка"
    assert normalize_org("Сбер-банк ПАО") == "сбер банк"
    assert normalize_org("Example Corp") == "example corp"


def test_parse_date_formats():
    assert parse_date("15 Nov 2026") == date(2026, 11, 15)
    assert parse_date("Mar 2018") == date(2018, 3, 1)
    assert parse_date("Feb 2021", end=True) == date(2021, 2, 28)
    assert parse_date("2019-12", end=True) == date(2019, 12, 31)
    assert parse_date("2015") == date(2015, 1, 1)
    assert parse_date("2015", end=True) == date(2015, 12, 31)


def test_parse_date_bad_input_is_none():
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("вчера") is None
    assert parse_date("31 Feb 2020") is None


def test_overlap_status():
    assert overlap_status(Period(), Period(), TODAY) == "yes"
    assert overlap_status(Period(), Period(date(2010, 1, 1), date(2019, 12, 31)), TODAY) == "unknown"
    assert overlap_status(
        Period(date(2018, 3, 1), date(2021, 2, 28)),
        Period(date(2020, 1, 1), date(2022, 1, 1)),
        TODAY,
    ) == "yes"
    assert overlap_status(
        Period(date(2018, 1, 1), date(2019, 1, 1)),
        Period(date(2020, 1, 1), date(2022, 1, 1)),
        TODAY,
    ) == "no"


def test_education_end_heuristic():
    assert education_end(date(2012, 9, 1)) == date(2016, 12, 31)
    assert education_end(None) is None
