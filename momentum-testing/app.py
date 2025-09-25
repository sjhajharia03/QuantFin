# app.py
import argparse
import yaml
from datetime import datetime

from momentum.session_clock import SessionClock
from momentum.bar_aggregator import PolarsBarAggregator
from momentum.features_engine import PolarsFeatureEngine
from momentum.iv_context import IVcontextNumba     # your custom name is fine
from momentum.state_machine import SimpleStateMachine
from momentum.ui_panel import render
from momentum.persistence import append_bar_csv, append_state_csv
from momentum.feed_broker_kite import KiteFeed


# ---------------- config helpers ----------------

def load_cfg(path: str) -> dict:
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    return data

def build_clock(cfg: dict) -> SessionClock:
    s = cfg.get("session", {})
    return SessionClock(
        tz=s.get("tz", "Asia/Kolkata"),
        open_str=s.get("open", "09:15"),
        close_str=s.get("close", "15:30"),
        open_embargo_min=s.get("open_embargo_min", 15),
        close_embargo_min=s.get("close_embargo_min", 20),
        expiry_afternoon_strict_after=s.get("expiry_afternoon_strict_after", "14:30"),
    )


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser(description="NIFTY breakout panel (Kite websocket)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--instrument-kind", default="index", choices=["index", "fut"])
    ap.add_argument("--kite-api-key", required=True)
    ap.add_argument("--kite-access-token", required=True)
    ap.add_argument("--persist", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    clock = build_clock(cfg)

    # ---------- plumbing you actually need ----------
    fcfg = cfg.get("features", {})
    bars = PolarsBarAggregator(window_minutes=fcfg.get("donch_window", 20))  # rolling window len
    feats = PolarsFeatureEngine(
        donch_window=fcfg.get("donch_window", 20),
        atr_median_len=fcfg.get("atr_median_len", 100),
        slope_len=fcfg.get("slope_ema_lookback", 9),
        pressure_len=fcfg.get("pressure_len", 15),
    )
    ivctx = IVcontextNumba(lookback_minutes=cfg.get("iv", {}).get("lookback_minutes", 60))
    sm = SimpleStateMachine(cfg, clock)

    # broker feed
    feed = KiteFeed(
        api_key=args.kite_api_key,
        access_token=args.kite_access_token,
        instrument_kind=args.instrument_kind,
    )
    feed.connect(symbol=args.symbol)

    # file outputs
    day = datetime.now().strftime("%Y-%m-%d")
    bars_path = f"runs/{day}/bars.csv"
    state_path = f"runs/{day}/state.csv"

    # ---------- main loop ----------
    for tick in feed.subscribe(args.symbol):

        bars.push_tick(tick)
        hb_age_s = feed.last_heartbeat_age_s()

        if not bars.minute_ready():
            continue

        bar, window = bars.finalize_bar()

        # compute features; pull last row (Polars-safe)
        feat_df = feats.compute(window)
        # ---- readiness guard for features ----
        required = ["donch_width", "atr_ratio", "slope", "pressure"]

        if feat_df.height == 0:
                # no features computed yet; keep accumulating
                continue

        last_row = feat_df.tail(1)
            # Check for nulls in the required columns
        null_mask = last_row.select(
                [pl.col(c).is_null().alias(c) for c in required]
            ).to_dicts()[0]

        if any(null_mask.values()):
                # Optional: lightweight diagnostic every N bars
            missing = [k for k, v in null_mask.items() if v]
                # print(f"[warmup] waiting for features: {missing}; bars_in_window={len(window)}")
            continue

        last = last_row.to_dicts()[0]

        f = type("F", (), dict(
                donch_width=float(last["donch_width"]),
                atr_ratio=float(last["atr_ratio"]),
                slope=float(last["slope"]),
                pressure=float(last["pressure"]),
            ))()


        # IV: NA for now (your IVcontext can replace this later)
        iv = ivctx.empty()

        now = bar.ts_close
        snap = sm.step(
            bar=bar,
            f=f,
            iv=iv,
            now=now,
            heartbeat_age_s=hb_age_s,
            is_expiry_day=False,
        )
        cdn_up_left, cdn_dn_left = sm._cooldown_left(now)

        render(now, bar, f, iv, snap, hb_age_s, cdn_up_left, cdn_dn_left)

        if args.persist:
            append_bar_csv(bars_path, bar)
            append_state_csv(
                state_path, now, bar, f, iv, snap,
                hb_age_s, cdn_up_left, cdn_dn_left
            )


if __name__ == "__main__":
    main()
