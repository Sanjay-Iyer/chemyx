# Chemyx Guide

Chemyx communication is isolated in `chemyx_lab/instruments/chemyx.py`.

## Configuration

Set the serial port in ignored local machine config:

```yaml
chemyx:
  serial_port: "REPLACE_WITH_COM_PORT"
  baud_rate: 115200
  timeout_seconds: 2.0
  response_delay_seconds: 0.2
```

## Workflow Commands

Workflow 01 configures the pump, then sends signed target volumes:

```text
set units <code>
set diameter <mm>
set rate <rate>
set volume -<withdraw_ml>
start 0
stop
set volume <infuse_ml>
start 0
stop
```

Commands are ASCII with `\r` termination. Channel `1` or `2` prefixes commands
as `<channel> <command>`.

## Troubleshooting

- Port missing: check cable, driver, and `chemyx.serial_port`.
- Access denied: close vendor software or serial monitors.
- Echo mismatch: verify units, syringe diameter, rate, and volume are accepted
  by the pump.
- No real run should start until the dry-run plan matches the physical setup.
