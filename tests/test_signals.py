"""Tests des fonctions de calcul pures — fixtures de données connues."""

import numpy as np
import pandas as pd
import pytest

from soltrader.signals import (
    is_decreasing, is_increasing, assess_structure, detect_swings,
    detect_fvg, detect_candle_confirmation, detect_absorption,
    calc_volume_profile, assess_funding, assess_oi, assess_ls_ratio,
    assess_taker_flow, check_session,
)


# ─────────────────────────────────────────────
# is_decreasing / is_increasing
# Régression du bug historique : vérifiaient les indices 0..2 (anciens)
# au lieu des DERNIÈRES valeurs.
# ─────────────────────────────────────────────

class TestMonotonic:
    def test_decreasing_checks_recent_values(self):
        # Début croissant, fin décroissante → doit être décroissant
        assert is_decreasing(np.array([1, 2, 5, 4, 3]))

    def test_increasing_checks_recent_values(self):
        # Début décroissant, fin croissante → doit être croissant
        assert is_increasing(np.array([5, 4, 1, 2, 3]))

    def test_decreasing_false_when_tail_rises(self):
        assert not is_decreasing(np.array([5, 4, 3, 2, 9]))

    def test_equal_values_are_not_strict(self):
        assert not is_decreasing(np.array([3, 3, 3]))
        assert not is_increasing(np.array([3, 3, 3]))

    def test_short_arrays(self):
        assert is_decreasing(np.array([2, 1]))
        assert is_increasing(np.array([1, 2]))
        assert not is_decreasing(np.array([1]))   # < 2 valeurs
        assert not is_increasing(np.array([]))


# ─────────────────────────────────────────────
# assess_structure (pivots HH/HL vs LH/LL)
# ─────────────────────────────────────────────

def _structure_df(highs, lows):
    return pd.DataFrame({"high": highs, "low": lows})


# Zigzag avec 3 pivots hauts (indices 2, 8, 14) et 3 pivots bas (5, 11, 17)
BULL_HIGHS = [8, 9, 10, 9, 8, 7, 9, 11, 12, 11, 10, 9, 11, 13, 14, 13, 12, 11, 12, 12]
BULL_LOWS  = [6, 7,  8, 7, 6, 5, 7,  9, 10,  9,  8, 6,  9, 11, 12, 11, 10,  7,  9,  9]


class TestAssessStructure:
    def test_bullish_hh_hl(self):
        struct, sh, sl = assess_structure(_structure_df(BULL_HIGHS, BULL_LOWS), n=2)
        assert struct == "bullish"
        assert list(sh) == [10, 12, 14]   # HH
        assert list(sl) == [5, 6, 7]      # HL

    def test_bearish_lh_ll(self):
        # Miroir vertical du cas bullish
        highs = [20 - h for h in BULL_LOWS]
        lows  = [20 - h for h in BULL_HIGHS]
        struct, sh, sl = assess_structure(_structure_df(highs, lows), n=2)
        assert struct == "bearish"

    def test_unclear_with_too_few_pivots(self):
        df = _structure_df([1, 2, 3, 4, 5], [0, 1, 2, 3, 4])  # aucune inversion
        struct, _, _ = assess_structure(df, n=2)
        assert struct == "unclear"

    def test_ranging_equal_pivots(self):
        # Pivots hauts égaux (10, 10) et bas égaux (5, 5) → ni HH ni LH
        highs = [8, 9, 10, 9, 8, 9, 10, 9, 8, 9, 10, 9, 8]
        lows  = [6, 7,  8, 7, 5, 7,  8, 7, 5, 7,  8, 7, 6]
        struct, _, _ = assess_structure(_structure_df(highs, lows), n=2)
        assert struct == "ranging"


# ─────────────────────────────────────────────
# detect_fvg
# ─────────────────────────────────────────────

def _ohlcv_df(rows):
    """rows = list of dicts with open/high/low/close/volume."""
    return pd.DataFrame(rows)


class TestDetectFVG:
    def test_bullish_fvg(self):
        # candle0.high (10) < candle2.low (11) → FVG bullish [10, 11]
        df = _ohlcv_df([
            {"high": 10.0, "low": 9.0},
            {"high": 10.8, "low": 9.8},
            {"high": 12.0, "low": 11.0},
        ])
        fvgs = detect_fvg(df, n_candles=10)
        assert len(fvgs) == 1
        ftype, flo, fhi, _ = fvgs[0]
        assert ftype == "bullish"
        assert flo == 10.0 and fhi == 11.0

    def test_bearish_fvg(self):
        # candle0.low (10) > candle2.high (9) → FVG bearish [9, 10]
        df = _ohlcv_df([
            {"high": 11.0, "low": 10.0},
            {"high": 10.2, "low": 9.2},
            {"high": 9.0,  "low": 8.0},
        ])
        fvgs = detect_fvg(df, n_candles=10)
        assert len(fvgs) == 1
        ftype, flo, fhi, _ = fvgs[0]
        assert ftype == "bearish"
        assert flo == 9.0 and fhi == 10.0

    def test_no_fvg_on_overlapping_candles(self):
        df = _ohlcv_df([{"high": 10, "low": 9}] * 5)
        assert detect_fvg(df, n_candles=10) == []


# ─────────────────────────────────────────────
# detect_candle_confirmation
# ─────────────────────────────────────────────

class TestCandleConfirmation:
    def test_bullish_engulfing(self):
        df = _ohlcv_df([
            {"open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9},
            {"open": 10.0, "close": 9.0,  "high": 10.1, "low": 8.9},   # bear
            {"open": 8.8,  "close": 10.2, "high": 10.3, "low": 8.7},   # bull engulfs
        ])
        pattern, direction = detect_candle_confirmation(df)
        assert pattern == "engulfing_bullish"
        assert direction == "long"

    def test_bearish_engulfing(self):
        df = _ohlcv_df([
            {"open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9},
            {"open": 9.0,  "close": 10.0, "high": 10.1, "low": 8.9},   # bull
            {"open": 10.2, "close": 8.8,  "high": 10.3, "low": 8.7},   # bear engulfs
        ])
        pattern, direction = detect_candle_confirmation(df)
        assert pattern == "engulfing_bearish"
        assert direction == "short"

    def test_bullish_pin_bar(self):
        df = _ohlcv_df([
            {"open": 10.0, "close": 10.0, "high": 10.1, "low": 9.9},
            {"open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9},   # bull (pas engulfing)
            {"open": 10.0, "close": 10.1, "high": 10.15, "low": 9.0},  # long lower wick
        ])
        pattern, direction = detect_candle_confirmation(df)
        assert pattern == "pin_bar_bullish"
        assert direction == "long"

    def test_no_pattern(self):
        df = _ohlcv_df([
            {"open": 10.0, "close": 10.5, "high": 10.6, "low": 9.9},
            {"open": 10.5, "close": 11.0, "high": 11.1, "low": 10.4},
            {"open": 11.0, "close": 11.5, "high": 11.6, "low": 10.9},
        ])
        assert detect_candle_confirmation(df) == (None, None)

    def test_none_df(self):
        assert detect_candle_confirmation(None) == (None, None)


# ─────────────────────────────────────────────
# detect_absorption
# ─────────────────────────────────────────────

class TestAbsorption:
    def _df(self, last_vol, last_open, last_close):
        rows = [{"open": 100, "close": 101, "volume": 100}] * 9
        rows.append({"open": last_open, "close": last_close, "volume": last_vol})
        return _ohlcv_df(rows)

    def test_absorption_detected(self):
        # Volume x~3 la moyenne + body 0.1% → absorption
        absorbed, vol_ratio, body_pct = detect_absorption(self._df(300, 100, 100.1))
        assert absorbed
        assert vol_ratio > 2
        assert body_pct < 0.3

    def test_no_absorption_normal_volume(self):
        absorbed, _, _ = detect_absorption(self._df(100, 100, 100.1))
        assert not absorbed

    def test_no_absorption_big_body(self):
        absorbed, _, _ = detect_absorption(self._df(300, 100, 102))
        assert not absorbed

    def test_insufficient_data(self):
        assert detect_absorption(None) == (False, 0, 0)


# ─────────────────────────────────────────────
# calc_volume_profile
# ─────────────────────────────────────────────

class TestVolumeProfile:
    def test_poc_at_high_volume_zone(self):
        rows = []
        # 20 bougies réparties sur 90-110, volume faible
        for i in range(20):
            lo = 90 + i
            rows.append({"low": lo, "high": lo + 1, "volume": 10})
        # 10 bougies concentrées autour de 100 avec gros volume
        for _ in range(10):
            rows.append({"low": 99.5, "high": 100.5, "volume": 500})
        poc, lvns = calc_volume_profile(_ohlcv_df(rows), n_candles=30, bins=20)
        assert 98.5 <= poc <= 101.5
        assert isinstance(lvns, list)

    def test_flat_range_returns_none(self):
        df = _ohlcv_df([{"low": 100, "high": 100, "volume": 10}] * 15)
        poc, lvns = calc_volume_profile(df)
        assert poc is None and lvns == []

    def test_insufficient_data(self):
        assert calc_volume_profile(None) == (None, [])


# ─────────────────────────────────────────────
# assess_funding — unités : Gate.io renvoie une fraction (0.0001 = 0.01%),
# les seuils sont en % par période 8h.
# ─────────────────────────────────────────────

class TestAssessFunding:
    def _rates(self, fraction):
        return [{"fundingRate": str(fraction), "fundingTime": 0}]

    def test_typical_funding_is_neutral(self):
        val, signal, direction = assess_funding(self._rates(0.0001))  # 0.01%
        assert signal == "neutral" and direction is None

    def test_high_funding_caution(self):
        val, signal, direction = assess_funding(self._rates(0.0004))  # 0.04%
        assert signal == "caution_long" and direction == "short"

    def test_extreme_positive_funding(self):
        val, signal, direction = assess_funding(self._rates(0.0006))  # 0.06%
        assert signal == "danger_long" and direction == "short"

    def test_extreme_negative_funding(self):
        val, signal, direction = assess_funding(self._rates(-0.0006))  # -0.06%
        assert signal == "danger_short" and direction == "long"

    def test_negative_favorable(self):
        val, signal, direction = assess_funding(self._rates(-0.0002))  # -0.02%
        assert signal == "favorable_long" and direction == "long"

    def test_uses_most_recent_rate(self):
        rates = [{"fundingRate": "0.0006", "fundingTime": 0},
                 {"fundingRate": "0.0001", "fundingTime": 1}]
        val, signal, _ = assess_funding(rates)
        assert val == pytest.approx(0.0001)
        assert signal == "neutral"

    def test_empty(self):
        assert assess_funding([]) == (None, "unknown", None)


# ─────────────────────────────────────────────
# assess_oi
# ─────────────────────────────────────────────

class TestAssessOI:
    def test_insufficient_data_returns_neutral_percentile(self):
        # Régression : renvoyait un 3-tuple → ValueError à l'unpack
        change, current, pct_max, percentile = assess_oi(None)
        assert percentile == 50.0

    def test_percentile_at_max(self):
        df = pd.DataFrame({"sumOpenInterestValue": list(range(1, 101))})
        change, current, pct_max, percentile = assess_oi(df)
        assert percentile == 100.0
        assert pct_max == 100.0
        assert current == 100

    def test_percentile_mid(self):
        values = list(range(1, 101)) + [50]
        df = pd.DataFrame({"sumOpenInterestValue": values})
        *_, percentile = assess_oi(df)
        assert 45 <= percentile <= 55

    def test_zero_base_no_crash(self):
        df = pd.DataFrame({"sumOpenInterestValue": [0, 0, 0, 0, 0, 10]})
        change, *_ = assess_oi(df)
        assert change == 0.0


# ─────────────────────────────────────────────
# assess_ls_ratio (contrarian)
# ─────────────────────────────────────────────

class TestLSRatio:
    def _df(self, long_frac):
        return pd.DataFrame({"longAccount": [long_frac],
                             "shortAccount": [1 - long_frac]})

    def test_crowded_long_gives_short_signal(self):
        val, signal, direction = assess_ls_ratio(self._df(0.75))
        assert signal == "crowded_long" and direction == "short"

    def test_crowded_short_gives_long_signal(self):
        val, signal, direction = assess_ls_ratio(self._df(0.25))
        assert signal == "crowded_short" and direction == "long"

    def test_neutral(self):
        val, signal, direction = assess_ls_ratio(self._df(0.60))
        assert signal == "neutral" and direction is None

    def test_empty(self):
        assert assess_ls_ratio(None) == (None, "unknown", None)


# ─────────────────────────────────────────────
# assess_taker_flow (CVD réel)
# ─────────────────────────────────────────────

def _flow_df(buys, sells, prices):
    return pd.DataFrame({"long_taker_size": buys,
                         "short_taker_size": sells,
                         "mark_price": prices})


class TestTakerFlow:
    def test_aligned_bullish(self):
        df = _flow_df([600]*6, [400]*6, [100, 100.5, 101, 101.5, 101.8, 102])
        status, direction, cvd, share, chg = assess_taker_flow(df)
        assert status == "aligned_bullish" and direction == "long"
        assert cvd == 1200
        assert share == pytest.approx(0.6)

    def test_divergence_bearish_price_up_flow_sell(self):
        df = _flow_df([400]*6, [600]*6, [100, 100.5, 101, 101.5, 101.8, 102])
        status, direction, *_ = assess_taker_flow(df)
        assert status == "divergence_bearish" and direction is None

    def test_divergence_bullish_price_down_flow_buy(self):
        df = _flow_df([600]*6, [400]*6, [102, 101.5, 101, 100.5, 100.2, 100])
        status, direction, *_ = assess_taker_flow(df)
        assert status == "divergence_bullish" and direction is None

    def test_flat_price_directional_flow(self):
        df = _flow_df([600]*6, [400]*6, [100]*6)
        status, direction, *_ = assess_taker_flow(df)
        assert status == "price_flat" and direction == "long"

    def test_balanced_flow_is_neutral(self):
        df = _flow_df([500]*6, [500]*6, [100, 101, 102, 103, 104, 105])
        status, direction, *_ = assess_taker_flow(df)
        assert status == "flow_neutral" and direction is None

    def test_none_df(self):
        status, *_ = assess_taker_flow(None)
        assert status == "unclear"


# ─────────────────────────────────────────────
# check_session — le cron tourne à 07/13/17/21 UTC : aucun ne doit
# tomber en session asiatique (régression : 21h UTC était bloqué).
# ─────────────────────────────────────────────

class TestSession:
    def _at(self, hour, minute=0):
        from datetime import datetime, timezone
        return datetime(2026, 7, 12, hour, minute, tzinfo=timezone.utc)

    @pytest.mark.parametrize("hour,expected", [
        (7, "london"), (13, "us"), (17, "us"), (21, "us"),
    ])
    def test_cron_hours_not_asian(self, hour, expected):
        session_id, _, _ = check_session(self._at(hour))
        assert session_id == expected

    def test_cron_21h_with_actions_delay(self):
        # Le cron de 21h part souvent avec 30-60 min de retard
        session_id, _, _ = check_session(self._at(21, 55))
        assert session_id == "us"

    def test_asian_blocked_hours(self):
        assert check_session(self._at(23))[0] == "asian"
        assert check_session(self._at(3))[0]  == "asian"
        assert check_session(self._at(22))[0] == "asian"

    def test_inter_session(self):
        assert check_session(self._at(12))[0] == "inter"
