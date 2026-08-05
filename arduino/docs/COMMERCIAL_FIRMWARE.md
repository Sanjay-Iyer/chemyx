# Commercial One-Upload Firmware

`commercial_needle_controller.ino` is the runtime-configured UNO R4 Minima
variant. It is intended to be uploaded once to one reviewed controller and then
used by Tests 1-4 without recompiling when motion distances or limits change.

## Safety model

- Every reset boots with motion and limits uncommissioned, the driver disabled,
  position unknown, and all numeric motion ceilings zero.
- Tests 2-4 stage and atomically apply the reviewed YAML values before motion.
- Runtime configuration is accepted only while stopped and driver-disabled.
- I/O polarity is locked after the first successful apply until the next reset.
- ENABLE, HOME, JOG, and MOVE_ABS reject commands until configuration succeeds.
- Reconfiguration invalidates homing and command-derived position.
- Test 4B skips reconfiguration when the live firmware already exactly matches
  YAML, preserving the homed/enabled state established by Test 3.

Runtime configuration avoids firmware changes; it does not remove physical
commissioning. The open-collector/open-drain interface, external fail-safe
enable bias, reviewed DM542S 5 V control interface, fuse, emergency disconnect,
limit switches, hard stops, motor current, microsteps, travel, speed, and acceleration must
still be independently verified. Change I/O polarity only with driver power
removed, then reset the Arduino and rerun the staged tests.

## Upload once

Open and upload:

```text
arduino/firmware/commercial_needle_controller/commercial_needle_controller.ino
```

Select **Arduino UNO R4 Minima**. The uploaded identity is
`commercial_needle_controller`, version `1.0.0`.

Copy the matching example and record the explicit COM port:

```powershell
Copy-Item arduino\configs\commercial_arduino.example.yaml arduino\configs\commercial_arduino.local.yaml
```

Test 1 can run with motion placeholders false/null. Before live Test 2, replace
every Test 2 placeholder with reviewed facts. Before Test 3, the same local file
must additionally contain the reviewed limit and axis values. This changes YAML
configuration, not the uploaded firmware.

```powershell
python arduino\scripts\test_01_arduino_connection.py --config arduino\configs\commercial_arduino.local.yaml --live
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\commercial_arduino.local.yaml --live
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\commercial_arduino.local.yaml --live --preflight-only
python arduino\scripts\test_03_needle_axis.py --config arduino\configs\commercial_arduino.local.yaml --live
```

Test 4 uses a local copy of
`commercial_integrated_hello_world.example.yaml` and still requires the Chemyx
pump, NMR endpoint, prior passing records, and approved experiment actions.

## Scope

One image can support multiple reviewed mechanics through different YAML
values, but no open-loop firmware can infer safe travel or detect every physical
failure. There is no encoder, force sensor, temperature sensor, or connected
DM542S alarm input. Stalls, skipped steps, collisions, and wrong driver current
remain physical commissioning and inspection responsibilities.
