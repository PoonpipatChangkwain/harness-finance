from __future__ import annotations

import pytest
from pydantic import BaseModel, field_validator

from app.domain.models.coerce import coerce_optional_float, coerce_percent_field


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("N/A", None),
        ("None", None),
        ("null", None),
        ("", None),
        ("  ", None),
        ("TBD", None),
        ("$1,234.50", 1234.5),
        ("1,234", 1234.0),
        ("฿34.25", 34.25),
        ("34.25 THB", 34.25),
        ("€99", 99.0),
        ("15%", None),  # a percentage where a price was asked for
        ("150-160", None),  # range
        ("around 150", None),  # hedge
        ("~150", None),
        ("-12.5", -12.5),
        ("1e3", 1000.0),
        (".5", 0.5),
        (42, 42),  # non-strings pass through
        (3.14, 3.14),
        (None, None),
        (True, None),
    ],
)
def test_coerce_optional_float(raw: object, expected: object) -> None:
    assert coerce_optional_float(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15%", 0.15),
        ("15 %", 0.15),
        ("0.15", 0.15),
        ("15", 0.15),
        (15, 0.15),
        (0.2, 0.2),
        (1, 1.0),
        ("100%", 1.0),
        ("N/A", None),
        ("about 10%", None),
        ("10-15%", None),
    ],
)
def test_coerce_percent_field(raw: object, expected: object) -> None:
    result = coerce_percent_field(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


class _Proposal(BaseModel):
    stop_loss: float | None = None
    weight: float | None = None

    _price = field_validator("stop_loss", mode="before")(coerce_optional_float)
    _pct = field_validator("weight", mode="before")(coerce_percent_field)


def test_as_field_validator_keeps_rest_of_decision() -> None:
    p = _Proposal.model_validate({"stop_loss": "15%", "weight": "12.5%"})
    assert p.stop_loss is None
    assert p.weight == pytest.approx(0.125)
