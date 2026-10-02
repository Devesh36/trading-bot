import pytest
from data.market_data import normalize


def row(t):
    return dict(time=t, open=10, high=12, low=9, close=11, volume=1)


def test_closed_and_sorted():
    assert normalize([row(1800), row(0)], now=1800).time.tolist() == [0]


def test_gaps_and_conflicting_data():
    with pytest.raises(ValueError):
        normalize([row(0), row(3600)])
    with pytest.raises(ValueError):
        normalize([row(0), dict(row(0), close=10)])
