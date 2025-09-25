import time
import math
import pandas as pd
import numpy as np
import streamlit as st
from datetime import datetime, timedelta  # noqa: F401
from zoneinfo import ZoneInfo
from scipy.optimize import brentq
from scipy.stats import norm
from kiteconnect import KiteConnect

# -----------------------------
# Simple Black–Scholes helpers
# -----------------------------
def _d1(S, K, r, q, sigma, T):
    return (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * np.sqrt(T))

def _d2(d1, sigma, T):
    return d1 - sigma * np.sqrt(T)

def bs_price(S, K, r, q, sigma, T, right="C"):
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if right == "C" else (K - S))
    d1 = _d1(S, K, r, q, sigma, T)
    d2 = _d2(d1, sigma, T)
    if right == "C":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)

def implied_vol_from_price(price, S, K, r, q, T, right="C"):
    # No heroics: clamp to intrinsic first
    intrinsic = max(0.0, (S - K) if right == "C" else (K - S))
    if price <= intrinsic + 1e-8:
        return 0.0
    # Root find between [1e-6, 5.0] vol
    def f(s):
        return bs_price(S, K, r, q, s, T, right) - price
    try:
        return brentq(f, 1e-6, 5.0, maxiter=100, xtol=1e-6)
    except Exception:
        return np.nan

def bs_greeks(S, K, r, q, sigma, T, right="C"):
    if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
        return np.nan, np.nan, np.nan, np.nan
    d1 = _d1(S, K, r, q, sigma, T)
    d2 = _d2(d1, sigma, T)
    pdf = norm.pdf(d1)
    if right == "C":
        delta = math.exp(-q * T) * norm.cdf(d1)
        theta = (
            -S * math.exp(-q * T) * pdf * sigma / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * norm.cdf(d2)
            + q * S * math.exp(-q * T) * norm.cdf(d1)
        ) / 365.0
    else:
        delta = -math.exp(-q * T) * norm.cdf(-d1)
        theta = (
            -S * math.exp(-q * T) * pdf * sigma / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
            - q * S * math.exp(-q * T) * norm.cdf(-d1)
        ) / 365.0
    gamma = math.exp(-q * T) * pdf / (S * sigma * math.sqrt(T))
    vega = S * math.exp(-q * T) * pdf * math.sqrt(T) / 100.0  # per 1% vol
    return delta, gamma, vega, theta

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Kite Live Option Chain + Greeks", layout="wide")

st.title("Live Option Chain + Greeks (Kite)")
st.caption("Purely display. No orders. No surprises. Breathe.")

colA, colB = st.columns(2)
api_key = colA.text_input("Kite API Key", type="password")
access_token = colB.text_input("Kite Access Token", type="password")

underlying = st.text_input("Underlying symbol (cash/index, e.g., NIFTY, BANKNIFTY, RELIANCE)", "NIFTY")
risk_free_pct = st.number_input("Risk-free rate (%)", value=7.0, step=0.1)
div_yield_pct = st.number_input("Dividend yield (%)", value=0.0, step=0.1)
strike_step = st.number_input("Strike step (₹)", value=50, step=50, help="Typical: NIFTY 50, BANKNIFTY 100")
window = st.slider("Strikes around ATM (± count)", min_value=5, max_value=50, value=15)
refresh_secs = st.slider("Refresh interval (seconds)", min_value=1, max_value=10, value=2)

if not api_key or not access_token:
    st.info("Enter API key and Access Token to start.")
    st.stop()

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)

# -----------------------------
# Load instruments once
# -----------------------------
@st.cache_data(show_spinner=True, ttl=300)
def load_option_instruments():
    df = pd.DataFrame(kite.instruments())
    opt = df[(df["segment"] == "NFO-OPT") & (df["exchange"] == "NFO")].copy()
    opt["expiry"] = pd.to_datetime(opt["expiry"], utc=True).dt.tz_convert("Asia/Kolkata")
    opt = opt[["instrument_token", "tradingsymbol", "name", "expiry", "strike", "instrument_type", "tick_size"]]
    return opt

try:
    opt_df = load_option_instruments()
except Exception as e:
    st.error(f"Failed to load instruments: {e}")
    st.stop()

u_df = opt_df[opt_df["name"] == underlying.upper()].copy()
if u_df.empty:
    st.error("No options found for that underlying. Check the symbol.")
    st.stop()

# expiry picker
expiries = sorted(u_df["expiry"].unique())
expiry = st.selectbox("Expiry", expiries, index=0, format_func=lambda x: pd.Timestamp(x).strftime("%d %b %Y"))

# -----------------------------
# Helper: time to expiry in years
# Assume expiry at 15:30 IST on the selected date.
# -----------------------------
def time_to_expiry_yrs(exp_ts_ist: pd.Timestamp):
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(tz=ist)
    exp_close = exp_ts_ist.astimezone(ist).replace(hour=15, minute=30, second=0, microsecond=0)
    if now > exp_close:
        return 1e-6
    return max((exp_close - now).total_seconds(), 1) / (365.0 * 24 * 3600)

# -----------------------------
# Pick strikes around ATM
# -----------------------------
# Grab live spot for the underlying from NSE
try:
    q = kite.quote([f"NSE:{underlying.upper()}"])
    spot = q[f"NSE:{underlying.upper()}"]["last_price"]
except Exception as e:
    st.error(f"Failed to fetch underlying price: {e}")
    st.stop()

# ATM estimate
try:
    step = int(strike_step)
    atm = int(round(spot / step) * step)
except Exception:
    atm = int(round(spot / 50) * 50)

lo, hi = atm - window * strike_step, atm + window * strike_step
view = u_df[(u_df["expiry"] == expiry) & (u_df["strike"].between(lo, hi))].copy()

# -----------------------------
# Build tradingsymbol list and refresh loop
# -----------------------------
tokens = list(view["instrument_token"])
symbol_map = {row["instrument_token"]: row for _, row in view.iterrows()}
tradings = [f"NFO:{row['tradingsymbol']}" for _, row in view.iterrows()]

# UI holders
top = st.container()
grid_placeholder = st.empty()
note = st.empty()

# Compute constants
r = risk_free_pct / 100.0
qdiv = div_yield_pct / 100.0
T_years = time_to_expiry_yrs(pd.Timestamp(expiry))

# polite warning
st.caption("Tip: this uses REST quotes on a timer. Keep strike range sane to avoid rate limits.")

# crude live loop: 300 cycles is plenty; re-run to continue
cycles = 300
for i in range(cycles):
    try:
        # Batch quote
        quotes = kite.quote(tradings)
    except Exception as e:
        note.warning(f"Quote fetch error: {e}")
        time.sleep(refresh_secs)
        continue

    rows = []
    for inst_token, row in symbol_map.items():
        tsym = row["tradingsymbol"]
        key = f"NFO:{tsym}"
        if key not in quotes:
            continue
        qd = quotes[key]
        K = float(row["strike"])
        right = "C" if row["instrument_type"] == "CE" else "P"

        ltp = qd.get("last_price")
        # Prefer mid if depth is sane
        bid = None
        ask = None
        try:
            bid = qd["depth"]["buy"][0]["price"]
            ask = qd["depth"]["sell"][0]["price"]
        except Exception:
            pass
        price = None
        if bid and ask and bid > 0 and ask > 0 and ask >= bid:
            price = (bid + ask) / 2.0
        else:
            price = ltp

        if not price or price <= 0:
            continue

        iv = implied_vol_from_price(price, spot, K, r, qdiv, T_years, right)
        dlt, gmm, vga, tht = bs_greeks(spot, K, r, qdiv, iv if iv == iv else 0.0, T_years, right)

        rows.append({
            "Strike": int(K),
            "Type": row["instrument_type"],
            "LTP": round(ltp, 2) if ltp else None,
            "Mid": round(price, 2) if price else None,
            "IV (%)": round(iv * 100, 2) if iv == iv else None,
            "Delta": round(dlt, 4) if dlt == dlt else None,
            "Gamma": round(gmm, 6) if gmm == gmm else None,
            "Vega": round(vga, 4) if vga == vga else None,
            "Theta (per day)": round(tht, 4) if tht == tht else None,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        # pretty grid: CE left, PE right
        pivot = df.pivot_table(index="Strike", columns="Type",
                               values=["LTP", "Mid", "IV (%)", "Delta", "Gamma", "Vega", "Theta (per day)"])
        pivot = pivot.sort_index().fillna("")
        with grid_placeholder.container():
            st.subheader(f"{underlying.upper()}  |  Spot: ₹{spot:.2f}  |  Expiry: {pd.Timestamp(expiry).strftime('%d %b %Y')}")
            st.dataframe(pivot, use_container_width=True, height=700)
    else:
        grid_placeholder.info("No rows to display yet. Try widening your strike window a bit.")

    note.caption(f"Last update: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%H:%M:%S IST')} • Auto refresh every {refresh_secs}s • Loop {i+1}/{cycles}")
    time.sleep(refresh_secs)
