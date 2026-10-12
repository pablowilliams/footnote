"""Arithmetic and number formatting.

The language model, when one is used, is never asked to calculate. Every figure in
an answer is produced here, and the verifier re-runs the same functions to check it.
"""

from __future__ import annotations

import math


def growth(current: float, previous: float) -> float:
    if previous == 0:
        raise ZeroDivisionError("growth from a zero base is undefined")
    return (current - previous) / abs(previous)


def cagr(end: float, start: float, years: int) -> float:
    if years <= 0 or start <= 0 or end <= 0:
        raise ValueError("CAGR needs positive start and end values and at least one year")
    return (end / start) ** (1 / years) - 1


def margin(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise ZeroDivisionError("margin on zero revenue is undefined")
    return numerator / denominator


def free_cash_flow(operating_cash_flow: float, capital_expenditure: float) -> float:
    return operating_cash_flow - capital_expenditure


def fmt_value(value: float, unit: str) -> str:
    """The one way a figure is written in an answer."""
    if unit == "USD":
        sign = "-" if value < 0 else ""
        v = abs(value)
        if v >= 1e12:
            return f"{sign}${v / 1e12:.2f} trillion"
        if v >= 1e9:
            return f"{sign}${v / 1e9:.1f} billion"
        if v >= 1e6:
            return f"{sign}${v / 1e6:.1f} million"
        return f"{sign}${v:,.0f}"
    if unit == "USD/share":
        return f"${value:.2f}" if value >= 0 else f"-${abs(value):.2f}"
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "percentage_points":
        return f"{abs(value) * 100:.1f} percentage points"
    if unit == "people":
        return f"{value:,.0f}"
    raise ValueError(f"unknown unit {unit}")


def fmt_change(value: float) -> str:
    """A relative change, unsigned: the sentence carries the direction."""
    return f"{abs(value) * 100:.1f}%"


def close(a: float, b: float, rel: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)
