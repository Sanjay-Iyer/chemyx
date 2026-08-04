# System Overview

The subsystem is intentionally isolated under `arduino/`; production Chemyx,
NMR, and experiment scripts are not changed.

## Reused repository interfaces

- Chemyx: `chemyx_lab.instruments.chemyx.Pump` and its existing mock serial.
- NMR: `chemyx_lab.instruments.nmr.NmrRpcClient`; Test 4A uses the existing
  `ping()` readiness call, while 4B delegates acquisition to the existing
  `run_nmr_acquisition()` path and existing experiment configuration.
- Configuration: `chemyx_lab.config.read_mapping_config`, machine YAML,
  `load_pump_config`, and `load_nmr_settings`.
- Persistence: atomic JSON and Git commit discovery from the runtime journal
  utilities; run folders follow the repository's timestamped-run convention.

## Layers

1. Firmware owns fail-safe pins, bounded command input, nonblocking pulses,
   limits, movement timeout, communication-loss fault, STOP, and fault latch.
2. `transport.py` bounds serial lines and read/write time.
3. `protocol.py` parses READY, ACK, DONE, ERR, and EVENT.
4. `controller.py` validates identity and sequence IDs and keeps motion denied
   by default.
5. `config.py`, prerequisite result records, port collision checks, and the
   process lock block unsafe live dispatch before opening motion paths.
6. Four scripts run synchronously; there are no threads, async jobs,
   multiprocessing, or overlapping instrument commands.

The firmware's `commanded_position_steps` is a command-derived estimate, not a
physical measurement. Without an encoder, completed step commands cannot prove
physical position or mechanical accuracy.

