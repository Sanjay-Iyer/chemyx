# Legacy Arduino firmware

These sketches are retained for provenance and recovery only. They are not the
active upload target.

The only active sketch is:

```text
arduino/firmware/needle_controller/needle_controller.ino
```

It is the runtime-configured, sequence-checked controller used with
`arduino.python.controller.NeedleController` and the YAML files under
`arduino/configs/`.

Archived material:

- `compile_time_needle_controller/`: the 0.1.0 compile-time-commissioned staged
  controller.
- `commercial_variant/`: configuration examples for the former
  `commercial_needle_controller` identity. The implementation was consolidated
  into the active sketch.
- `proven_dm542s_bridge/`: the 2026-08-06 D3 STEP / D4 DIR firmware used by the
  older standalone `dm542s_hello_world` scripts.
- `smoke_test_duplicate/`: a packaging duplicate of the former commercial
  sketch.

The proven bridge and active controller use different pin assignments and
protocols. Never upload one in place of the other without a wiring and host
software review.
