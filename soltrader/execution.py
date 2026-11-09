"""Simulateur d'exécution — partagé par le backtest et le paper trading.

Règles (pré-enregistrées dans research/preregistration.md, fixées le
2026-11-09 avant tout calcul de rendement) :
- entrée au prix du signal, ordre taker ;
- stop = pivot structurel 4H de R1, ± SL_BUFFER ; trade refusé si la
  distance au stop sort de [MIN_STOP_PCT, MAX_STOP_PCT] ;
- objectif = RR_TARGET × risque (R:R 1:2 de la checklist) ;
- sortie forcée après MAX_HOLD_H heures, à la clôture de la bougie ;
- stop et objectif touchés dans la même bougie 15 min → stop (prudent) ;
- coûts : frais taker + glissement à l'entrée et à la sortie, funding
  réellement versé aux échéances 8h pendant la détention.
"""

from dataclasses import dataclass, asdict, field
from typing import Optional

import pandas as pd

from .config import (FEE_TAKER, SLIPPAGE, SL_BUFFER, RR_TARGET, MAX_HOLD_H,
                     MIN_STOP_PCT, MAX_STOP_PCT, LEVERAGE)


@dataclass
class Trade:
    direction: str
    entry_time: str            # ISO 8601 UTC
    entry: float
    stop: float
    target: float
    risk_pct: float            # distance entrée → stop, en % du prix
    status: str = "open"       # open | closed
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None   # stop | target | time
    gross_pct: Optional[float] = None   # en % du notionnel
    costs_pct: Optional[float] = None   # frais + glissement
    funding_pct: Optional[float] = None # >0 = payé
    net_pct: Optional[float] = None
    r_net: Optional[float] = None       # résultat net en multiples du risque
    margin_pct: Optional[float] = None  # net × levier = % de la marge engagée
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def plan_trade(direction, entry, entry_time, sl_level, meta=None):
    """Construit un Trade depuis le niveau structurel R1. Retourne (trade, raison_refus)."""
    if direction not in ("long", "short"):
        return None, "pas de direction"
    if not entry or sl_level is None:
        return None, "pas de SL structurel"
    if direction == "long":
        stop = sl_level * (1 - SL_BUFFER)
        risk = entry - stop
    else:
        stop = sl_level * (1 + SL_BUFFER)
        risk = stop - entry
    if risk <= 0:
        return None, "SL du mauvais côté du prix"
    risk_pct = risk / entry * 100
    if risk_pct < MIN_STOP_PCT:
        return None, f"SL trop serré ({risk_pct:.2f}%)"
    if risk_pct > MAX_STOP_PCT:
        return None, f"SL trop large ({risk_pct:.2f}%)"
    target = entry + RR_TARGET * risk if direction == "long" else entry - RR_TARGET * risk
    t = pd.Timestamp(entry_time)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return Trade(direction, t.isoformat(), float(entry), float(stop), float(target),
                 float(risk_pct), meta=dict(meta or {})), None


def _funding_paid(trade, funding, until):
    """Somme signée des taux versés pendant la détention (>0 = coût)."""
    start = pd.Timestamp(trade.entry_time).timestamp()
    end = pd.Timestamp(until).timestamp()
    total = sum(float(r["fundingRate"]) for r in funding or []
                if start < int(r["fundingTime"]) <= end)
    sign = 1 if trade.direction == "long" else -1
    return sign * total * 100


def _close(trade, exit_time, exit_price, reason, funding):
    sign = 1 if trade.direction == "long" else -1
    trade.status = "closed"
    trade.exit_time = pd.Timestamp(exit_time).isoformat()
    trade.exit_price = float(exit_price)
    trade.exit_reason = reason
    trade.gross_pct = sign * (exit_price - trade.entry) / trade.entry * 100
    trade.costs_pct = 2 * (FEE_TAKER + SLIPPAGE) * 100
    trade.funding_pct = _funding_paid(trade, funding, exit_time)
    trade.net_pct = trade.gross_pct - trade.costs_pct - trade.funding_pct
    trade.r_net = trade.net_pct / trade.risk_pct
    trade.margin_pct = trade.net_pct * LEVERAGE
    return trade


def simulate(trade, candles_15m, funding=None):
    """Fait avancer un trade ouvert sur les bougies 15 min CLÔTURÉES.

    Les bougies dont la période contient l'entrée sont incluses (le prix
    peut toucher le stop dès la première bougie). Retourne le trade, fermé
    si une sortie a eu lieu, sinon inchangé (toujours ouvert).
    """
    if trade.status != "open" or candles_15m is None or candles_15m.empty:
        return trade
    entry_t = pd.Timestamp(trade.entry_time)
    deadline = entry_t + pd.Timedelta(hours=MAX_HOLD_H)
    path = candles_15m[candles_15m.index + pd.Timedelta("15min") > entry_t]
    for open_time, c in path.iterrows():
        close_time = open_time + pd.Timedelta("15min")
        if trade.direction == "long":
            hit_stop, hit_target = c["low"] <= trade.stop, c["high"] >= trade.target
        else:
            hit_stop, hit_target = c["high"] >= trade.stop, c["low"] <= trade.target
        if hit_stop:
            return _close(trade, close_time, trade.stop, "stop", funding)
        if hit_target:
            return _close(trade, close_time, trade.target, "target", funding)
        if close_time >= deadline:
            return _close(trade, close_time, c["close"], "time", funding)
    return trade


def summarize(trades):
    """Métriques d'une liste de trades fermés."""
    closed = [t for t in trades if t.status == "closed"]
    n = len(closed)
    if not n:
        return {"n": 0}
    r = pd.Series([t.r_net for t in closed])
    net = pd.Series([t.net_pct for t in closed])
    equity_r = r.cumsum()
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    hold_h = pd.Series([(pd.Timestamp(t.exit_time) - pd.Timestamp(t.entry_time)).total_seconds() / 3600
                        for t in closed])
    return {
        "n": n,
        "win_rate": float((r > 0).mean()),
        "avg_r": float(r.mean()),
        "median_r": float(r.median()),
        "sum_r": float(r.sum()),
        "profit_factor": float(wins / losses) if losses else float("inf"),
        "max_drawdown_r": float((equity_r.cummax().clip(lower=0) - equity_r).max()),
        "avg_net_pct": float(net.mean()),
        "avg_costs_pct": float(pd.Series([t.costs_pct + t.funding_pct for t in closed]).mean()),
        "avg_hold_h": float(hold_h.mean()),
        "exits": {k: int(v) for k, v in pd.Series([t.exit_reason for t in closed]).value_counts().items()},
        "long": int(sum(t.direction == "long" for t in closed)),
        "short": int(sum(t.direction == "short" for t in closed)),
    }
