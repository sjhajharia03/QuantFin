# features_engine.py
import polars as pl
import numpy as np
from numba import njit

@njit
def rolling_median_numba(x, win):
    out = np.empty(x.size)
    out[:] = np.nan
    for i in range(x.size):
        j = max(0, i - win + 1)
        seg = np.sort(x[j:i+1])
        out[i] = seg[len(seg)//2]
    return out

class PolarsFeatureEngine:
    def __init__(self, donch_window=20, atr_median_len=100, slope_len=9, pressure_len=15):
        self.donch = donch_window
        self.atr_median_len = atr_median_len
        self.slope_len = slope_len
        self.pressure_len = pressure_len

    def compute(self, bars_window: pl.DataFrame) -> pl.DataFrame:
        df = bars_window.with_columns([
            pl.col("close").pct_change().alias("ret"),
            # True Range: max(h-l, |h-prev_c|, |l-prev_c|)
            pl.max_horizontal(
                pl.col("high") - pl.col("low"),
                (pl.col("high") - pl.col("close").shift(1)).abs(),
                (pl.col("low") - pl.col("close").shift(1)).abs()
            ).alias("tr"),
        ]).with_columns([
            pl.col("tr").rolling_mean(self.donch).alias("atr20"),
            pl.col("close").rolling_max(self.donch).alias("hh20"),
            pl.col("close").rolling_min(self.donch).alias("ll20"),
        ]).with_columns([
            ((pl.col("hh20") - pl.col("ll20")) / pl.col("close")).alias("donch_width"),
            # slope via EMA of returns; Polars has ewm_mean
            pl.col("ret").ewm_mean(alpha=2/(self.slope_len+1)).alias("slope"),
            pl.col("ret").rolling_sum(self.pressure_len).alias("pressure"),
        ])

        # Robust ATR baseline with Numba median (optional; could use rolling_median)
        atr = df["atr20"].to_numpy()
        base = rolling_median_numba(np.nan_to_num(atr, nan=np.nan), min(self.atr_median_len, len(atr)))
        df = df.with_columns(pl.Series("atr_median", base))
        df = df.with_columns((pl.col("atr20") / pl.col("atr_median")).alias("atr_ratio"))
        return df
