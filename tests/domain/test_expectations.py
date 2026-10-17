"""DOC007 expected runs: windows in the HA time zone and the three outcomes (8.1)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.ha_sentinel.core.models import ObservationKind
from custom_components.ha_sentinel.modules.doctor.engine import DoctorEngine
from custom_components.ha_sentinel.modules.doctor.expectations import Expectation, next_window
from custom_components.ha_sentinel.modules.doctor.overrides import RuleOverride

from .helpers import heartbeat

S = 1_000_000
TZ = "Europe/Warsaw"
WEEKDAYS = 0b0011111
UID = "auto-uid"


def us(*args) -> int:
    return int(datetime(*args, tzinfo=ZoneInfo(TZ)).timestamp() * S)


def test_windows_follow_weekdays_and_dst():
    exp = Expectation(WEEKDAYS, 7 * 60, 15)
    # Friday 2026-10-23 08:00 -> next weekday window is Monday 26th (after the DST change).
    start, end = next_window(exp, us(2026, 10, 23, 8, 0), TZ)
    assert (start, end) == (us(2026, 10, 26, 6, 45), us(2026, 10, 26, 7, 15))
    start, _ = next_window(exp, us(2026, 10, 23, 7, 0), TZ)
    assert start == us(2026, 10, 23, 6, 45)  # current window still open


def _engine(enabled=True, covered=True):
    e = DoctorEngine(boot_started_us=us(2026, 10, 19, 0, 0), tz_name=TZ,
                     automation_enabled=lambda _uid: enabled,
                     coverage_complete=lambda _f, _t: covered)
    e.set_overrides([RuleOverride("o", "DOC007", UID, True,
                                  {"days_mask": WEEKDAYS, "minute": 420,
                                   "tolerance_min": 15})], us(2026, 10, 19, 1, 0))
    return e


def _run(at_us: int):
    obs = heartbeat(UID, at_us)
    object.__setattr__(obs, "kind", ObservationKind.AUTOMATION_RUN)
    return obs


def test_missed_run_raises_and_next_run_resolves():
    e = _engine()
    out = e.tick(us(2026, 10, 19, 7, 15))
    assert [f.rule_id for f in out] == ["DOC007"]
    assert out[0].details["window_end"].endswith("Z")
    closed = e.process(_run(us(2026, 10, 20, 7, 2)))
    assert [(f.rule_id, str(f.status)) for f in closed] == [("DOC007", "resolved")]
    assert e.tick(us(2026, 10, 20, 7, 15)) == []  # ran inside the window


def test_unknown_when_off_or_not_covered_never_missed():
    for e in (_engine(enabled=None), _engine(enabled=False), _engine(covered=False)):
        assert e.tick(us(2026, 10, 19, 7, 15)) == []
        assert e.expect_unknown == {UID}


def test_removing_the_calendar_closes_the_finding():
    e = _engine()
    e.tick(us(2026, 10, 19, 7, 15))
    closed = e.set_overrides([], us(2026, 10, 19, 8, 0))
    assert [f.details["resolution"] for f in closed] == ["contract_removed"]
    assert e.tick(us(2026, 10, 27, 0, 0)) == []
