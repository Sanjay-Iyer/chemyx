# Test 4 Guide: Arduino, Chemyx, and NMR

## Test 4A connection-only preflight

Required hardware: USB-connected Arduino, separately configured Chemyx serial
port, and configured NMR RPC endpoint. Arduino and Chemyx COM ports must differ.
Motor interface installation is not required because 4A sends only Arduino
PING/STATUS, Chemyx `help`, and NMR `PingSpectrometer`.

Keep the motor driver and pump fluid path in a no-motion state. No NMR sample
acquisition is performed.

```powershell
conda activate ai
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.local.yaml --live --preflight-only
```

Expected output: one combined report showing the three read-only checks and an
empty physical-action list. Stop on endpoint mismatch, port collision, any
Arduino moving/enabled surprise, or any unsolicited instrument action. Final
safe state: all clients closed/stateless and no physical operation dispatched.

Common problems: Arduino Serial Monitor owning the port, wrong Chemyx COM port,
same COM port assigned twice, missing machine-local YAML, or unreachable NMR
RPC host.

## Test 4B full sequential hello world

> **LIVE TEST BLOCKED. Framework and mock only.**

Required: matching live Tests 1-3, a matching live Test 4A, and every Test 3 requirement, plus explicitly
reviewed 1-based pump action index(es) from the existing Si6 experiment YAML,
explicit `si6_configured_1d`, settling delays, and `.dx` expectation. The code
does not invent pump volume/rate/diameter, NMR parameters, or needle travel.
The operator must also confirm no Arduino reset, power loss, manual axis motion,
or loss of holding torque since the matching Test 3. Runtime STATUS must still
show homed, known, enabled, idle, fault-free, and exactly at safe UP; otherwise
4B remains blocked and the axis must be revalidated.

Safe mock command:

```powershell
python arduino\scripts\test_04_integrated_system.py --config arduino\configs\integrated_hello_world.example.yaml --mock
```

Expected future sequence: verify homed; DOWN; settle; selected existing pump
action; settle; verify motor idle; existing configured NMR acquisition and
artifact; safe UP; mandatory opposite-direction, equal-volume selected existing
return action. Commands never
overlap. Hard ceiling: 120 seconds.

The complete sequence is budgeted before the first move. The present example
selects no live actions, and the existing 5 mL + 5 mL pair at 5 mL/min alone
takes about 120 seconds, leaving no budget for needle motion, settling, or NMR;
those values therefore cannot be approved for this 120-second diagnostic.

Stop on any failure and do not continue to the next instrument. Attempt Arduino
STOP when reachable and existing Chemyx stop when appropriate. Never retry NMR
automatically. Preserve partial artifacts, mark incomplete needle motion
uncertain, and require inspection. Do not blindly command UP after failure.

Final safe state on success: needle at commanded safe UP, motor idle, pump
stopped, NMR acquisition complete, and combined event/result records written.
