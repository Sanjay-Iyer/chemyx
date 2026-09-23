# Test 3: supervised needle-axis demonstration

Use only the validated D3 STEP / D4 DIR wiring. No ENABLE connection or limit
switches are used. A matching live Test 2 result, an inspected connected axis,
and verified `needle.steps_per_unit` and `needle.up_step_sign` are required.
The default `needle.min_position: -3` and `needle.max_position: 5` are examples;
set bounds from the actual physical clearance before live motion.

From the repository root:

```powershell
conda activate air
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.local.yaml --preflight-only
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.example.yaml --mock
```

For a live first reference, physically inspect and put the needle at the
chosen HOME, then deliberately establish `HOME=0`:

```powershell
python arduino\scripts\needle_control.py confirm-home --live --config arduino\configs\arduino.local.yaml
```

The command demands an exact typed confirmation. It sends no movement. If a
valid saved estimate already exists, inspect that it still matches reality.
Then run the bounded test:

```powershell
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\arduino.local.yaml --live
```

The test returns to software HOME from the saved position, visits logical UP
and DOWN twice, and ends at UP. There is no limit-switch preflight or physical
homing. If interrupted, the state becomes uncertain and movement is blocked
until inspection and a new explicit `confirm-home`. The estimate cannot detect
stalls or skipped steps. Do not run unattended.
