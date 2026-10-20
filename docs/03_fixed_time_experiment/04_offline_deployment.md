# 04 — Update through Git and the WORK external drive

[Fixed-time experiment index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

The user's deployment workflow is:

**HOME Git push -> WORK Git pull -> external hard drive from WORK to the second WORK laptop.**

HOME does not create or supply offline ZIPs, release exports or dependency
packages for this workflow. Existing archives can remain unused. The filename
of this guide is retained so existing documentation links continue to work.

## 1 — HOME: source changes through Git

Edit and locally validate the source, then commit and push through the user's
normal Git process. Include new Python files, configuration files and the
numbered documentation: untracked files do not reach WORK until committed.
The user handles the actual Git push/pull.

Local no-hardware validation can use the existing `ai` environment:

```powershell
cd C:\code\chemyx_pump
```

```powershell
conda activate ai
```

```powershell
python scripts\04_si6_fixed_time_run.py --validate-config
```

No package-export step is part of completing a code change on HOME.

## 2 — WORK: pull the source update

Use the existing repository and Git branch on WORK, pull the committed changes,
and retain its installed Python environment. The fixed-time restart update adds
no new dependencies or firmware requirement. Review the selected COM3 needle,
COM4/COM6 machine file and physical recipe for the actual rig.

## 3 — WORK to the second WORK laptop: external hard drive

Use the user's existing external-drive copy process from WORK. Transfer the
updated source and any existing WORK-side dependencies or assets that the second
WORK laptop needs. Ignored wheels, installers, vendor resources and experimental
data do not arrive through Git automatically; handle those on WORK when needed.
Preserve the relevant rig's configuration, state and results during the update.
HOME's development environment or mock state is not the transfer source.

## 4 — Start the experiment on the instrument laptop

After manually restoring Channel 1 empty, Channel 2 at 5.00 mL and new reaction
paths, with the official Arduino HOME established and current position tracked:

```powershell
cd C:\code\chemyx_pump
```

```powershell
python scripts\04_si6_fixed_time_run.py --live
```

Use the existing verified instrument-laptop Python. Select COM6 explicitly when
needed, as shown in [01 — Run guide](01_run_guide.md). Launch starts directly,
with no interactive prompt or prior-dose reconciliation. Both official HOME
definitions must exist from manual setup and are preserved. Startup logs the
manual return to existing pump HOME loading and MOVES to the saved Arduino HOME.
See [05 — Restart behavior](05_restart_behavior.md).
