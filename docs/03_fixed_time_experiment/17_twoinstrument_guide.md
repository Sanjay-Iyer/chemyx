# 17: Chemyx + NMR, stationary needle

Edit `configs/experiments/17_twoinstrument.yaml` at the top:

```yaml
withdraw_volume_ml: 5.0
infuse_volume_ml: 5.2
```

Change only `infuse_volume_ml` (for example to `5.1`, `5.5`, or `6.0`) to change
the return independently. Both flow rates default to Script 15's 5.0 mL/min. Channel 1
uses the same 20 mm diameter / 20 mL syringe and timed transfer + STOP method.

Set `initial_volume_ml` to the actual liquid loaded before starting (default
6.0 mL). Each 5.0/5.2 cycle delivers 0.2 mL extra; 25 cycles need 5.0 mL extra.
Leave withdrawal headroom and enough flask capacity. At 5.0/6.0, 25 cycles
would need 25 mL extra: a single 20 mL syringe cannot supply that without
replenishment. The script never replenishes; the operator must choose a feasible
loading/schedule. Existing per-transfer syringe limits remain in effect.

On WORK, after pulling the HOME Git changes (then transferring from WORK by
external drive to the second WORK laptop as needed):

```powershell
cd C:\code\chemyx_pump
python scripts\17_twoinstrument.py --live
```

Secure the needle inside the flask, prepare the syringe and open return path,
and leave NMReady idle. Read the startup volume/schedule summary and answer
the single `Confirm rig is ready [y/N]:` prompt. Arduino can be disconnected;
no Arduino imports, commands, HOME checks or position files are used.

Every observation is **withdraw -> NMR -> infuse**, completing each step before
the next. Defaults: 25 slots at 0, 15, ..., 360 minutes; 8 scans each. Change
`experiment.observation_count`, `experiment.interval_minutes`, or `nmr.scans`
in YAML. Times stay anchored to the start; overrun slots are skipped as in 15.

Raw files `observation_001.dx`, ... and timestamped `log.txt` are saved directly
under `results/experiments/17_twoinstrument_<timestamp>/`. A suffix avoids
collisions; existing folders/files are never overwritten. Use `--mock` in place
of `--live` for fake instruments and simulated time (also one confirmation).

On NMR failure, the script logs the error, attempts iFlow cancel/idle waiting,
and still attempts the configured infusion after a completed withdrawal.
It continues only after the return completes and NMR idle is confirmed.
On pump error or uncertain displacement it STOPs and does not blindly infuse.
Ctrl+C also attempts STOP and a known, completed sample's return. Unreturned
or uncertain liquid is reported for physical inspection. Connections close
after cleanup; saved spectra remain. No fault files, journals, HOME records
or previous logs are read to permit another run; prepare the rig again first.
