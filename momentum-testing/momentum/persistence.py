# persistence.py
import csv
from pathlib import Path
from datetime import datetime

def _ensure_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def append_bar_csv(path: str, bar):
    p = Path(path)
    _ensure_dir(p)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts_close","open","high","low","close","volume","tr","atr20","hh20","ll20"])
        w.writerow([bar.ts_close.isoformat(), bar.open, bar.high, bar.low, bar.close,
                    bar.volume, bar.tr, bar.atr20, bar.hh20, bar.ll20])

def append_state_csv(path: str, now: datetime, bar, feats, iv, snap, hb_age_s, cdn_up_left, cdn_dn_left):
    p = Path(path)
    _ensure_dir(p)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow([
                "ts","state","reason","dir","hb_age_s",
                "close","dist_up_bps","dist_dn_bps",
                "donch_width","atr_ratio","slope","pressure",
                "iv_pct","iv_quality","cdn_up_left","cdn_dn_left"
            ])
        dist_up_bps = (bar.close / max(bar.hh20, 1e-12) - 1) * 10000
        dist_dn_bps = (1 - bar.close / max(bar.ll20, 1e-12)) * 10000
        iv_pct = "" if (iv is None or iv.percentile is None) else f"{iv.percentile:.1f}"
        iv_q = "" if iv is None else iv.quality
        w.writerow([
            now.isoformat(), snap.state, snap.reason, snap.direction, f"{hb_age_s:.2f}",
            bar.close, f"{dist_up_bps:.1f}", f"{dist_dn_bps:.1f}",
            f"{feats.donch_width:.6f}", f"{feats.atr_ratio:.4f}",
            f"{feats.slope:.6f}", f"{feats.pressure:.6f}",
            iv_pct, iv_q, cdn_up_left, cdn_dn_left
        ])
