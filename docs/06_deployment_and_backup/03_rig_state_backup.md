> [Documentation home](../00_START_HERE.md) | [Deployment and rig-state backup](00_START_HERE.md)

# Persistent physical-state maintenance

Use [SI6_START_HERE](../07_other_experiments/01_legacy_experiment_index.md) for normal operation. These maintenance
commands never open instruments and add no experiment-startup prompts.

```powershell
python -B scripts\rig_state_backup.py inventory
python -B scripts\rig_state_backup.py backup
python -B scripts\rig_state_backup.py verify --backup rig_backups\<timestamp>
python -B scripts\rig_state_backup.py compare --backup rig_backups\<timestamp>
python -B scripts\rig_state_backup.py restore-plan --backup rig_backups\<timestamp>
```

The timestamp is UTC, with microseconds to avoid collisions. Each snapshot has
SHA-256 for every file and a separately checksummed manifest. Verify both after
copying to an external drive. Checksums detect corruption; they do not provide
authentication against someone replacing the backup and both checksum records.

Inventory includes `runs/arduino` (including UNKNOWN position, pending HOME and
historical recovery journals), `runtime/si6_doses`, dated experiment journals,
legacy journals beneath `runs`, and explicitly configured Arduino state files.
Add external/custom journal directories with repeatable `--state-path <path>`.
Missing default/configured locations are reported. Backups preserve opaque and
corrupt state verbatim for investigation; successful copying does not validate
their physical meaning. Stop experiments before backup to obtain a consistent
multi-file snapshot. A detected per-file change aborts verification; an active
rig could still change between files, so a backup during motion is insufficient.

`compare` is read-only. `--root <copied-repository>` maps repository-relative
sources to a replacement software directory; external paths require manual
mapping. Source updates do not invalidate a backup. A newer/conflicting receipt
is explicitly reported, never overwritten. Missing files are reported, never
silently restored.

`restore-plan` intentionally offers no automatic writer. This is the controlled
recovery boundary: keep current records and backup records side by side, inspect
both on the physical rig, and reconcile the union of dose reservations and
uncertainties. Never restore an older ledger over a newer one, remove uncertain
receipts, or choose a new experiment ID to repeat a physical dose. There is no
automatic partial-experiment resume. If reconciliation is ambiguous, keep the
blocking records and investigate on the instrument laptop.

A copied needle-state file cannot establish physical position, even on the same
rig. Do not activate a saved `position_valid: true` reference by copying it into
an operating installation. Preserve that file and all pending markers as
evidence; use the existing operator-confirmed `home`/`set-home` procedure on the
rig to establish the current position. HOME does not clear pump, STOP, motion or
dose uncertainties. See [HOME recovery](../01_arduino/02_home_recovery.md).

Install source into a separate directory and leave the rig's state in place.
Source releases exclude `runs`, `runtime`, `results`, and `rig_backups`; the
installer does not restore or reset those records. Keep backups private and
separate from Git and release ZIPs.
