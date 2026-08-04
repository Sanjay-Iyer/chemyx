# Hardware Schematic

## Current approved Test 1 connection

```text
Dynabook laptop -- USB-C data cable --> Arduino UNO R4 Minima
```

Nothing else is connected for Test 1.

## Future conceptual motion system

```text
Laptop
  |
  | USB-C
  v
Arduino UNO R4 Minima
  |
  | STEP / DIR / ENABLE logic
  v
Professionally verified open-collector/open-drain signal interface
  |
  v
DM542T stepper driver ------> NEMA 17 motor ------> needle mechanism
  ^
  |
24 VDC supply through fuse and emergency driver-power disconnect

Upper NC limit ----> reviewed input interface ----> Arduino
Lower NC limit ----> reviewed input interface ----> Arduino
```

The Arduino sends logic commands; it does not power the motor. Do not connect
UNO R4 GPIO directly to DM542T PUL, DIR, or ENA. No exact signal wiring is
specified because the interface type, inversion, driver common-anode/common-
cathode arrangement, and 5 V selector setting have not been verified. A
ULN2803A must not be assumed merely because it is one possible interface.

Before wiring, an electrical-controls reviewer must issue an interface-specific
schematic that documents source/sink current, grounds/isolation, PUL/DIR/ENA
polarity, selector position, external fail-safe enable bias during MCU reset or
power loss, and NC switch logic.
