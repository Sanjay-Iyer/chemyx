# Active needle firmware: validated D3/D4 demo

The only active sketch is
`arduino/firmware/needle_controller/needle_controller.ino` for the Arduino UNO
R4 Minima. It identifies as `needle_controller` version `1.2.0`.

| Arduino pin | Existing connection |
| --- | --- |
| D3 | STEP / PUL input on the already-working DM542S bridge |
| D4 | DIR input on the already-working DM542S bridge |

No ENABLE connection, upper limit switch, lower limit switch, or other Arduino
GPIO connection is required. **Do not rewire the proven setup or change driver
switch settings to use this sketch.** The legacy bridge used D3/D4 too; its
positive/forward movement held DIR LOW. Version 1.2.0 preserves that direction
and the established 5 ms HIGH / 5 ms LOW pulse timing at a maximum of 100
steps/s, while adding bounded serial acknowledgements.

The firmware begins with motion disarmed after reset. Python applies the
reviewed YAML with `CONFIG_IO`, `CONFIG_LIMITS`, and `CONFIG_APPLY`; the
`ENABLE`/`DISABLE` commands are *software motion arms only*. They do not drive
an ENA pin and do not remove holding torque or motor power. `JOG <signed steps>
<steps/s>` sends bounded relative pulses and reports ACK, DONE, or ERR.
`STOP`, communication-loss detection, and motion timeouts stop pulse generation.
Physical `HOME` and firmware `MOVE_ABS` deliberately reject with an error:
without switches they cannot establish a physical reference. Python's
`TrackedNeedle` supplies the software HOME and bounded logical moves.

The saved position is a **software estimate, not encoder feedback or physical
homing**. Missed steps, a stall, manual axis movement, or unpowered drift are
not detectable. Use only for a supervised demo; do not treat this as an
unattended production safety system. Keep the real 24 V driver-power disconnect
accessible. `STOP` is not a substitute for that disconnect.

## Offline compile and instrument-laptop upload

From the repository root, with the offline Arduino toolchain bundle already
transferred:

```powershell
powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Compile
```

With 24 V motor power **off**, the verified Arduino COM port substituted for
`COMx`, and the Arduino Serial Monitor closed:

```powershell
powershell -ExecutionPolicy Bypass -File offline\arduino_toolchain.ps1 -Upload -Port COMx
```

Do not upload from this development computer. On the instrument laptop, first
verify the D3/D4 wiring and intended movement direction. See
`arduino/docs/TEST_03_GUIDE.md` for the supervised software-HOME procedure.
