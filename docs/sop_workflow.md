# Si6 SOP Workflow Plan

This is a first software translation of `NMR/chemyx_sop.md`. It is intentionally
conservative and easy to mock before it controls the full physical rig.

## One Sampling Cycle

The current cycle is represented as:

```text
1. Withdraw 8 mL with needle out of solution
2. Lower needle into solution
3. Withdraw 5 mL
4. Pause before NMR
5. Run or ingest NMR
6. Infuse 13 mL
7. Raise needle out of solution
8. Withdraw 5 mL
9. Infuse 5 mL
```

The needle steps are placeholders because the Arduino/actuator hardware is not
yet incorporated. They are explicit in the workflow so the future motor code has
a defined place to attach.

## Reaction Schedule From SOP

The manual plan is:

```text
initial reaction under N2
repeat sampling cycle every 60 min for about 24 hr
add diphenyl silane
repeat sampling cycle every 15 min for about 1.5 hr
add acetone
repeat sampling cycle every 15 min for about 1.5 hr
add diphenyl silane
repeat sampling cycle every 15 min for about 1.5 hr
repeat acetone/silane blocks desired number of times
stop reaction
```

The current script only executes a requested number of sampling cycles. Reagent
addition blocks and stop/continue logic will be added after first hardware
tests.

## Mock Run

```powershell
python scripts\sop_mock_workflow.py --cycles 1
```

Mock run with `.dx` ingestion:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --data-dir NMR\06-08-26
```

Mock pump run with real NMR RPC:

```powershell
python scripts\sop_mock_workflow.py --cycles 1 --nmr-rpc --nmr-save-dir runs\nmr
```

## Real Bench Test

Start with a small volume scale:

```powershell
python scripts\sop_mock_workflow.py --real --volume-scale 0.05
```

Example using explicit work-laptop values:

```powershell
python scripts\sop_mock_workflow.py --real --port COM4 --baud 115200 --channel 1 --diameter 28.6 --rate 2.0 --volume-scale 0.05
```

With NMR RPC after standalone NMR testing:

```powershell
python scripts\sop_mock_workflow.py --real --nmr-rpc --port COM4 --baud 115200 --channel 1 --diameter 28.6 --rate 2.0 --volume-scale 0.05 --nmr-save-dir runs\nmr
```

When the fluid path is verified, increase `--volume-scale`.

## Timing

By default:

- mock mode uses `pause_scale = 0`
- real mode uses `pause_scale = 1`

To test real hardware without waiting five minutes at the NMR pause:

```powershell
python scripts\sop_mock_workflow.py --real --pause-scale 0 --volume-scale 0.05
```

## Next Engineering Steps

- Replace needle placeholders with an Arduino/actuator controller.
- Confirm which RPC route is most stable on the instrument software version:
  `Experiment/1D/Start`, `iFlow/RunExperiment`, or `Service/Acquire`.
- Save workflow logs to `runs/<date>/`.
- Add an experiment state file so a long sequence can resume after interruption.
- Add chemistry stop/continue rules using the 6.1 ppm trend.
