"""Arithmetic and formatting: the numbers an answer is allowed to contain."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from footnote import calc

finite = st.floats(min_value=1e3, max_value=1e13, allow_nan=False, allow_infinity=False)


def test_growth_matches_the_textbook_formula():
    assert calc.growth(391_035, 383_285) == pytest.approx(0.020220, abs=1e-6)


def test_growth_from_a_negative_base_uses_its_magnitude():
    # Intel's operating income moved from a small profit to a loss; a loss that
    # shrinks must read as an improvement, not a decline.
    assert calc.growth(-50, -100) == pytest.approx(0.5)


def test_growth_from_zero_is_refused_rather_than_infinite():
    with pytest.raises(ZeroDivisionError):
        calc.growth(10, 0)


@given(start=finite, end=finite)
def test_growth_reproduces_the_end_value(start, end):
    assert start * (1 + calc.growth(end, start)) == pytest.approx(end, rel=1e-9, abs=1e-12 * max(start, end))


@given(start=finite, end=finite, years=st.integers(min_value=1, max_value=10))
def test_cagr_compounds_back_to_the_end_value(start, end, years):
    assert start * (1 + calc.cagr(end, start, years)) ** years == pytest.approx(end, rel=1e-7, abs=1e-12 * max(start, end))


def test_cagr_rejects_a_negative_start():
    with pytest.raises(ValueError):
        calc.cagr(100, -5, 2)


def test_margin_and_free_cash_flow():
    assert calc.margin(123_216, 391_035) == pytest.approx(0.31510, abs=1e-5)
    assert calc.free_cash_flow(118_254, 9_447) == 108_807


@pytest.mark.parametrize(
    ("value", "unit", "text"),
    [
        (391_035_000_000, "USD", "$391.0 billion"),
        (2_120_000_000_000, "USD", "$2.12 trillion"),
        (-18_756_000_000, "USD", "-$18.8 billion"),
        (5_560_000, "USD", "$5.6 million"),
        (6.08, "USD/share", "$6.08"),
        (-4.38, "USD/share", "-$4.38"),
        (0.31510, "ratio", "31.5%"),
        (-0.0215, "percentage_points", "2.1 percentage points"),
        (164_000, "people", "164,000"),
    ],
)
def test_every_unit_has_one_written_form(value, unit, text):
    assert calc.fmt_value(value, unit) == text


def test_an_unknown_unit_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        calc.fmt_value(1.0, "EUR")


def test_change_is_written_without_a_sign():
    assert calc.fmt_change(-0.0984) == "9.8%"
    assert not math.isnan(calc.growth(1, 2))
