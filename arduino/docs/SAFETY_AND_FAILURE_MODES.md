# Needle demo safety and failure modes

The UNO R4 Minima uses only D3 STEP and D4 DIR with the validated wiring.
There is no ENABLE output, physical limit switch, encoder, or homing sensor.
Firmware `ENABLE`/`DISABLE` are software command arms, **not** a motor-power
cutoff. Keep the existing 24 V driver-power disconnect accessible during every
attended move.

Python checks proposed logical positions against the configured min/max before
dispatch. It writes `position_valid=false` before a JOG command and only
writes the new logical position after an ACK/DONE with a matching pulse count.
Timeout, ERR, disconnect, STOP while moving, or host crash leaves the saved
position uncertain; inspect the axis and explicitly confirm HOME again. A
corrupt state file blocks motion rather than being silently overwritten;
explicit `confirm-home` retains it as a `.corrupt-*.bak` before re-referencing.

The JSON position is an open-loop estimate. It cannot detect missed pulses,
stall, collision, manual displacement, or drift while unpowered. Firmware STOP
halts future pulses when communication works; it is not a physical emergency
stop. Do not run this demo unattended or represent software limits as
equivalent to physical limit switches.

The three-instrument workflow still stops the Chemyx pump on failures and
preserves NMR artifacts and event records. Never continue another instrument
action after needle uncertainty without physical inspection and reconciliation.
