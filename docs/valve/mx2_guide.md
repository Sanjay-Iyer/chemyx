# MX Series II Valve Guide

Valve support is separate from workflow 01. The implementation is
`chemyx_lab/instruments/valve.py`; exact framing and safety behavior are covered
by `tests/test_valve.py`.

The known MXX777-601 has six fluidic ports but only two selectable positions.
The driver therefore defaults to positions 1 and 2 and rejects nonexistent
positions before sending bytes. Commands and responses are CR-terminated.

Configure the work-laptop endpoint in the shared machine YAML:

```yaml
valve:
  serial_port: "REPLACE_WITH_VALVE_COM_PORT"
  baud_rate: 19200
  positions: 2
  timeout_seconds: 1.0
  motion_timeout_seconds: 10.0
```

Offline mock check:

```powershell
conda run -n ai python scripts\diagnostics\06_check_mx_valve.py --mock
```

Work-laptop diagnostics, in order:

```powershell
conda run -n ai python scripts\diagnostics\01_list_serial_ports.py
conda run -n ai python scripts\diagnostics\05_inspect_mx_controller.py --port REPLACE_WITH_VALVE_COM_PORT --identify
conda run -n ai python scripts\diagnostics\06_check_mx_valve.py --machine-config configs\machines\00_machine.local.yaml
```

If home succeeds but position commands are ignored, inspect `command-mode`. BCD
mode is required for the USB command path. The `--set-bcd` option stores BCD
mode; the controller must then be power-cycled before the setting takes effect.
Only perform this procedure on the work laptop with the physical setup checked.

Protocol evidence is retained in `docs/reference/valve/titan_uart_protocol.pdf`
and the checksum archive.
