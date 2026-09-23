# Validated needle demo wiring

```text
Laptop -- USB-C --> Arduino UNO R4 Minima
                         D3 STEP --> existing DM542S STEP/PUL connection
                         D4 DIR  --> existing DM542S DIR connection
                                      DM542S --> NEMA 17 --> needle axis
                         24 V driver supply --> existing power/disconnect path
```

This documents the wiring already validated on the bench. Keep the existing
signal return/ground and driver DIP-switch settings exactly as installed and
reviewed. There is **no ENABLE wire**, **no upper limit switch**, and **no lower
limit switch** in the active demo. D2, D5, and D6 are not used by the sketch.

The earlier conceptual open-collector/limit-switch expansion is not part of
this supervised demo. The software position is open-loop and cannot detect a
stall, collision, or unpowered drift. Have the existing 24 V driver-power
disconnect available; never claim software STOP is a physical emergency stop.
