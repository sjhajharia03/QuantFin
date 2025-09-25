from datetime import datetime, time
import zoneinfo

class SessionClock:
    def __init__(self, tz: str, open_str: str, close_str: str,
                 open_embargo_min: int, close_embargo_min: int,
                 expiry_afternoon_strict_after: str):
        self.tz = zoneinfo.ZoneInfo(tz)
        h1,m1 = map(int, open_str.split(":"))
        h2,m2 = map(int, close_str.split(":"))
        self.open_t = time(h1, m1)
        self.close_t = time(h2, m2)
        self.open_embargo_min = open_embargo_min
        self.close_embargo_min = close_embargo_min
        h3,m3 = map(int, expiry_afternoon_strict_after.split(":"))
        self.expiry_strict_t = time(h3, m3)

    def in_open_embargo(self, now: datetime) -> bool:
        now = now.astimezone(self.tz)
        start = datetime.combine(now.date(), self.open_t, self.tz)
        return start <= now < start.replace(minute=self.open_t.minute + self.open_embargo_min)

    def in_close_embargo(self, now: datetime) -> bool:
        now = now.astimezone(self.tz)
        end = datetime.combine(now.date(), self.close_t, self.tz)
        from_min = self.close_embargo_min
        return end.replace(minute=self.close_t.minute - from_min) <= now <= end

    def is_expiry_afternoon(self, now: datetime, is_expiry_day: bool) -> bool:
        if not is_expiry_day:
            return False
        now = now.astimezone(self.tz)
        return now.time() >= self.expiry_strict_t
