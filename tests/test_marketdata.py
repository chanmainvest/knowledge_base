"""Unit tests for marketdata's pure pieces (no DB, no network)."""
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from kb.marketdata import PriceTable, _batched, _f, _rows_for


class TestF:
    def test_plain(self):
        assert _f(1.5) == 1.5
        assert _f("3.25") == 3.25

    def test_nan_and_junk(self):
        assert _f(float("nan")) is None
        assert _f(None) is None
        assert _f("n/a") is None


class TestRowsFor:
    def _df(self):
        idx = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"])
        return pd.DataFrame({
            "Open": [10.0, 11.0, np.nan],
            "High": [12.0, 12.5, 12.0],
            "Low": [9.5, 10.5, 10.0],
            "Close": [11.0, 12.0, np.nan],   # third day has no close -> dropped
            "Volume": [1000, 2000, 3000],
        }, index=idx)

    def test_drops_closeless_days_and_converts(self):
        rows = _rows_for(self._df())
        assert len(rows) == 2
        assert rows[0]["day"] == date(2026, 8, 10)
        assert rows[0]["close"] == 11.0
        assert rows[1]["volume"] == 2000

    def test_empty(self):
        assert _rows_for(None) == []
        assert _rows_for(pd.DataFrame()) == []


class TestPriceTable:
    def _table(self):
        data = {
            "X": ([date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
                  [10.0, 11.0, 12.0]),
        }
        return PriceTable(data)

    def test_on_or_after(self):
        t = self._table()
        # Saturday -> first close on/after is Monday the 3rd.
        assert t.on("X", datetime(2026, 8, 1, tzinfo=timezone.utc)) == 10.0
        assert t.on("X", datetime(2026, 8, 4, 15, tzinfo=timezone.utc)) == 11.0

    def test_falls_back_to_last_before(self):
        # After the last stored day, the latest close wins (today's close
        # may not have landed in the store yet).
        assert self._table().on("X", datetime(2026, 8, 7, tzinfo=timezone.utc)) == 12.0

    def test_unknown_ticker(self):
        assert self._table().on("NOPE", datetime(2026, 8, 4)) is None


class TestBatched:
    def test_chunks(self):
        out = list(_batched(list(range(10)), 4))
        assert out == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]

    def test_exact_multiple(self):
        out = list(_batched(list(range(6)), 3))
        assert out == [[0, 1, 2], [3, 4, 5]]
