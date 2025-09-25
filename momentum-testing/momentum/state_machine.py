from dataclasses import dataclass
from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Optional, Tuple

#State Definitions 
STATE_NEUTRAL = "NEUTRAL"
STATE_COILING = "COILING"
STATE_ARMED_UP = "ARMED_UP"
STATE_ARMED_DOWN = "ARMED_DOWN"
STATE_FIRE_UP = "FIRE_UP"
STATE_FIRE_DOWN = "FIRE_DOWN"
STATE_WATCH = "WATCH"

#Reasons 
R_EMBARGO_OPEN = "EMBARGO_OPEN"
R_EMBARGO_CLOSE = "EMBARGO_CLOSE"
R_FEED_STALL = "FEED_STALL"
R_IV_SUPPRESS_UP = "IV_SUPPRESS_UP"
R_IV_SUPPRESS_DOWN = "IV_SUPPRESS_DOWN"
R_COOLDOWN_UP = "COOLDOWN_UP"
R_COOLDOWN_DN = "COOLDOWN_DOWN"
R_FIRE_UP = "FIRE_UP_OK"
R_FIRE_DOWN = "FIRE_DOWN_OK"
R_ARMED_UP_OK = " ARMED_UP_OK"
R_ARMED_DN_OK = "ARMED_DOWN_OK"
R_COILING_OK = "COILING_OK"
R_IDLE = "IDLE"

@dataclass
class Bar: 
    ts_close : datetime 
    open: float 
    high: float 
    low: float 
    close: float 
    volume: float 
    tr: float
    atr20: float 
    hh20: float
    ll20 = float

class Features: 
    donch_width: float 
    atr_ratio: float 
    slope: float 
    pressure: float 

class IVcontext: 
    atm_iv: Optional [float]
    percentile: Optional [float]
    updated_ts: Optional [float]
    quality: str

class StateSnapshot: 
    state: str 
    reason: str
    direction: str 
    cooldown_remaining_min: str

class SimpleStateMachine: 

    def __init__(self, cfg: dict, clock) -> None:
        self.cfg = cfg
        self.clock = clock
        self._slope_signs: Deque[int] = deque (maxlen=5)
        self._cdn_up_until: Optional [datetime] = None
        self._cdn_dn_until: Optional [datetime] = None 
        self._last_dir: str = "NA"

    def step(self, bar: Bar, f: Features, iv: IVcontext, now: datetime, heartbeat_age_s: float, is_expiry_day: bool=False) -> StateSnapshot:
        """Compute the current state for this minute close."""
        # 1 ops vetoes 
        veto = self._ops_vetoes(now, heartbeat_age_s)
        if veto is not None: 
            return veto 
        
        self._update_slope_votes(f.slope)

        coiling = self._is_coiling (f)
        armed_up, armed_dn = self._is_armed (f, coiling)
        break_up, break_dn, dist_up_bps, dist_dn_bps = self._is_break(bar, f, coiling)

        iv_ok_up, iv_ok_dn = self._iv_gate(iv)

        cdn_left_up, cdn_left_dn = self._cooldown_left(now)
        if break_up and cdn_left_up > 0: 
            return self._snap(STATE_WATCH, R_COOLDOWN_UP, "UP", cdn_left_up )
        if break_dn and cdn_left_dn > 0: 
            return self._snap (STATE_WATCH, R_COOLDOWN_DN, "DOWN", cdn_left_dn)

        if break_up:
            if iv_ok_up: 
                self._arm_cooldown("UP", now)
                return self._snap(STATE_FIRE_UP, R_FIRE_UP, "UP", self.cfg["ops"]["cooldown_min"], cdn_left_up)
            else:
                return self._snap(STATE_WATCH, R_IV_SUPPRESS_UP, "UP", 0)
            
        if break_dn:
            if iv_ok_dn: 
                self._arm_cooldown("DOWN", now)
                return self._snap(STATE_FIRE_DOWN, R_FIRE_DOWN, "DOWN", self.cfg["ops"]["cooldown_min"], cdn_left_dn)
            else:
                return self._snap(STATE_WATCH, R_IV_SUPPRESS_DOWN, "DOWN", 0)
        
        if armed_up:
            return self._snap(STATE_ARMED_UP, R_ARMED_UP_OK, "UP", cdn_left_up)
        if armed_dn:
            return self._snap(STATE_ARMED_DOWN, R_ARMED_DN_OK, "DOWN", cdn_left_dn)
        
        if coiling: 
            return self._snap(STATE_COILING, R_COILING_OK, "NA", 0)
        
        return self._snap(STATE_NEUTRAL, R_IDLE, "NA", 0)
    
    def _ops_vetoes(self, now: datetime, heartbeat_age_s: float) -> Optional[StateSnapshot]: 
        """Open/close embargo, feed stall; return a WATCH snapshot or None."""
        # a) Feed health
        if heartbeat_age_s >= self.cfg["ops"]["heartbeat_error_s"]:
            return self._snap(STATE_WATCH, R_FEED_STALL, "NA", 0)
        # b) Embargoes
        if self.clock.in_open_embargo(now):
            return self._snap(STATE_WATCH, R_EMBARGO_OPEN, "NA", 0)
        if self.clock.in_close_embargo(now):
            return self._snap(STATE_WATCH, R_EMBARGO_CLOSE, "NA", 0)
        return None
    
    def _update_slope_votes(self, slope: float) -> None:
        """Append slope sign into a 5-slot deque."""
        sign = 1 if slope > 0 else -1 if slope < 0 else 0
        self._slope_signs.append(sign)

    def _agree_counts(self) -> Tuple[int, int]: 
        """Count how many of the last 5 slopes are +1 or -1."""
        up = sum(1 for s in self._slope_signs if s > 0)
        dn = sum(1 for s in self._slope_signs if s < 0)
        return up, dn
    
    def _is_coiling(self, f: Features) -> bool:
        return (f.atr_ratio < self.cfg["features"]["contraction_threshold"] and f.donch_width <= self.cfg["features"]["max_donch_width_pct"])


    def _is_armed(self, f: Features, coiling: bool) -> Tuple[bool, bool]: 
        """Directional readiness: coiling + directional agreement + pressure sign."""
        need = self.cfg["features"]["armed_agree_count"]  # e.g. 3 of last 5
        up_ct, dn_ct = self._agree_counts()
        armed_up = coiling and (up_ct >= need) and (f.pressure > 0)
        armed_dn = coiling and (dn_ct >= need) and (f.pressure < 0)
        return armed_up, armed_dn

    def _is_break(self, bar: Bar, f: Features) -> Tuple[bool, bool, float, float]: 
        """Band break with distance filter (bps) and energy (TR vs ATR20)."""
        # Required cfg:
        # break_bps, bar_tr_min_atr
        eps = 1e-12
        dist_up_bps   = (bar.close / max(bar.hh20, eps) - 1.0) * 10000.0
        dist_dn_bps   = (1.0 - bar.close / max(bar.ll20, eps)) * 10000.0
        energy_ok     = bar.tr >= self.cfg["features"]["bar_tr_min_atr"] * (bar.atr20 or 0.0)
        break_up      = (dist_up_bps   >= self.cfg["features"]["break_bps"]) and energy_ok
        break_dn      = (dist_dn_bps   >= self.cfg["features"]["break_bps"]) and energy_ok
        return break_up, break_dn, dist_up_bps, dist_dn_bps

    def _iv_gate(self, iv: IVcontext) -> Tuple[bool, bool]:
        """Check IV freshness and percentile caps; symmetric for UP/DOWN."""
        # If you don't use IV yet, just return (True, True)
        if iv.quality == "NA":
            return True, True
        if iv.quality == "STALE":
            return False, False
        cap = self.cfg["iv"]["max_iv_percentile_for_fire"]  # e.g. 85
        if iv.percentile is None:
            return True, True
        ok = iv.percentile <= cap
        return ok, ok

    def _cooldown_left(self, now: datetime) -> Tuple[int, int]:
        """Minutes left on UP/DOWN cooldowns, clipped to 0."""
        def left(until: Optional[datetime]) -> int:
            if until is None:
                return 0
            return max(0, int((until - now).total_seconds() // 60))
        return left(self._cdn_up_until), left(self._cdn_dn_until)

    def _arm_cooldown(self, direction: str, now: datetime) -> None:
        """Start direction-specific cooldown timer after a FIRE."""
        mins = self.cfg["ops"]["cooldown_min"]
        if direction == "UP":
            self._cdn_up_until = now + timedelta(minutes=mins)
        elif direction == "DOWN":
            self._cdn_dn_until = now + timedelta(minutes=mins)
        self._last_dir = direction

    @staticmethod
    def _snap(state: str, reason: str, direction: str, cdn_min: int) -> StateSnapshot:
        return StateSnapshot(state=state, reason=reason, direction=direction, cooldown_remaining_min=cdn_min)