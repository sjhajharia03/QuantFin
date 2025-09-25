# app.py
import argparse
import os
import yaml
import time
import inspect
import threading
import queue
from datetime import datetime
from momentum.session_clock import SessionClock
from momentum.bar_aggregator import PolarsBarAggregator
from momentum.features_engine import PolarsFeatureEngine
from momentum.iv_context import IVcontextNumba
from momentum.state_machine import SimpleStateMachine
from momentum.ui_panel import render
from momentum.persistence import append_bar_csv, append_state_csv
from momentum.feed_broker_kite import KiteFeed

# ---------------- utils ----------------

def load_cfg(path: str):
    """Loads configuration from a YAML file."""
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}

def build_clock(cfg):
    s = cfg.get("session", {})
    return SessionClock(
        tz=s.get("tz", "UTC"),
        open_str=s.get("open", "09:15"),
        close_str=s.get("close", "15:30"),
        open_embargo_min=s.get("open_embargo_min", 0),
        close_embargo_min=s.get("close_embargo_min", 0),
        expiry_afternoon_strict_after=s.get("expiry_afternoon_strict_after", "15:30"),
    )

def _feed_to_iterator(feed, timeout=1.0):
    # direct iterator
    if hasattr(feed, "stream") and callable(feed.stream):
        return feed.stream()
    if hasattr(feed, "__iter__"):
        return iter(feed)

    q = queue.Queue()
    stop_evt = threading.Event()  # noqa: F841

    def _cb(tick):
        q.put(tick)

    # try callback-style start/run/connect
    for name in ("start", "run", "connect"):
        fn = getattr(feed, name, None)
        if not callable(fn):
            continue
        sig = inspect.signature(fn)
        kwargs = {}
        if "callback" in sig.parameters:
            kwargs["callback"] = _cb
        elif "cb" in sig.parameters:
            kwargs["cb"] = _cb
        # spawn thread to run feed
        def _runner(fn=fn, kwargs=kwargs):
            try:
                fn(**kwargs) if kwargs else fn(_cb)
            except TypeError:
                try:
                    fn(_cb)
                except Exception:
                    pass
        t = threading.Thread(target=_runner, daemon=True)
        t.start()

        def _gen():
            while True:
                try:
                    item = q.get(timeout=timeout)
                    if item is None:
                        break
                    yield item
                except queue.Empty:
                    if not t.is_alive() and q.empty():
                        break
                    continue
        return _gen()

    raise RuntimeError("KiteFeed does not expose stream()/__iter__ or a callback-based start/run/connect")

def prepare_run_dir():
    run_date = datetime.today().strftime("%Y-%m-%d")
    run_dir = os.path.join("runs", run_date)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir

# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="Intraday breakout panel (Kite websocket)")
    ap.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    ap.add_argument("--symbol", default="NIFTY", help="Symbol to stream (NIFTY spot/fut via token resolution)")
    ap.add_argument("--instrument-kind", default="index", choices=["index", "fut"],
                    help="Stream NIFTY index LTP or nearest futures")
    ap.add_argument("--kite-api-key", required=True, help="Kite API key")
    ap.add_argument("--kite-access-token", required=True, help="Kite DAILY access token")
    ap.add_argument("--persist", action="store_true", help="Write bars/state CSVs under runs/YYYY-MM-DD/")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    clock = build_clock(cfg)
    run_dir = prepare_run_dir() if args.persist else None

    features_cfg = cfg.get("features", {})
    donch_w = features_cfg.get("donch_window", 180)

    bars = PolarsBarAggregator(window_minutes=donch_w)
    feats = PolarsFeatureEngine(
        donch_window=donch_w,
        atr_median_len=features_cfg.get("atr_median_len", 14),
        slope_len=features_cfg.get("slope_ema_lookback", 10),
        pressure_len=features_cfg.get("pressure_len", 20),
    )
    ivctx = IVcontextNumba()
    sm = SimpleStateMachine(cfg, clock)

    # instantiate KiteFeed (positional by default)
    feed = KiteFeed(args.kite_api_key, args.kite_access_token)

    # best-effort: set symbol if supported
    if hasattr(feed, "set_symbol"):
        try:
            feed.set_symbol(args.symbol, args.instrument_kind)
        except Exception:
            pass
    elif hasattr(feed, "subscribe"):
        try:
            feed.subscribe(args.symbol)
        except Exception:
            pass

    stream_iter = _feed_to_iterator(feed)

    try:
        for tick in stream_iter:
            # basic session guard
            if not clock.is_open():
                time.sleep(1)
                continue

            if tick is None:
                continue

            try:
                bars.push_tick(tick)
            except Exception:
                # skip malformed tick but keep running
                continue

            if not bars.minute_ready():
                continue

            res = bars.finalize_bar()
            if not res:
                continue
            bar, window = res

            if args.persist and bar is not None:
                append_bar_csv(run_dir, bar)

            last = feats.compute(window)
            if not last or last.get("donch_width") is None:
                # not enough data yet
                continue

            iv = ivctx.compute(
                ts=last["ts_close"],
                close=float(last["close"]),
                donch_width=float(last["donch_width"]),
                atr=float(last["atr"]),
            )

            state = sm.evaluate(last, iv)

            if args.persist:
                append_state_csv(run_dir, state)

            render(state)
    except KeyboardInterrupt:
        print("Exiting gracefully...")

if __name__ == "__main__":
    main()
