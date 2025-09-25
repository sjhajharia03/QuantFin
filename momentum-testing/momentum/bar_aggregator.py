# bar_aggregator.py
import polars as pl
from datetime import timedelta
from .core_contracts import Tick

class PolarsBarAggregator:
    def __init__(self, window_minutes=180):
        self.window_minutes = window_minutes
        # initialize empty typed frames
        self._ticks = pl.DataFrame(schema={"ts": pl.Datetime, "price": pl.Float64, "vol": pl.Float64})
        self._last_min_ts = None
        self._bars = pl.DataFrame(schema={
            "ts_close": pl.Datetime, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64
        })

    def push_tick(self, t: Tick):
        # defensive: ensure tick has ts, last, volume
        ts = getattr(t, "ts", None)
        if ts is None:
            return
        # make ts naive (your _ticks are stored naive)
        ts_naive = ts.replace(tzinfo=None)
        price = float(getattr(t, "last", 0.0))
        vol = float(getattr(t, "volume", 0.0) or 0.0)

        self._ticks = pl.concat([self._ticks, pl.DataFrame({"ts": [ts_naive], "price": [price], "vol": [vol]})], how="vertical")

        cutoff = ts - timedelta(minutes=self.window_minutes)
        cutoff = cutoff.replace(tzinfo=None)
        # filter requires both sides same tz-awareness (we store naive)
        self._ticks = self._ticks.filter(pl.col("ts") >= cutoff)

    def minute_ready(self) -> bool:
        if self._ticks.height == 0:
            return False

        cur_ts = self._ticks[-1, "ts"]
        # ensure python datetime object
        cur_min = cur_ts.replace(second=0, microsecond=0)
        if self._last_min_ts is None:
            # initialize and wait for next minute
            self._last_min_ts = cur_min
            return False

        if cur_min > self._last_min_ts:
            return True
        return False

    def finalize_bar(self):
        """
        Return (bar_dict, rolling_window_df). Bar is dict with keys:
        ts_close, open, high, low, close, volume
        """
        if self._ticks.height == 0:
            return None

        latest_ts = self._ticks[-1, "ts"]
        timeframe_cut = latest_ts - timedelta(minutes=2)
        df = self._ticks.filter(pl.col("ts") >= timeframe_cut)

        # use lazy and dynamic grouping; group_by_dynamic is the correct name
        bars_df = (df.lazy()
                     .group_by_dynamic(index_column="ts", every="1m", closed="right")
                     .agg([
                         pl.col("price").first().alias("open"),
                         pl.col("price").max().alias("high"),
                         pl.col("price").min().alias("low"),
                         pl.col("price").last().alias("close"),
                         pl.col("vol").sum().alias("volume"),
                     ])
                     .rename({"ts": "ts_close"})
                     .collect())

        if bars_df.height == 0:
            return None

        bars_df = bars_df.sort("ts_close")
        last_bar_dicts = bars_df.to_dicts()
        bar = last_bar_dicts[-1]

        # update last_min_ts and append to self._bars, pruning to window
        self._last_min_ts = bar["ts_close"]
        self._bars = pl.concat([self._bars, pl.DataFrame([bar])], how="vertical")

        cutoff = self._last_min_ts - timedelta(minutes=self.window_minutes)
        self._bars = self._bars.filter(pl.col("ts_close") >= cutoff)

        return bar, self._bars
