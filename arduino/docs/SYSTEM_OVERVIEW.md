# Needle controller architecture

The UNO R4 sketch accepts sequence-numbered serial `PING`, `STATUS`, `JOG`,
`STOP`, and runtime configuration commands. Its sole motor GPIO outputs are
D3 STEP and D4 DIR. It bounds steps, speed, acceleration, elapsed motion time,
and serial responses. Firmware pulse counts reset with the board; they are not
the persistent needle position.

`arduino/python/controller.py` verifies READY identity and ACK/DONE sequencing
and sends the existing JOG command. `arduino/python/needle_state.py` adds
software HOME=0, UP-positive logical movement, configurable bounds and
step-per-unit conversion, an atomic durable JSON state, explicit manual HOME
confirmation, and fail-closed uncertainty on interrupted movement. It is
used by the needle operator CLI, staged Test 3/4B, and both three-instrument
Si6 entry points. The same COM-port process lock prevents competing live
sessions. Chemyx and NMR implementations are unchanged.

Staged Test 1 checks Arduino serial/LED only. Test 2 exercises a decoupled
motor. Test 3 runs supervised software-position UP/DOWN cycles. Test 4A checks
all three connections; Test 4B uses the tracked needle in a sequential pump
and NMR diagnostic. Matching prior live result records remain a staged-test
requirement, but no ENABLE/switch commissioning or switch preflight exists.

See `arduino/README.md` and `arduino/docs/FIRMWARE.md` for commands and the
explicit limits of this supervised-only design.
