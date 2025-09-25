# momentum/core_contracts.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Optional, Protocol, Tuple

# --------- Data containers (real objects, not just annotations) ---------

@dataclass
class Tick:
    ts: datetime
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[float] = None

@dataclass
class Bar:
    ts_close: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    tr: float          # true range
    atr20: float       # rolling ATR over 20 bars
    hh20: float        # rolling high over 20 bars
    ll20: float        # rolling low over 20 bars

@dataclass
class Features:
    donch_width: float   # (hh20 - ll20) / close
    atr_ratio: float     # atr20 / median(atr20)
    slope: float         # EMA of 1-min returns
    pressure: float      # short-term directional push

@dataclass
class IVcontext:
    atm_iv: Optional[float]
    percentile: Optional[float]
    updated_ts: Optional[datetime]
    quality: str  # "OK" | "STALE" | "NA"

@dataclass
class StateSnapshot:
    state: str                 # "NEUTRAL" | "COILING" | "ARMED_UP" | ...
    reason: str                # short code like FIRE_UP_OK, COOLDOWN_UP
    direction: str             # "UP" | "DOWN" | "NA"
    cooldown_remaining_min: int

# --------- Interfaces (contracts) ---------

class MarketDataSource(Protocol):
    def connect(self, symbol: str = ...) -> None: ...
    def subscribe(self, symbol: str) -> Iterator[Tick]: ...
    def last_heartbeat_age_s(self) -> float: ...

class OptionChainSource(Protocol):
    def fetch(self, symbol: str) -> dict: ...  # ATM ± strikes, IV, ts, etc.

class BarAggregator(Protocol):
    def push_tick(self, t: Tick) -> None: ...
    def minute_ready(self) -> bool: ...
    def finalize_bar(self) -> Tuple[Bar, Any]: ...
    # returns (finalized Bar, rolling window DataFrame)

class FeatureEngine(Protocol):
    def compute(self, bars_window: Any) -> Any: ...
    # returns a DataFrame with at least: donch_width, atr_ratio, slope, pressure

class StateMachine(Protocol):
    def step(
        self,
        bar: Bar,
        feats: Features,
        iv: IVcontext,
        now: datetime,
        heartbeat_age_s: float,
        is_expiry_day: bool = False,
    ) -> StateSnapshot: ...
