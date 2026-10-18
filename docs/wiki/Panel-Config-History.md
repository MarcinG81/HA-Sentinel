# Config history

Versions of selected configuration resources, not of your whole Home Assistant. Full system
recovery belongs to Home Assistant Backup (see [[Settings|Panel Settings]]).

## Resource types

| Type | What it is | Restore |
|---|---|---|
| **Imported YAML** | Automations, scripts or blueprints you paste in | Download only. Copy the YAML back into HA yourself. |
| **Managed file** | An automation file owned by Sentinel that you include in your config | Preview → approve → safe write (optional, off by default) |
| **Sentinel configuration** | Sentinel's own options, rule overrides, numeric sources and weights | Versioned automatically on every change |

## Importing

1. **Import YAML**, paste the content, choose the kind (automation, script, blueprint) and a
   label.
2. Sentinel parses it with a safe YAML parser. Nothing is executed and `!include` /
   `!secret` are kept as references, never opened. Limits: 1 MiB, 50 levels, 10 000 nodes.
3. **Plain secrets** (tokens, passwords written in clear text) are refused by default. You
   can let Sentinel redact them instead; a redacted version can never be restored.
4. Importing the same content again is deduplicated; changed content becomes a new version
   of the same resource.

A parse error never replaces the last good version.

## Versions and diffs

For every resource: the list of versions (at most 50 kept per resource), **Download YAML**,
and a diff between two versions:

- **Semantic diff**: what changed in meaning, as JSON Pointer paths (`/0/actions/1/target`)
  with add / remove / replace. Reordering actions **is** a change; key order and comments
  are not.
- **Text diff**: the raw lines, including comments and whitespace.

Imported automations are also used by the [[Map|Panel Map]], by DOC006 (missing references)
and by [[Autopilot|Panel Autopilot]].

## Managed files and safe restore

For automations you want Sentinel to be able to roll back. Off by default.

**Setup**
1. Options → *Managed files* → enable *Allow Sentinel to write managed files*.
2. Create `<config>/ha_sentinel_managed/automations.yaml` (owned by the HA user, mode `0644`).
3. Include it yourself, e.g. in `configuration.yaml`:
   `automation sentinel: !include ha_sentinel_managed/automations.yaml`
4. Check the configuration and restart Home Assistant.
5. Panel → Config history → **Managed file** → register it and confirm exclusive ownership.

Sentinel refuses files outside the managed folder, symlinks, files writable by others,
files over 1 MiB and files containing plain secrets. It never adds `!include` lines and
never touches files of the HA automation editor.

**Restore**
1. Pick a version → **Preview restore**: the exact diff, impact on dependent objects and a
   plan valid for 10 minutes.
2. **Restore** approves exactly that plan. Sentinel re-checks your admin rights, that the
   file has not changed since the preview, and the plan's age.
3. Before writing, the operation is saved in a journal. The file is written atomically
   (temporary file, fsync, replace), then `automation.reload` runs and Sentinel checks that
   the restored automations are loaded.
4. If reload or verification fails, Sentinel rolls back, but only if the file is still exactly
   what it wrote. If someone else changed it meanwhile, nothing is overwritten; the plan ends
   as *manual recovery required*.
5. After a crash at any step, the journal tells Sentinel at the next start whether the write
   happened, so a restore is never applied twice.

**Sync with file** records edits you made to the managed file outside Sentinel.

> Restoring a file never undoes physical actions devices already performed.
