"""Fee-floor + net-EV pre-entry gate (PROPOSE-ONLY)."""
from __future__ import annotations
from dataclasses import dataclass

TAKER_FEE = 0.0006
ROUND_TRIP = TAKER_FEE * 2


@dataclass
class FeeVerdict:
    fee_r: float
    net_ev_r: float
    passed: bool
    reason: str


def fee_in_r(entry: float, stop: float, round_trip: float = ROUND_TRIP) -> float:
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return float("inf")
    return entry * round_trip / stop_dist


def net_ev_r(
    win_prob: float,
    reward_r: float,
    fee_r: float,
    funding_r: float = 0.0,
) -> float:
    return win_prob * reward_r - (1.0 - win_prob) * 1.0 - fee_r - funding_r


def evaluate(
    entry: float,
    stop: float,
    reward_r: float,
    win_prob: float,
    max_fee_r: float = 0.15,
    min_net_ev_r: float = 0.0,
    funding_r: float = 0.0,
) -> FeeVerdict:
    fr = fee_in_r(entry, stop, ROUND_TRIP)
    ev = net_ev_r(win_prob, reward_r, fr, funding_r)
    if fr > max_fee_r:
        return FeeVerdict(
            fr, ev, False,
            f"FEE_FLOOR: fee={fr:.3f}R > max {max_fee_r:.3f}R (stop too tight)",
        )
    if ev < min_net_ev_r:
        return FeeVerdict(fr, ev, False, f"NET_EV: {ev:.3f}R < min {min_net_ev_r:.3f}R")
    return FeeVerdict(fr, ev, True, "ok")
