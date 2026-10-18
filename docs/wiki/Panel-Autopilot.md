# Autopilot

An assistant for checking automations. **Off by default**: enable it in the options
(*Modules → Autopilot*). It has no AI, never installs anything into Home Assistant and never
calls a service.

## Static checks (lint)

Choose an imported automation, script or blueprint (import it first in
[[Config history|Panel Config History]]). Sentinel reports:

- **Analysis completeness:** how much of the YAML it fully understood. Templates and
  unsupported fragments lower it; they are never executed.
- **Findings** by level (error, warning, risk, info, unsupported). Each one points to the
  exact line and column; click it to see the code around it.

The rules are listed in [[Autopilot Checks]]. **No findings means only that the defined rules
passed**, not that the automation is correct.

## Draft an automation

A small wizard for the most common case:

- **When:** a state change (optionally *to* a state) or a value crossing a threshold
  (above / below), optionally *for at least* N seconds.
- **Only if:** an optional state condition.
- **Then:** turn lights on (optionally with brightness %) or off.
- **Mode** (single, restart, queued, parallel) and an optional **cooldown** (only with mode
  *single*).

The draft goes through **the same parser and checks** as an import. You can download the
YAML or keep it as an imported snapshot, then add it in Home Assistant yourself. Unknown
entities are refused with a clear message.

## Replay

Runs an automation against **recorded history** (from the Recorder) to show when it *would
have* triggered, whether its conditions were met and which actions it would have planned.

- No service is ever called.
- Supported: state and numeric_state triggers and conditions, simple if/choose. Delays,
  waits and templates are listed as unsupported.
- Missing history is reported as *unknown*, never as false.
- Planned actions do not change later states: it is not a simulation of your home.
- Limits: up to 24 h window, 50 entities, 10 000 events.

Replay needs the Recorder; without it the button is unavailable with the reason shown.
