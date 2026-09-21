# Instrument communication smoke tests

These three tests answer one question: **can this laptop exchange data with the
instrument?** They deliberately avoid pump movement, NMR acquisition, valve
movement, and Arduino motor control.

Run all commands from the repository root:

```powershell
cd C:\code\chemyx_pump
conda activate ai
```

The only hardware-specific Python dependency is `pyserial`. The repository's
normal offline installation from `requirements.txt` includes it.

## 1. Chemyx pump: read-only HELP test

Connect and power the Chemyx. Close the Chemyx GUI, Arduino Serial Monitor,
PuTTY, and any other program that may own its COM port.

First list the ports:

```powershell
python smoke_test\01_smoke_chemyx.py --list-ports
```

Identify the pump port by unplugging/reconnecting the pump USB/serial adapter.
Then run, replacing `COM6` if necessary:

```powershell
python smoke_test\01_smoke_chemyx.py --port COM6 --channel 1
```

The script opens the port at 115200 baud, 8N1, sends `1 help` followed by a
carriage return, prints the raw response, and closes the port. It sends no
movement command. A PASS proves serial communication, not physical pump
calibration or fluid movement.

If the pump is configured without a channel prefix, use:

```powershell
python smoke_test\01_smoke_chemyx.py --port COM6 --channel 0
```

## 2. NMR: read-only HTTP status test

Connect the laptop to the NMR Ethernet interface. Internet access is not
required; this is local HTTP communication. The established repository address
is `169.254.30.54` on port `5000`.

Run:

```powershell
python smoke_test\02_smoke_nmr.py --host 169.254.30.54 --port 5000
```

The script sends only these read-only requests:

```text
GET /interfaces/iStatus/PingSpectrometer
GET /interfaces/iStatus/RpcEnabled
GET /interfaces/iStatus/SpectrometerStatus
```

It does not change NMR settings or start an acquisition. If it cannot connect,
check the Ethernet cable, laptop IPv4 configuration, NMR address, Windows
firewall, port 5000, and whether RPC is enabled on the NMR.

An optional Windows TCP check is:

```powershell
Test-NetConnection 169.254.30.54 -Port 5000
```

## 3. Arduino: USB PING/PONG test

### Upload the included firmware

The upload file is:

```text
smoke_test\arduino_smoke_test\arduino_smoke_test.ino
```

1. Disconnect the DM542 driver, motor, needle mechanism, and other external
   wiring. For this check, use only `Laptop -> USB-C cable -> UNO R4 Minima`.
2. Open the `.ino` file in Arduino IDE.
3. Select **Arduino UNO R4 Minima** and the board's actual COM port.
4. Upload the sketch.
5. Close Arduino Serial Monitor so Python can open the COM port.

The included sketch configures no motor pins and recognizes only `PING` and
`INFO`. Uploading it replaces whatever firmware is currently on the Arduino.
Before later needle operation, restore and verify the appropriate production
firmware from this repository.

List the ports:

```powershell
python smoke_test\03_smoke_arduino.py --list-ports
```

Run the test, replacing `COM3` with the port shown on the offline laptop:

```powershell
python smoke_test\03_smoke_arduino.py --port COM3
```

Expected exchange:

```text
TX: PING
RX: PONG ARDUINO_SMOKE_TEST
PASS: the laptop exchanged PING/PONG with the Arduino.
```

If a different response appears, the port is communicating but different
firmware is loaded. If no response appears, check that the USB cable supports
data, the correct board and port were selected, the upload succeeded, the baud
rate is 115200, and Serial Monitor is closed.

## What a PASS means

- Chemyx PASS: the selected COM port accepted a read-only command and replied.
- NMR PASS: the local RPC server answered all three read-only HTTP requests.
- Arduino PASS: the uploaded smoke firmware exchanged the expected PING/PONG.

A PASS isolates later failures from basic laptop-to-instrument communication.
It does not validate tubing, pump direction, delivered volume, NMR acquisition,
spectral quality, Arduino motion wiring, stepper motion, or mechanical safety.
