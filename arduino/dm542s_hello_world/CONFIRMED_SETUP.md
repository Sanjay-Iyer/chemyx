# Confirmed UNO R4 + DM542S Hello-World Setup

This is the hardware-specific bring-up record for the configuration reported
working on **2026-08-06**. It supplements `README.md`; it is not a generic
wiring diagram for every DM542S revision.

## Reported validation status

The operator reported successful completion of the complete staged suite:

1. `01_serial_hello.py`: USB serial `PING`/`PONG` passed with driver power off.
2. `01b_led_blink_test.py`: all ten onboard-LED blinks were visually observed.
3. `02_slow_forward_test.py`: the one-time 100-pulse motor test worked.
4. `03_forward_reverse_test.py`: the 200-pulse forward/reverse cycle worked.

These results confirm functional communication and movement for this brief
test configuration. They do not establish long-term GPIO electrical margin,
thermal performance, mechanical load capability, or production suitability.

## Hardware used

| Component | Confirmed identification / setting |
| --- | --- |
| Controller | Arduino UNO R4 Minima, USB serial on `COM3` during bring-up |
| Driver | Cloudray DM542S microstep driver |
| Motor | iMetrx NEMA 17, 42-48 variant, 2-phase/4-wire, 1.8-degree step, advertised 1.5 A/phase |
| Motor body marking | `42BL481.8-22A` with `20251022` below it |
| DC adapter | Chicony `TRH50A240` family; photographed P/N `TRH50A240-26E03 WI` |
| Adapter output | Fixed 24.0 V DC, 2.1 A maximum, 50.4 W |
| Firmware | `arduino_dm542s_bridge/arduino_dm542s_bridge.ino` |
| Serial protocol | 115200 baud; newline-terminated `PING`, `BLINK10`, `FWD`, and `CYCLE` |

`COM3` is machine-specific and may change after reconnecting the Arduino.

## Control-signal wiring

This working setup uses **common-cathode, active-high** control. The wire colors
below record this particular build; always follow the terminal labels first.

| Arduino UNO R4 | Wire color | DM542S signal terminal |
| --- | --- | --- |
| D3 | White | `PUL+` |
| GND | Black | `PUL-` |
| D4 | Grey | `DIR+` |
| GND | Purple | `DIR-` |
| No connection | — | `ENA+` |
| No connection | — | `ENA-` |

Important distinctions:

- The signal-negative wires return to **Arduino GND**, not DM542S power `V-`.
- No 24 V connection goes to the Arduino or any PUL/DIR/ENA terminal.
- Firmware D3 is STEP and D4 is DIR. STEP idles LOW and pulses HIGH.
- Direct GPIO control was functional in this test, but the UNO R4 GPIO limit
  remains 8 mA. Functional success does not prove safe long-term input current.

## Driver power wiring

The fixed 24 V adapter replaced an unsuitable adjustable 3–12 V adapter. The
old adapter, set near 6 V, could illuminate the driver indicator but did not
provide valid DM542S operating power or motor movement.

The working power path is:

```text
24 V adapter positive -> insulated DC-rated switch -> DM542S V+
24 V adapter negative -----------------------------> DM542S V-
```

In the photographed build, the driver-side `V+` lead was black and the `V-`
lead was grey, with red wiring around the positive-side switch. Because black
is normally associated with negative, polarity must be determined from the
adapter and terminal labels—not color—and ideally marked at both ends.

Before reconnecting after maintenance, measure the disconnected low-voltage
pair with a multimeter. Red probe on the intended `V+` lead and black probe on
the intended `V-` lead must read approximately **+24 V DC**. Insulate the
switch terminals and keep all low-voltage connections strain-relieved.

## Motor wiring

The working driver-terminal assignment is:

| DM542S motor terminal | Motor wire color |
| --- | --- |
| `A+` | Black |
| `A-` | Green |
| `B+` | Red |
| `B-` | Blue |

The four wires pass through an intermediate four-pin connector. That connector
must preserve the color mapping, remain mechanically secure, and be rated for
the phase current. With all power removed and the motor terminal plug removed
from the driver, `A+` to `A-` and `B+` to `B-` should each measure a similar,
small finite resistance; measurements between the two phase pairs should be
open.

Never attach, remove, or reseat a motor lead or connector while the DM542S is
powered.

## DIP-switch configuration

On this driver, moving a white actuator toward the printed `ON` arrow—down in
the bring-up photographs—means ON.

| Switch | Physical position | Logical state | Function |
| --- | --- | --- | --- |
| SW1 | Down | ON | Current selection |
| SW2 | Up | OFF | Current selection |
| SW3 | Down | ON | 2.0 A peak / 1.4 A RMS |
| SW4 | Up | OFF | Half holding current while stopped |
| SW5 | Down | ON | Microstep selection |
| SW6 | Up | OFF | Microstep selection |
| SW7 | Down | ON | Microstep selection |
| SW8 | Down | ON | 800 pulses/revolution |

Compact physical pattern:

```text
SW1-SW8: DOWN, UP, DOWN, UP, DOWN, UP, DOWN, DOWN
```

At 800 pulses/revolution:

- 100 pulses correspond to approximately 45 degrees.
- 200 pulses correspond to approximately 90 degrees.

Change DIP switches only with DM542S power off.

## Firmware motion parameters

The bridge firmware uses:

```text
STEP pin:               D3
DIR pin:                D4
STEP idle:              LOW
STEP active:            HIGH
Pulse timing:           5 ms HIGH + 5 ms LOW (about 100 pulses/second)
Direction setup delay:  10 ms
FWD:                    100 pulses
CYCLE:                  200 pulses, 2 s pause, reverse, 200 pulses
ENA:                    unused
```

## Recommended power sequence

1. Keep DM542S power off while inspecting, connecting, or changing switches.
2. Connect the motor and secure every terminal and inline connector.
3. Power the Arduino over USB so the bridge firmware establishes idle outputs.
4. Apply 24 V DC to the DM542S.
5. Confirm normal driver indication, holding torque, no fault, and no rapid heat.
6. Run only the intended one-shot Python test.
7. Switch off DM542S 24 V before disconnecting Arduino USB or changing wiring.

Immediately remove 24 V for a red alarm, violent vibration, Arduino reset,
rapid heating, smoke, sparking, or an overheating smell.

## Reference documents

- [Cloudray DM542S user manual](https://cloudray2021.oss-us-west-1.aliyuncs.com/AliExpress/AE04/%E8%AF%B4%E6%98%8E%E4%B9%A6/DM542S%20User%20Manual.pdf)
- [Arduino UNO R4 Minima datasheet](https://docs.arduino.cc/resources/datasheets/ABX00080-datasheet.pdf)
