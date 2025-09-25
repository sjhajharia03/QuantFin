# iv_context.py
import numpy as np
from numba import njit
from datetime import datetime, timedelta  # noqa: F401
from .core_contracts import IVcontext

@njit
def percentile_rank(x, value):
    n = 0
    for i in range(x.size):
        if not np.isnan(x[i]) and x[i] <= value:
            n += 1
    m = 0
    for i in range(x.size):
        if not np.isnan(x[i]):
            m += 1
    if m == 0: 
        return np.nan
    return 100.0 * n / m

class IVcontextNumba:
    def __init__(self, lookback_minutes=60, stale_after_s=180):
        self.ivs = np.full(600, np.nan)  # enough for 60 min if sampled every ~6s
        self.ts = np.full(600, None)
        self.idx = 0
        self.lb = lookback_minutes
        self.stale_after = stale_after_s

    def update(self, atm_iv: float, ts: datetime) -> IVcontext:
        self.ivs[self.idx % self.ivs.size] = float(atm_iv)
        self.ts[self.idx % self.ts.size] = ts
        self.idx += 1
        latest = self.ivs[~np.isnan(self.ivs)]
        pct = percentile_rank(latest, latest[-1]) if latest.size else np.nan
        age = (datetime.now(ts.tzinfo) - ts).total_seconds()
        qual = "OK" if age <= self.stale_after else "STALE"
        return IVcontext(atm_iv=atm_iv, percentile=pct, updated_ts=ts, quality=qual)
