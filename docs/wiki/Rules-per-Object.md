# Rules per Object

Panel → **Doctor** → *Rules per object* and *Numeric sources*. Everything here applies to
**one entity** (or one automation) and is stored in Sentinel's database, audited and
versioned with Sentinel's configuration.

> **Entities without a unique ID** get a new identity at every Home Assistant restart.
> A setting for such an entity ends with the next restart; the panel warns you about it.

## Overrides

Pick an entity, a rule, then:

- **Turn the rule off** for this entity. An open finding is closed with the reason *rule
  turned off for this object*.
- **Change its parameters:**

| Rule | Parameter | Range |
|---|---|---|
| DOC001 unavailable | unavailable after (s) | 30–3600 |
| DOC002 unknown | unknown after (s) | 60–86400 |
| DOC003 flapping | flaps per hour | 2–100 |
| DOC004 low battery | low below (%), clear above (%) | 1–99, 2–100 |

Empty fields keep the global value. Timers already running keep their old threshold.

**Typical use:** a tumble dryer or a watch that is regularly offline: turn DOC001 off for its
entities, or give it a long threshold such as 3600 s.

## Heartbeat contract (DOC005)

For sensors that must report regularly (a freezer thermometer, a water leak sensor):

- **Report period (s)**, 10–86400.
- **Manual threshold (s)**, optional.

DOC005 is raised after `max(3 × period, manual threshold)` without a report. A report is any
state update while the entity is available, including the same value repeated by the device.
Being `unavailable` is **not** a report. Without a contract Sentinel never assumes that an
unchanged value means a dead sensor.

## Expected runs (DOC007)

For automations that must run on a schedule: pick an `automation.*` entity, rule DOC007, then:

- **Days:** weekdays to check.
- **Time:** in the Home Assistant time zone (daylight saving time handled).
- **Tolerance:** 1–240 minutes either side.

At the end of each window:

| Situation | Result |
|---|---|
| The automation started inside the window | fine |
| The automation is off or unavailable, or monitoring had a gap | **unknown**, no finding |
| Otherwise | **DOC007**, closed by the next start |

A start (`last_triggered`) proves the automation was triggered, not that every action
succeeded.

## Numeric sources

Sentinel never guesses what a number means from an entity's name. Declare it instead:

| Kind | Scale | What it does |
|---|---|---|
| **Battery level 0–100 / 0–255** (or any scale) | min–max | The value is converted to % and checked by **DOC004**. Works without any option. Takes precedence over the device class. |
| **Signal: LQI 0–255 / RSSI in dBm** (or any scale) | min–max and *weak below* | Checked by **DOC008** against this source's own scale. Needs *Numeric sampling* in the options. |

Removing a source closes the finding that came from it (reason: *numeric source removed*);
it is not shown as a recovery.

## When an entity is re-added

If you delete an entity and a new one with the same entity ID appears, the settings stay with
the old identity, and the Doctor tab asks **"Possibly the same entity"**. Confirm to move the
settings over.
