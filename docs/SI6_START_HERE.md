# Si6: start here

Run commands from `C:\code\chemyx_pump` (or the copied repository root).
HOME has no instruments. Software PASS does not commission spectral endpoint
thresholds or prove physical delivery/needle position.

## HOME

```powershell
conda activate ai
python -B scripts\validate_si6_synthetic_analysis.py
python -B scripts\validate_si6_offline.py --copy-test --run-tests
```

The first command validates numeric completion, synthetic processed spectra,
historical counterexamples and the full shared mock workflow. It saves a new
`test_tmp_si6_synthetic_validation/<timestamp>/REPORT.md`, CSV traces, spectral
evidence, journal and dataset-titled PNG/SVG/PDF figures. Levels1/2 are explicitly historical offline statistical/QC counterexamples.
Level3 runs the current area-only workflow with48/12h runtime ceilings. Acquisition
cadences remain120/30min on a virtual clock.

## WORK1 after pulling

Use the prepared environment (`conda activate ai`, or the offline `.venv`
Python below), then run both HOME commands. Follow
[offline deployment](OFFLINE_DEPLOYMENT.md) for preparing/copying dependencies.
Copy the nominal experiment and machine template to reviewed per-rig files:

```powershell
Copy-Item config_templates\experiments\si6_two_stage_nominal.yaml configs\experiments\si6_run.local.yaml
Copy-Item config_templates\machines\si6_instrument_settings.example.yaml configs\machines\si6_work.local.yaml
python -B scripts\02_si6_experiment.py --workflow-config configs\experiments\si6_run.local.yaml --machine-config configs\machines\si6_work.local.yaml --arduino-config arduino\configs\arduino.local.yaml
```

Edit the copies before that validate-only command: unique physical reaction ID,
syringes/dose, timing/scans/thresholds, verified COM ports and NMR host. Review
Arduino calibration on this rig. These copy commands are for new files; preserve
existing reviewed settings. Default `.local.yaml` files are tracked shared
starting points, so keep rig-only copies private as described in the config guide.

## WORK2 after copying the offline package

After the installation steps in [offline deployment](OFFLINE_DEPLOYMENT.md):

```powershell
.venv\Scripts\python.exe -B scripts\validate_si6_offline.py --copy-test --run-tests
.venv\Scripts\python.exe -B scripts\validate_si6_synthetic_analysis.py
```

Repeat WORK1 configuration review using WORK2's actual endpoints and calibration.
Preserve the physical rig's run history, needle state and dose ledger when moving
that same experiment between laptops. Copy source alone is insufficient for
recovery; there is no automatic partial-run resume.

## Read next / results

- [Script map and exact commands](SI6_SCRIPT_USER_GUIDE.md)
- [Which config to edit](SI6_CONFIG_USER_GUIDE.md)
- [Physical setup and operating procedure](SI6_TWO_STAGE_OPERATOR_GUIDE.md)
- [Final HOME validation evidence](SI6_FINAL_HOME_VALIDATION_REPORT.md)
- [Offline requirements](OFFLINE_REQUIREMENTS.md), [handoff](SI6_TWO_STAGE_HANDOFF.md)

Normal runs use the experiment's `output.run_root_dir`; mocks use its `_mock`
sibling. Within each run: `stages/stage_1`, `stages/stage_2`, `transition`, `final`
and `operation_journal.jsonl`. Synthetic validation outputs are local/ignored;
rerun the one command after pull/copy to regenerate them.

Still unverified: physical Chemyx channel addressing/delivery, syringe calibration,
needle travel/direction/fluid path, Arduino wiring/limits, NMR acquisition and
real chemical endpoint thresholds. QC warnings from the retained real fixture are retrospective only. Complete supervised WORK commissioning before a live reaction.
