# Autopilot Checks

| Rule | Name | Detects | Level |
|---|---|---|---|
| **AUT001** | Missing entity | An entity referenced literally does not exist (or was renamed: the new name is suggested) | error, only with a complete inventory |
| **AUT002** | Unknown action | The called action (service) is not registered | warning (its integration may not be loaded) |
| **AUT003** | Possible conflict | The same trigger leads to opposite actions on the same target | warning, heuristic |
| **AUT004** | Possible loop | Automation A controls a state that triggers A again (cycles found with Tarjan's algorithm), with possible breakers listed | warning, heuristic |
| **AUT005** | Skipped runs | A delay or wait combined with `mode: single` and frequent triggers | warning |
| **AUT006** | No hysteresis | An on/off pair on the same threshold without a gap | info (recommendation) |
| **AUT007** | Template | A dynamic target or Jinja template: the analysis is incomplete there | info |
| **AUT008** | High-risk action | Actions on `lock`, `alarm_control_panel`, `valve` or `shell_command` | risk |
| **AUT009** | Duplicate id | Two automations with the same `id` in one file | error |
| **AUT010** | Unsupported fragment | Unknown blueprint, custom trigger or structure Sentinel cannot read | unsupported, with its location |

A cycle alone is not an error; it is reported as a *potential* loop. Conflicts with dynamic
conditions are "possible", never "confirmed". Up to 1000 automations per analysis, 5 s limit.
