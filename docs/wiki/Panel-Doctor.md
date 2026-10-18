# Doctor

The working tab: incidents and everything that controls how they are raised.

## Incident list

Open incidents sorted by severity and time. Tick **Show resolved** for the history. Each row
shows the title, severity, number of affected objects, the probable cause (if there is one)
and since when.

An incident groups findings of **one episode**: if a Zigbee coordinator drops and 18 devices
go unavailable, you get one incident with 18 affected objects, not 18 alarms. How grouping
and causes work: [[Incidents and Probable Cause]].

## Incident details

The card keeps four things apart, on purpose:

| Section | Meaning |
|---|---|
| **Observation** | What was actually seen: rule, coverage, first seen, rule-specific facts (value, threshold, missing references…) and evidence |
| **Hypothesis** | Ranked candidate causes with their features. A ranking, not a probability. |
| **Counter-evidence** | Facts that speak against the hypothesis, such as dependents that are still healthy |
| **Proposed check** | What to look at first |

Additional banners:
- **Incomplete coverage.** Monitoring had a gap, so the duration has bounds instead of a
  precise value.
- **Recurrence.** The same problem was resolved before and came back, with a link to the
  previous incident.
- **Configuration changed shortly before.** An automation on the dependency path changed in
  the 30 minutes before the start. This is listed separately and never raises the ranking.

Actions:
- **Acknowledge.** "I've seen it." It does **not** mean fixed; the incident closes only
  when the problem recovers.
- **Mute** for 1 h, 4 h, 24 h or 7 days, with a reason. A muted failure still counts in the
  health score.
- **Confirm / reject the cause.** Your verdict is stored separately from Sentinel's ranking.

An incident is never closed by silence: missing data keeps it open with an "unknown" status.

## Maintenance

Planned work on an entity, a device or a whole integration (30 min, 1 h, 4 h or 24 h, with
a reason). During the window Sentinel raises no new failures for it, and it is left out of
the health score (counted separately). The window ends on its own; **End now** stops it
early. If the device is still down afterwards, the failure is reported then.

## Possibly the same entity

Shown only when needed: an entity was removed and a new one with the same entity ID appeared.
That does not prove it is the same device, so Sentinel does not merge them. **Same entity:
move settings** moves its rule settings, numeric source and weight to the new entity;
**Different entity** keeps them apart. History stays where it was recorded.

## Rules per object

Turn a rule off or change a threshold for one entity, declare heartbeats and expected runs.
See [[Rules per Object]].

## Numeric sources

Battery levels without a battery device class, and radio signal scales. See
[[Rules per Object]].
