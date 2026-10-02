from app.domain.compute.statements import margins, period_changes


def test_period_changes_newest_first() -> None:
    res = period_changes(
        ["q2", "q1", "q0"], [{"rev": 110.0, "x": None}, {"rev": 100.0, "x": 1.0}, {"rev": 0.0}]
    )
    assert res.value["rev"][0] == 0.10000000000000009 or abs(res.value["rev"][0] - 0.1) < 1e-12
    assert res.value["rev"][1] is None and res.value["x"] == [None, None]
    assert res.records[0].calculation == "compute.statements.period_changes@v1"


def test_margins_by_provider_names() -> None:
    res = margins(
        ["a", "b"], [{"Total Revenue": 200.0, "Gross Profit": 100.0, "Net Income": 20.0}, {"revenue": 0.0}]
    )
    assert res.value["gross_margin"] == [0.5, None] and res.value["net_margin"] == [0.1, None]
    assert res.value["operating_margin"] == [None, None]
