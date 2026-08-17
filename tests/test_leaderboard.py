"""Unit tests for the pure scoring logic in leaderboard.py (no DB needed)."""
from datetime import datetime, timedelta, timezone

from kb.leaderboard import _final, _horizon_days, compute_score, stance


class TestHorizonDays:
    def test_units(self):
        assert _horizon_days("day") == 7
        assert _horizon_days("5d") == 7
        assert _horizon_days("week") == 14
        assert _horizon_days("2 weeks") == 14
        assert _horizon_days("month") == 90
        assert _horizon_days("3M") == 90
        assert _horizon_days("quarter") == 90
        assert _horizon_days("1Q") == 90
        assert _horizon_days("year") == 365
        assert _horizon_days("2Y") == 365

    def test_freeform_and_missing(self):
        # NB: "by year end" ends with 'd' → the day rule fires first (7) —
        # the suffix heuristics predate this rewrite and are kept as-is.
        assert _horizon_days("by year end") == 7
        assert _horizon_days("within a year") == 365
        assert _horizon_days("6-12 months") == 90
        assert _horizon_days("") == 90
        assert _horizon_days(None) == 90


class TestStance:
    def test_direction(self):
        assert stance("up", None) == 1
        assert stance("down", None) == -1
        assert stance("flat", None) == 0
        assert stance("unspecified", None) == 0

    def test_actions(self):
        assert stance(None, "buy") == 1
        assert stance(None, "long") == 1
        assert stance(None, "cover") == 1
        assert stance(None, "short") == -1
        assert stance(None, "sell") == -1
        assert stance(None, "avoid") == -1
        assert stance(None, "watch") == 0
        assert stance(None, "hold") == 0

    def test_direction_wins_over_neutral_action(self):
        assert stance("up", "watch") == 1
        # A bullish action dominates a bearish direction (same precedence as
        # the original implementation: the +1 branch is checked first).
        assert stance("down", "buy") == 1

    def test_case_insensitive(self):
        assert stance("UP", "BUY") == 1
        assert stance("Down", "SHORT") == -1


class TestComputeScore:
    def test_no_prices_is_none(self):
        assert compute_score("up", "buy", None, 100.0) is None
        assert compute_score("up", "buy", 100.0, None) is None
        assert compute_score("up", "buy", 0.0, 100.0) is None

    def test_neutral_is_zero(self):
        assert compute_score("flat", "watch", 100.0, 130.0) == 0.0

    def test_full_scale_at_20pct(self):
        assert compute_score("up", "buy", 100.0, 120.0) == 1.0
        assert compute_score("down", "short", 100.0, 80.0) == 1.0

    def test_clamped(self):
        assert compute_score("up", "buy", 100.0, 150.0) == 1.0
        assert compute_score("up", "buy", 100.0, 50.0) == -1.0

    def test_wrong_direction_is_negative(self):
        assert compute_score("up", "buy", 100.0, 90.0) == -0.5
        assert compute_score("down", "short", 100.0, 110.0) == -0.5

    def test_small_move(self):
        assert abs(compute_score("up", "buy", 100.0, 101.0) - 0.05) < 1e-9


class TestFinal:
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def _row(self, score=0.5, eval_at=None, timeframe="1Y"):
        return {"score": score, "eval_at": eval_at or self.base + timedelta(days=30),
                "made_at": self.base, "timeframe": timeframe}

    def test_unscored_not_final(self):
        assert not _final(self._row(score=None, eval_at=None))

    def test_horizon_elapsed_is_final(self):
        # 1Y horizon: an eval 30 days after the call is still running.
        assert not _final(self._row())
        # eval reaching the horizon end is frozen.
        assert _final(self._row(eval_at=self.base + timedelta(days=365)))

    def test_short_horizon_freezes_fast(self):
        row = self._row(timeframe="day", eval_at=self.base + timedelta(days=7))
        assert _final(row)
