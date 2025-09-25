# momentum/feed_broker_kite.py
import time
from datetime import datetime, timezone
from typing import Iterator, List, Optional

from .core_contracts import Tick

class KiteFeed:
    """
    Minimal Zerodha Kite adapter.
    You pass api_key and access_token (copied daily from Kite console flow).
    It resolves an instrument token for NIFTY index or nearest FUT, opens a websocket,
    and yields Tick(ts, last) for your app loop.
    """

    def __init__(self, api_key: str, access_token: str, *args, symbol=None, instrument_kind: str = "index", **kwargs):
        # preserve existing parameters and add compatibility for `symbol` and `instrument_kind`
        self.api_key = api_key.strip()
        self.access_token = access_token.strip()
        self.symbol = symbol or kwargs.get("symbol")
        self.instrument_kind = instrument_kind or kwargs.get("instrument_kind")

        # REST client for instrument lookup
        from kiteconnect import KiteTicker, KiteConnect  # lazy import to keep deps optional
        self.KiteTicker = KiteTicker
        self.KiteConnect = KiteConnect

        self.kite = self.KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)

        self._ticker = None
        self._queue: List[Tick] = []
        self._last_ts: Optional[datetime] = None
        self._connected = False
        self._tokens: List[int] = []

    # ---------- public API expected by app.py ----------
    def connect(self, symbol: str = "NIFTY"):
        token = self._resolve_token(symbol, self.instrument_kind)
        self._tokens = [token]

        self._ticker = self.KiteTicker(self.api_key, self.access_token)

        def on_ticks(ws, ticks):
            now = datetime.now(timezone.utc)
            self._last_ts = now
            for t in ticks or []:
                ltp = t.get("last_price")
                if ltp is not None:
                    self._queue.append(Tick(ts=now, last=float(ltp)))

        def on_connect(ws, response):
            self._connected = True
            ws.subscribe(self._tokens)
            ws.set_mode(ws.MODE_LTP, self._tokens)

        def on_error(ws, code, reason):
            # keep it simple; you can add logging if you like suffering
            self._connected = False

        def on_close(ws, code, reason):
            self._connected = False

        self._ticker.on_ticks = on_ticks
        self._ticker.on_connect = on_connect
        self._ticker.on_error = on_error
        self._ticker.on_close = on_close

        # threaded connect so your loop continues
        self._ticker.connect(threaded=True, disable_ssl_verification=False)

        # wait up to ~5s for the socket to report connected
        for _ in range(50):
            if self._connected:
                return
            time.sleep(0.1)
        raise RuntimeError("KiteTicker failed to connect (check token/plan)")

    def subscribe(self, symbol: str) -> Iterator[Tick]:
        while True:
            if self._queue:
                yield self._queue.pop(0)
            else:
                time.sleep(0.02)

    def last_heartbeat_age_s(self) -> float:
        if self._last_ts is None:
            return 999.0
        return max(0.0, (datetime.now(timezone.utc) - self._last_ts).total_seconds())

    # ---------- internals ----------
    def _resolve_token(self, symbol: str, kind: str) -> int:
        """
        Resolve an instrument token for:
          - kind == "index": NSE index LTP for NIFTY
          - kind == "fut": nearest NIFTY futures in NFO
        No hardcoding. We ask the instruments dump each run.
        """
        if kind == "index":
            # Try to find the index in NSE instruments
            for ins in self.kite.instruments("NSE"):
                if (ins.get("instrument_type") == "INDEX"
                    and ins.get("tradingsymbol", "").startswith("NIFTY")):
                    return ins["instrument_token"]
            # Fallback to futures if index not available for your account
            kind = "fut"

        # Futures path (NFO)
        futs = [
            ins for ins in self.kite.instruments("NFO")
            if ins.get("segment") == "NFO-FUT"
            and ins.get("tradingsymbol", "").startswith("NIFTY")
        ]
        if not futs:
            raise RuntimeError("Could not find NIFTY FUT in NFO instruments")
        # pick nearest expiry
        futs.sort(key=lambda x: x["expiry"])
        return futs[0]["instrument_token"]
