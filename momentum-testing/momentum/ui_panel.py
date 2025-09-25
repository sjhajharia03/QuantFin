# ui_panel.py
from datetime import datetime

def fmt_pct(x):
    return "NA" if x is None else f"{x*100:.2f}%"

def fmt_bps(x):
    return "NA" if x is None else f"{x:+.0f}bps"

def fmt_iv(iv):
    if iv is None: 
        return "NA"
    if iv.quality == "NA": 
        return "IV:NA"
    if iv.quality == "STALE": 
        return "IV:STALE"
    p = "NA" if iv.percentile is None else f"{iv.percentile:.0f}%"
    return f"IV:{p}"

def render(now: datetime, bar, feats, iv_ctx, snap, hb_age_s, cdn_up_left, cdn_dn_left):
    # bar: Bar, feats: Features, iv_ctx: IvContext, snap: StateSnapshot
    dist_up_bps = (bar.close / max(bar.hh20, 1e-12) - 1) * 10000
    dist_dn_bps = (1 - bar.close / max(bar.ll20, 1e-12)) * 10000
    # choose the nearer side for display
    dist_bps = dist_up_bps if abs(dist_up_bps) > abs(dist_dn_bps) else dist_dn_bps

    print(
        f"{now.strftime('%H:%M')}  "
        f"{bar.close:8.1f}  "
        f"{snap.state:10s}  {snap.reason:14s}  "
        f"{fmt_bps(dist_bps):>7}  "
        f"donch {fmt_pct(feats.donch_width):>7}  "
        f"atrR {feats.atr_ratio:4.2f}  "
        f"slope {feats.slope:+.4f}  "
        f"press {feats.pressure:+.4f}  "
        f"{fmt_iv(iv_ctx):>8}  "
        f"HB {hb_age_s:>4.1f}s  "
        f"CD UP {cdn_up_left:>2}m / DN {cdn_dn_left:>2}m"
    )
