# Overview

The first tab answers "is everything fine, and how sure are we?".

## Health score

A number from 0 to 100, with **coverage** shown next to it.

- **Unit = device.** Every device is one unit; an entity without a device is its own unit.
- Each unit takes the penalty of its **worst active finding**: critical 1, high 0.6,
  warning 0.25, info 0. Several problems of one device are not added up.
- `H = 100 × (1 − Σ(weight × penalty) / Σ weight)`, rounded half up.
- **Coverage** is the share of units observed continuously during the last 15 minutes with a
  valid baseline. Below 80 % (and during the first 15 minutes after a start or a gap) the
  score is greyed out as **partial**, the sensor reports *unknown*, and a countdown shows
  when it will be complete.
- Units in planned **maintenance** are left out and counted separately. A *muted* incident
  still counts.

The score is an indicator of what Sentinel can see, not a measure of your home's safety.

## Active incidents

The most important open incidents, with a button that opens the top one. Details:
[[Doctor|Panel Doctor]].

## Score components

A table with one row per unit: the unit's name, its active rules, penalty, coverage and
weight. By default only notable units are listed: those with a penalty, an exclusion, incomplete
coverage or a non-default weight. Tick **Show all** to see every unit.

**Weight (1–5):** make important devices count more. For example, give the heating controller
weight 5 and a garden sensor weight 1. Changes are audited and versioned.

## Modules and capabilities

The state of each module: `ready`, `degraded`, `failed` or `disabled`. A failed module never
takes the others down; Doctor keeps working even if, say, the network module fails.

Below it, **capabilities**: what works on this installation and, if something does not, the
exact reason (for example *recorder unavailable* for replay, or *module disabled*).

The Overview also shows how many entities Sentinel observes.

If Sentinel itself is degraded (queue overload, read-only database), a banner explains why
at the top.
