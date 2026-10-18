# Doctor Rules

Every rule is deterministic and explainable: each finding stores the observation that
triggered it. Severity sets the health-score penalty (critical 1, high 0.6, warning 0.25,
info 0).

| Rule | Name | Raised when | Severity | Notes |
|---|---|---|---|---|
| **DOC001** | Entity unavailable | `unavailable` for at least **120 s** (option, 30–3600; per entity in [[Rules per Object]]) | high | Start-up grace 120 s. Recovery needs **30 s** of stable availability; a shorter return does not close it. Paused during maintenance. |
| **DOC002** | Unknown state | changes **to** `unknown` during operation and stays **10 min** | warning | Never for entities that are `unknown` by nature: button, input_button, scene, event, conversation, tts, stt, wake_word, notify, ai_task, image. An entity already unknown at start-up is not reported. |
| **DOC003** | Flapping | **6** entries into `unavailable` within **60 min** (10 s debounce) | warning | Only with complete monitoring of the window; with a gap the result is *partial* and no finding is raised. Duplicate events do not count. |
| **DOC004** | Low battery | below **20 %** in two samples at least **30 min** apart | warning | Selected by device class `battery` and unit `%`, never by entity name; or by a manual battery mapping. Clears above **25 %**. 0 % is a valid value. |
| **DOC005** | Missing heartbeat | no report for `max(3 × period, manual threshold)` | warning | **Only for entities you declare.** An unchanged value repeated by the device counts as a report; `unavailable` does not. An unchanged temperature without a contract is never "stale". |
| **DOC006** | Missing reference | imported YAML refers to an entity that does not exist, or to an old name | warning (missing) / info (old name only) | Literal references only (templates are never evaluated). Not raised for entities deleted in the last 24 h, incomplete imports, or while the inventory is incomplete. Sentinel never edits the YAML. |
| **DOC007** | Expected run missed | an automation did not start inside the window you defined (days, time, tolerance) | warning | **Only with your calendar.** Automation off, unavailable or a monitoring gap gives *unknown*, never "missed". A start (`last_triggered`) is not proof the whole sequence succeeded. |
| **DOC008** | Weak signal | two samples below **your** threshold at least 30 min apart | info | Only for signal sources you declare, against their own scale; RSSI and LQI are never compared. Clears above threshold + 5 % of the scale. Needs *Numeric sampling*. |
| **DOC009** | Update available | an `update` entity offers a new version | info | Information only; Sentinel never installs updates. |
| **DOC010** | Storage pressure | Sentinel's own data above **80 %** of the storage budget (high above 95 %) | warning / high | Clears below 75 %. Says nothing about the Recorder database. |
| **NET001** | Endpoint unreachable | an approved probe target failed **3** times in a row | warning | Closes after 2 successes. A refused connection means "host answers, service closed", not unreachable. Only with active probes ([Network](Panel-Network)). |

## After a restart

- Open findings are restored and **not announced again**.
- Findings that can no longer be valid are closed with an explicit reason (shown in the
  incident): the object no longer exists, the rule was revised, the rule was turned off for
  the object, the module was switched off, or the module that raised it is not running.

## Rules that are deliberately not there

Sentinel does not guess. There is no "automation has not run for N days" alarm without your
calendar, no stale-sensor alarm without a heartbeat contract, and no battery detected from an
entity's name.
