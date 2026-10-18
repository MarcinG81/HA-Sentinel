# Incidents and Probable Cause

## Findings and incidents

- A **finding** is the result of one rule for one object, for example "DOC001 on
  `sensor.garden_temp`".
- An **incident** groups findings that belong to one episode. There is one active incident
  per problem: the same failure never opens a second one while the first is open.

## Grouping

Findings are grouped when they start **within 120 s** and share an **explicit dependency**
in the [[Map|Panel Map]]: the same device, integration entry, network host or service
endpoint. Sharing only an **area** or happening at the same moment is **not** enough. Two
independent failures in the same room stay separate incidents.

Titles:
- **Rule name** (e.g. *Entity unavailable*): a single finding.
- **Common failure: X**: several affected objects with the shared parent *X*. For example,
  14 entities of a tumble dryer give *Common failure: Tumble dryer*.

## Probable cause ranking

For each incident Sentinel looks up to three dependency steps upwards (devices,
integrations, hosts, endpoints; areas and "controls" links are ignored, as are links with
confidence below 0.5) and scores each candidate:

`score = 0.20·T + 0.25·D + 0.20·P + 0.35·E − 0.30·K` (clamped to 0–1)

| Feature | Meaning |
|---|---|
| **T**, time agreement | how close the candidate's failure is to the effects (within 120 s; a candidate that fails *after* its effects gets 0) |
| **D**, share affected | affected dependents / all observable dependents of the candidate |
| **P**, path quality | confidence of the dependency paths |
| **E**, direct evidence | 1 for an explicit failure of the candidate itself (e.g. an approved probe says the endpoint is down), 0.5 for an unavailable status entity, 0 for none |
| **K**, counter-evidence | share of dependents that are demonstrably healthy after the start |

Labels: **strong hypothesis** (score ≥ 0.75 and direct evidence ≥ 0.5), **possible cause**
(0.5–0.75), otherwise no main cause. At most three hypotheses, each with its features and
counter-evidence. Identical inputs on the same graph revision give identical rankings.

**A ranking is not a probability and never confirmed automatically.** You can confirm or
reject it; your verdict is stored separately.

Without direct evidence the wording stays careful: "A common problem with X is possible", not
"X is down".

## Configuration changes

If an imported or managed automation changed in the **30 minutes before** the incident, and
one of its automations reads, controls or calls an affected object (or something it depends
on), the incident lists it under *Configuration changed shortly before*. Time order alone is
not evidence, so it never raises the score.

## Recurrence

A failure after the incident was resolved opens a **new** incident that links the previous
one ("The same problem was resolved before and came back").

## Status and notifications

`open` → `acknowledged` → `resolved`. Open and acknowledged incidents can be **muted**
until a time; afterwards they return to their previous state if the problem persists.
Recovery requires observation; acknowledging never closes an incident.

With HA notifications enabled (option, off by default) a new incident creates one
persistent notification, which is dismissed on recovery. Repeats of the same problem wait
30 minutes, but a new critical severity is shown at once.
