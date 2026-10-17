"""DOC007 missed expectation: the admin's calendar for an automation (spec 2.3, 8.1).

Pure logic, no HA imports. An automation that did not run is not a failure by itself;
only an explicit expectation ("weekdays around 07:00, +/- 15 min") makes a missing run
a finding. last_triggered proves an observed start, not a successful run. Windows are
computed in the HA time zone, so they follow DST changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

S = 1_000_000


@dataclass(frozen=True, slots=True)
class Expectation:
    days_mask: int  # bit 0 = Monday ... bit 6 = Sunday
    minute: int  # minute of the local day
    tolerance_min: int

    @classmethod
    def from_params(cls, params: dict[str, int]) -> Expectation:
        return cls(params["days_mask"], params["minute"], params["tolerance_min"])


def _window_on(day: datetime, exp: Expectation, tz: ZoneInfo) -> tuple[int, int]:
    local = datetime(day.year, day.month, day.day, exp.minute // 60, exp.minute % 60,
                     tzinfo=tz)
    tol = timedelta(minutes=exp.tolerance_min)
    return (int((local - tol).timestamp() * S), int((local + tol).timestamp() * S))


def next_window(exp: Expectation, after_us: int, tz_name: str) -> tuple[int, int]:
    """First window whose end lies after `after_us`."""
    tz = ZoneInfo(tz_name)
    day = datetime.fromtimestamp(after_us / S, tz=UTC).astimezone(tz) - timedelta(days=1)
    for _ in range(9):
        if exp.days_mask >> day.weekday() & 1:
            start, end = _window_on(day, exp, tz)
            if end > after_us:
                return start, end
        day += timedelta(days=1)
    raise ValueError("days_mask selects no day")
