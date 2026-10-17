"""Doctor: rules DOC001-DOC005 and DOC009 (spec 8). Pure logic, no HA imports.

The engine consumes observations (`process`) and wakes up on deadlines (`tick`). It
returns findings whose status changed to ACTIVE or RESOLVED; PENDING lives only in
memory. Deadlines sit in a heap invalidated by tokens, so the entity list is never
scanned.
"""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ...const import (
    DOC001_RECOVERY_STABLE_S,
    DOC001_UNAVAILABLE_S,
    DOC002_UNKNOWN_S,
    DOC003_DEBOUNCE_S,
    DOC003_FLAP_THRESHOLD,
    DOC003_FLAP_WINDOW_S,
    DOC004_CLEAR_PCT,
    DOC004_CONFIRM_S,
    DOC004_LOW_PCT,
    STARTUP_GRACE_S,
)
from ...core.models import (
    Availability,
    CoverageStatus,
    Finding,
    FindingStatus,
    Observation,
    ObservationKind,
    Severity,
    new_uuid,
)
from .expectations import Expectation, next_window
from .numeric import SIGNAL_HYSTERESIS
from .overrides import RuleOverride, expectations, heartbeat_contracts

S = 1_000_000  # microseconds per second

CoverageFn = Callable[[int, int], bool]
"""(from_us, to_us) -> True if the source was observed continuously in that range."""


@dataclass(slots=True)
class DoctorParams:
    unavailable_s: int = DOC001_UNAVAILABLE_S
    recovery_stable_s: int = DOC001_RECOVERY_STABLE_S
    unknown_s: int = DOC002_UNKNOWN_S
    startup_grace_s: int = STARTUP_GRACE_S
    flap_window_s: int = DOC003_FLAP_WINDOW_S
    flap_threshold: int = DOC003_FLAP_THRESHOLD
    flap_debounce_s: int = DOC003_DEBOUNCE_S
    battery_low_pct: float = DOC004_LOW_PCT
    battery_clear_pct: float = DOC004_CLEAR_PCT
    battery_confirm_s: int = DOC004_CONFIRM_S
    # DOC005: explicit heartbeat contract only, subject_uid -> (period_s, manual_threshold_s)
    heartbeats: dict[str, tuple[int, int]] = field(default_factory=dict)


RULE_SEVERITY = {
    "DOC001": Severity.HIGH,
    "DOC002": Severity.WARNING,
    "DOC003": Severity.WARNING,
    "DOC004": Severity.WARNING,
    "DOC005": Severity.WARNING,
    "DOC007": Severity.WARNING,
    "DOC008": Severity.INFO,  # advisory: a weak link is a risk, not a failure
    "DOC009": Severity.INFO,  # information, never an automatic upgrade
}
MAINTENANCE_RECHECK_S = 60
# Domains whose normal state is "unknown" (no value until used): never DOC002.
STATELESS_DOMAINS = frozenset({"button", "input_button", "scene", "event", "conversation",
                               "tts", "stt", "wake_word", "notify", "ai_task", "image"})
RULE_VERSION = 1
# Rules evaluated by this engine; other modules own DOC006, DOC010 and NET001.
DOCTOR_RULES = frozenset(RULE_SEVERITY)
# DOC002 v2: only live transitions to "unknown", never stateless domains.
RULE_VERSIONS = {"DOC002": 2}


@dataclass(slots=True)
class _EntityState:
    availability: Availability | None = None
    since_us: int = 0
    since_obs_id: str | None = None
    token: int = 0  # invalidates scheduled deadlines on every state change
    flap_entries: deque[int] = field(default_factory=deque)
    flap_token: int = 0
    battery_low_since_us: int | None = None
    battery_low_obs_id: str | None = None
    signal_low_since_us: int | None = None
    signal_low_obs_id: str | None = None
    heartbeat_token: int = 0


class DoctorEngine:
    def __init__(
        self,
        *,
        boot_started_us: int,
        params: DoctorParams | None = None,
        coverage_complete: CoverageFn | None = None,
        in_maintenance: Callable[[str], bool] | None = None,
        domain_of: Callable[[str], str | None] | None = None,
        tz_name: str = "UTC",
        automation_enabled: Callable[[str], bool | None] | None = None,
    ) -> None:
        self.params = params or DoctorParams()
        self._grace_end_us = boot_started_us + self.params.startup_grace_s * S
        self._coverage_complete = coverage_complete or (lambda _f, _t: True)
        # Planned maintenance suppresses new failures of its subjects (spec 8.2).
        self._in_maintenance = in_maintenance or (lambda _uid: False)
        self._domain_of = domain_of or (lambda _uid: None)
        self._entities: dict[str, _EntityState] = {}
        self._findings: dict[str, Finding] = {}  # fingerprint -> pending/active
        self._heap: list[tuple[int, int, str, str, int]] = []
        self._overrides: dict[tuple[str, str], RuleOverride] = {}
        self._heartbeats: dict[str, tuple[int, int]] = dict(self.params.heartbeats)
        # DOC007: expectation, current window and last observed run per automation.
        self._tz_name = tz_name
        self._automation_enabled = automation_enabled or (lambda _uid: None)
        self._expect: dict[str, Expectation] = {}
        self._windows: dict[str, tuple[int, int, int]] = {}  # uid -> (start, end, token)
        self._last_run: dict[str, int] = {}
        self.expect_unknown: set[str] = set()  # last window could not be judged
        self._seq = itertools.count()
        self.flap_partial: set[str] = set()  # subjects whose DOC003 window is incomplete
        for uid in self._heartbeats:
            self._schedule_heartbeat(uid, boot_started_us)

    # --- Public API -------------------------------------------------------

    def restore(self, findings: list[Finding]) -> None:
        """Load active findings after a restart so they are not re-announced."""
        for f in findings:
            if f.status is FindingStatus.ACTIVE and f.rule_id in DOCTOR_RULES:
                self._findings[f.fingerprint] = f

    def active_findings(self) -> list[Finding]:
        return [f for f in self._findings.values() if f.status is FindingStatus.ACTIVE]

    def retire_stale(self, now_us: int) -> list[Finding]:
        """Close restored findings that can no longer be valid in this boot.

        - the subject does not exist any more: entities without a registry entry get a
          new identity every boot (spec 6.1), so their findings would stay open forever;
        - the finding was raised by an older version of its rule;
        - DOC002 on a stateless domain.
        They are closed with an explicit reason, never presented as a recovery.
        """
        out = []
        for fp, f in list(self._findings.items()):
            if f.status is not FindingStatus.ACTIVE:
                continue
            if f.rule_id in ("DOC001", "DOC002", "DOC003", "DOC004", "DOC008", "DOC009") \
                    and f.subject_uid not in self._entities:
                reason = "subject_identity_ended"
            elif f.rule_version < RULE_VERSIONS.get(f.rule_id, RULE_VERSION) or (
                    f.rule_id == "DOC002"
                    and self._domain_of(f.subject_uid) in STATELESS_DOMAINS):
                reason = "rule_revised"
            else:
                continue
            del self._findings[fp]
            f.status = FindingStatus.RESOLVED
            f.resolved_at_us = f.last_seen_us = now_us
            f.details = {**f.details, "resolution": reason}
            out.append(f)
        return out

    def set_overrides(self, overrides: list[RuleOverride], now_us: int) -> list[Finding]:
        """Replace per-object overrides; returns findings closed by the change.

        A disabled rule closes its active finding ("rule_disabled"); a removed heartbeat
        contract closes DOC005. Timers already running keep their old threshold.
        """
        self._overrides = {o.key: o for o in overrides}
        contracts = {**self.params.heartbeats, **heartbeat_contracts(self._overrides)}
        out: list[Finding] = []
        for uid in set(self._heartbeats) - set(contracts):
            if (st := self._entities.get(uid)) is not None:
                st.heartbeat_token += 1
            out += self._resolve("DOC005", uid, now_us, reason="contract_removed")
        added = [uid for uid, c in contracts.items() if self._heartbeats.get(uid) != c]
        self._heartbeats = contracts
        for uid in added:
            self._schedule_heartbeat(uid, now_us)
        wanted = {uid: Expectation.from_params(p)
                  for uid, p in expectations(self._overrides).items()}
        for uid in set(self._expect) - set(wanted):
            self._windows.pop(uid, None)
            out += self._resolve("DOC007", uid, now_us, reason="contract_removed")
        changed = [uid for uid, e in wanted.items() if self._expect.get(uid) != e]
        self._expect = wanted
        for uid in changed:
            self._schedule_expectation(uid, now_us)
        for (rule, uid), o in self._overrides.items():
            if not o.enabled:
                out += self._resolve(rule, uid, now_us, reason="rule_disabled")
        return out

    @property
    def expectation_subjects(self) -> frozenset[str]:
        return frozenset(self._expect)

    def close_all(self, now_us: int, reason: str) -> list[Finding]:
        """Close every active finding and forget pending timers (module switched off)."""
        out = []
        for fp, f in list(self._findings.items()):
            del self._findings[fp]
            if f.status is FindingStatus.ACTIVE:
                f.status = FindingStatus.RESOLVED
                f.resolved_at_us = f.last_seen_us = now_us
                f.details = {**f.details, "resolution": reason}
                out.append(f)
        self._heap.clear()
        return out

    @property
    def heartbeat_subjects(self) -> frozenset[str]:
        return frozenset(self._heartbeats)

    def _param(self, rule: str, uid: str, key: str, default: float) -> float:
        o = self._overrides.get((rule, uid))
        return o.params.get(key, default) if o is not None else default

    def _enabled(self, rule: str, uid: str) -> bool:
        o = self._overrides.get((rule, uid))
        return o is None or o.enabled

    def availability_snapshot(self) -> dict[str, Availability]:
        """Current availability of every subject seen in this boot (for correlation)."""
        return {uid: st.availability for uid, st in self._entities.items()
                if st.availability is not None}

    def next_deadline_us(self) -> int | None:
        return self._heap[0][0] if self._heap else None

    def process(self, obs: Observation) -> list[Finding]:
        if obs.subject_uid is None:
            return []
        if obs.kind is ObservationKind.NUMERIC_SAMPLE:
            return self._on_numeric(obs)
        if obs.kind is ObservationKind.UPDATE_STATE:
            return self._on_update(obs)
        if obs.kind is ObservationKind.AUTOMATION_RUN:
            return self._automation_ran(obs)
        if obs.kind is ObservationKind.HEARTBEAT:
            if obs.subject_uid in self._heartbeats:
                return self._heartbeat_seen(obs)
            return []
        if obs.kind in (ObservationKind.AVAILABILITY_CHANGED, ObservationKind.STATE_SNAPSHOT):
            out = self._on_availability(obs)
            raw = obs.payload.get("availability" if obs.kind is ObservationKind.STATE_SNAPSHOT
                                  else "to")
            # An entity that turns unavailable has not reported: no heartbeat.
            if obs.subject_uid in self._heartbeats and raw == Availability.AVAILABLE:
                out += self._heartbeat_seen(obs)
            return out
        return []

    def tick(self, now_us: int) -> list[Finding]:
        out: list[Finding] = []
        while self._heap and self._heap[0][0] <= now_us:
            deadline, _, uid, action, token = heapq.heappop(self._heap)
            st = self._entities.get(uid)
            if action == "expect":
                out += self._check_expectation(uid, token)
                continue
            if action == "heartbeat":
                if uid not in self._heartbeats or (st is not None
                                                   and st.heartbeat_token != token):
                    continue
                out += self._raise("DOC005", uid, deadline, None, CoverageStatus.COMPLETE)
                continue
            if st is None or (action != "flap_reeval" and st.token != token):
                continue
            if action in ("mature_unavailable", "mature_unknown") and self._in_maintenance(uid):
                # Re-check after the window instead of raising during maintenance.
                self._push(deadline + MAINTENANCE_RECHECK_S * S, uid, action, token)
                continue
            if action == "mature_unavailable":
                cov = self._episode_coverage(st)
                out += self._raise("DOC001", uid, st.since_us, st.since_obs_id, cov)
            elif action == "mature_unknown":
                cov = self._episode_coverage(st)
                out += self._raise("DOC002", uid, st.since_us, st.since_obs_id, cov)
            elif action == "recover":
                out += self._resolve("DOC001", uid, deadline)
                out += self._resolve("DOC002", uid, deadline)
            elif action == "flap_reeval":
                if st.flap_token == token:
                    out += self._evaluate_flapping(uid, st, deadline)
        return out

    # --- Availability: DOC001, DOC002, DOC003 -------------------------------------

    def _on_availability(self, obs: Observation) -> list[Finding]:
        uid = obs.subject_uid
        assert uid is not None
        is_snapshot = obs.kind is ObservationKind.STATE_SNAPSHOT
        raw = obs.payload.get("availability" if is_snapshot else "to")
        try:
            new = Availability(raw)
        except ValueError:
            return []
        st = self._entities.setdefault(uid, _EntityState())
        if st.availability is new:
            return []  # transport duplicate or no transition - counters stay unchanged
        now = obs.received_at_us
        prev = st.availability
        st.availability = new
        st.since_us = obs.occurred_at_us or now
        st.since_obs_id = None if is_snapshot else obs.id
        st.token += 1
        out: list[Finding] = []

        for rule in ("DOC001", "DOC002"):
            f = self._findings.get(f"{rule}:{uid}")
            if f is not None and f.status is FindingStatus.PENDING:
                del self._findings[f.fingerprint]

        if new is Availability.UNAVAILABLE:
            limit = self._param("DOC001", uid, "unavailable_s", self.params.unavailable_s)
            self._push(max(st.since_us + int(limit * S), self._grace_end_us),
                       uid, "mature_unavailable", st.token)
            if not is_snapshot and prev is not None:
                out += self._record_flap_entry(uid, st, st.since_us)
        elif new is Availability.UNKNOWN:
            # Only a change from a known state counts: an entity unknown since the
            # baseline (never set, stateless) is not evidence that something stopped.
            if prev is not None and not is_snapshot \
                    and self._domain_of(uid) not in STATELESS_DOMAINS:
                limit = self._param("DOC002", uid, "unknown_s", self.params.unknown_s)
                self._push(max(st.since_us + int(limit * S), self._grace_end_us),
                           uid, "mature_unknown", st.token)
        else:
            if self._has_active(uid, ("DOC001", "DOC002")):
                self._push(
                    max(st.since_us + self.params.recovery_stable_s * S, self._grace_end_us),
                    uid, "recover", st.token,
                )
        return out

    def _episode_coverage(self, st: _EntityState) -> CoverageStatus:
        if st.since_obs_id is None:
            return CoverageStatus.PARTIAL  # known only from a snapshot - start time is uncertain
        return CoverageStatus.COMPLETE

    def _record_flap_entry(self, uid: str, st: _EntityState, at_us: int) -> list[Finding]:
        debounce = self.params.flap_debounce_s * S
        if st.flap_entries and at_us - st.flap_entries[-1] < debounce:
            return []
        st.flap_entries.append(at_us)
        return self._evaluate_flapping(uid, st, at_us)

    def _evaluate_flapping(self, uid: str, st: _EntityState, now_us: int) -> list[Finding]:
        window_s = self.params.flap_window_s * S
        window_start = now_us - window_s
        while st.flap_entries and st.flap_entries[0] <= window_start:  # window is (start, now]
            st.flap_entries.popleft()
        count = len(st.flap_entries)
        if st.flap_entries:
            st.flap_token += 1
            self._push(st.flap_entries[0] + window_s, uid, "flap_reeval", st.flap_token)
        fp = f"DOC003:{uid}"
        if count >= self._param("DOC003", uid, "flap_threshold", self.params.flap_threshold):
            if not self._coverage_complete(window_start, now_us):
                # Gap inside the window: result is partial, count is not reliable (AC DOC 02, T05).
                self.flap_partial.add(uid)
                return []
            self.flap_partial.discard(uid)
            return self._raise("DOC003", uid, st.flap_entries[0], None, CoverageStatus.COMPLETE,
                               details={"entries": count})
        self.flap_partial.discard(uid)
        if fp in self._findings:
            return self._resolve("DOC003", uid, now_us)
        return []

    # --- Battery: DOC004 ----------------------------------------------------------

    def _on_numeric(self, obs: Observation) -> list[Finding]:
        p = obs.payload
        if p.get("purpose") == "signal":
            return self._on_signal(obs)
        if p.get("device_class") != "battery" or p.get("unit") != "%":
            return []  # never detect batteries by entity name
        value = p.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return []
        uid = obs.subject_uid
        assert uid is not None
        st = self._entities.setdefault(uid, _EntityState())
        now = obs.occurred_at_us or obs.received_at_us
        active = self._has_active(uid, ("DOC004",))
        if active:
            if value > self._param("DOC004", uid, "clear_pct", self.params.battery_clear_pct):
                st.battery_low_since_us = None
                return self._resolve("DOC004", uid, now)
            return []
        if value < self._param("DOC004", uid, "low_pct", self.params.battery_low_pct):
            if st.battery_low_since_us is None:
                st.battery_low_since_us = now
                st.battery_low_obs_id = obs.id
                return []
            if now - st.battery_low_since_us >= self.params.battery_confirm_s * S:
                return self._raise("DOC004", uid, st.battery_low_since_us, obs.id,
                                   CoverageStatus.COMPLETE, details={"value": value},
                                   extra_evidence=st.battery_low_obs_id)
            return []
        st.battery_low_since_us = None
        return []

    # --- Signal: DOC008 ------------------------------------------------------------

    def _on_signal(self, obs: Observation) -> list[Finding]:
        """Weak signal only against the source's own scale and threshold (spec 8.1)."""
        p = obs.payload
        value, threshold, scale = p.get("value"), p.get("threshold"), p.get("scale")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   for x in (value, threshold)) or not isinstance(scale, list):
            return []
        uid = obs.subject_uid
        assert uid is not None
        st = self._entities.setdefault(uid, _EntityState())
        now = obs.occurred_at_us or obs.received_at_us
        if self._has_active(uid, ("DOC008",)):
            if value >= threshold + SIGNAL_HYSTERESIS * (scale[1] - scale[0]):
                st.signal_low_since_us = None
                return self._resolve("DOC008", uid, now)
            return []
        if value < threshold:
            if st.signal_low_since_us is None:
                st.signal_low_since_us, st.signal_low_obs_id = now, obs.id
                return []
            if now - st.signal_low_since_us >= self.params.battery_confirm_s * S:
                return self._raise("DOC008", uid, st.signal_low_since_us, obs.id,
                                   CoverageStatus.COMPLETE,
                                   details={"value": value, "threshold": threshold},
                                   extra_evidence=st.signal_low_obs_id)
            return []
        st.signal_low_since_us = None
        return []

    def close(self, rule: str, uid: str, now_us: int, reason: str) -> list[Finding]:
        """Administrative close, e.g. the numeric source behind the finding was removed."""
        return self._resolve(rule, uid, now_us, reason=reason)

    # --- Updates: DOC009 ----------------------------------------------------------------

    def _on_update(self, obs: Observation) -> list[Finding]:
        uid = obs.subject_uid
        assert uid is not None
        if obs.payload.get("available") is True:
            return self._raise("DOC009", uid, obs.occurred_at_us or obs.received_at_us, obs.id,
                               CoverageStatus.COMPLETE,
                               details={"latest_version": obs.payload.get("latest_version")})
        return self._resolve("DOC009", uid, obs.received_at_us)

    # --- Expected runs: DOC007 ---------------------------------------------------------

    def _schedule_expectation(self, uid: str, after_us: int) -> None:
        start, end = next_window(self._expect[uid], after_us, self._tz_name)
        token = next(self._seq)
        self._windows[uid] = (start, end, token)
        self._push(end, uid, "expect", token)

    def _check_expectation(self, uid: str, token: int) -> list[Finding]:
        window = self._windows.get(uid)
        if uid not in self._expect or window is None or window[2] != token:
            return []
        start, end, _ = window
        out: list[Finding] = []
        ran = (last := self._last_run.get(uid)) is not None and start <= last <= end
        if ran:
            self.expect_unknown.discard(uid)
        elif self._automation_enabled(uid) is not True or not self._coverage_complete(start, end):
            # Switched off, unavailable or a monitoring gap: unknown, never "missed".
            self.expect_unknown.add(uid)
        else:
            self.expect_unknown.discard(uid)
            out += self._raise("DOC007", uid, end, None, CoverageStatus.COMPLETE, details={
                "window_start": _iso(start), "window_end": _iso(end)})
        self._schedule_expectation(uid, end)
        return out

    def _automation_ran(self, obs: Observation) -> list[Finding]:
        uid = obs.subject_uid
        assert uid is not None
        if uid not in self._expect:
            return []
        at = obs.occurred_at_us or obs.received_at_us
        self._last_run[uid] = max(at, self._last_run.get(uid, 0))
        return self._resolve("DOC007", uid, at)

    # --- Heartbeat: DOC005 -------------------------------------------------------

    def _schedule_heartbeat(self, uid: str, from_us: int) -> None:
        period, manual = self._heartbeats[uid]
        st = self._entities.setdefault(uid, _EntityState())
        st.heartbeat_token += 1
        deadline = max(from_us + max(3 * period, manual) * S, self._grace_end_us)
        self._push(deadline, uid, "heartbeat", st.heartbeat_token)

    def _heartbeat_seen(self, obs: Observation) -> list[Finding]:
        uid = obs.subject_uid
        assert uid is not None
        self._schedule_heartbeat(uid, obs.received_at_us)
        return self._resolve("DOC005", uid, obs.received_at_us)

    # --- Helpers ------------------------------------------------------------------

    def _push(self, deadline: int, uid: str, action: str, token: int) -> None:
        heapq.heappush(self._heap, (deadline, next(self._seq), uid, action, token))

    def _has_active(self, uid: str, rules: tuple[str, ...]) -> bool:
        return any(
            (f := self._findings.get(f"{r}:{uid}")) is not None
            and f.status is FindingStatus.ACTIVE
            for r in rules
        )

    def _raise(
        self,
        rule: str,
        uid: str,
        first_seen_us: int,
        evidence_obs: str | None,
        coverage: CoverageStatus,
        *,
        details: dict | None = None,
        extra_evidence: str | None = None,
    ) -> list[Finding]:
        if not self._enabled(rule, uid):
            return []
        fp = f"{rule}:{uid}"
        existing = self._findings.get(fp)
        if existing is not None and existing.status is FindingStatus.ACTIVE:
            return []
        evidence = [e for e in (extra_evidence, evidence_obs) if e]
        f = Finding(
            id=new_uuid(),
            rule_id=rule,
            rule_version=RULE_VERSIONS.get(rule, RULE_VERSION),
            subject_uid=uid,
            status=FindingStatus.ACTIVE,
            severity=RULE_SEVERITY[rule],
            first_seen_us=first_seen_us,
            last_seen_us=first_seen_us,
            evidence_ids=evidence,
            coverage=coverage,
            details=details or {},
        )
        self._findings[fp] = f
        return [f]

    def _resolve(self, rule: str, uid: str, at_us: int, *,
                 reason: str | None = None) -> list[Finding]:
        f = self._findings.get(f"{rule}:{uid}")
        if f is None:
            return []
        if f.status is not FindingStatus.ACTIVE:
            if reason is not None:  # an administrative close also drops a pending one
                del self._findings[f.fingerprint]
            return []
        del self._findings[f.fingerprint]
        f.status = FindingStatus.RESOLVED
        f.resolved_at_us = at_us
        f.last_seen_us = at_us
        if reason is not None:
            f.details = {**f.details, "resolution": reason}
        return [f]


def _iso(us: int) -> str:
    return datetime.fromtimestamp(us / S, tz=UTC).isoformat().replace("+00:00", "Z")
