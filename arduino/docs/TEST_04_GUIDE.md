# Test 4: Arduino, Chemyx, and NMR

Test 4A checks only Arduino PING/STATUS, Chemyx HELP, and NMR ping. No needle
or pump movement is sent. Test 4B is a short, sequential diagnostic using the
same D3/D4-only software-position needle controller as the experiment scripts.

```powershell
conda activate air
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.local.yaml --live --preflight-only
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.example.yaml --mock
```

Before a live 4B run, complete the staged live results, inspect the physical
needle, ensure its persistent JSON state is valid and logically at configured
UP, and review the Chemyx/NMR actions in the integrated YAML. A missing or
uncertain state blocks motion; no switch homing, limit-switch preflight, or
ENABLE wire is required. The 120-second diagnostic budget may reject long
pump/NMR plans before any motion. For the full supervised Si6 workflow, use
`scripts/01_three_instrument_system_test.py` and
`scripts/02_si6_experiment.py` only after independent physical verification.
