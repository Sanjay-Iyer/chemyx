# Test 2: unloaded motor

Use the validated D3 STEP / D4 DIR wiring without adding ENABLE or switches.
Mechanically decouple the motor from the needle axis, inspect the existing
power disconnect, and record the shaft-safe operator confirmation. Configure
small reviewed `motion.test_02_steps` and `motion.test_02_speed_steps_s` values
in `arduino.local.yaml`; the example remains uncommissioned.

```powershell
conda activate air
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.local.yaml --preflight-only
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.example.yaml --mock
python arduino\scripts\test_02_unloaded_motor.py --config arduino\configs\arduino.local.yaml --live
```

The live script requires an exact typed confirmation and records observed
direction, noise, vibration, temperature, and approximate return. It sends an
equal forward/reverse JOG pair and STOP. Firmware `ENABLE` is only a software
motion arm; it does not control holding torque through an ENA wire. Stop and
inspect for wrong direction, noise, heat, or any missing ACK/DONE. This test
does not prove safe needle-axis travel or a physical HOME.
