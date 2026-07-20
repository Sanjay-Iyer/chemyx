# Configuration

The refactored workflow separates experiment settings from laptop-specific
instrument endpoints.

## Files

Experiment config:

```text
configs/experiments/01_first_real_chemyx_nmr.yaml
```

Machine config template:

```text
configs/machines/00_machine.example.yaml
```

Local machine config, ignored by Git:

```text
configs/machines/00_machine.local.yaml
```

## Precedence

When a value is available in more than one place, the effective order is:

1. Command-line argument.
2. Explicit environment variable.
3. Local machine config.
4. Experiment config.
5. Safe source default.

Machine-specific endpoints such as the Chemyx serial port and NMR host should
come from `00_machine.local.yaml`, environment variables, or CLI flags. They
should not be committed in Python source.

## Experiment Fields

`workflow.sequence`

- Type: list of events.
- Values: `W` withdraw, `N` NMR acquisition, `I` infuse.
- Default in workflow 01: withdraw 5 mL, NMR, infuse 5 mL.

`workflow.pump_extra_seconds`

- Type: number, seconds.
- Default in workflow 01: `2.0`.
- Meaning: extra wait after calculated pump travel time before `stop`.

`workflow.settle_before_nmr_seconds`

- Type: number, seconds.
- Default in workflow 01: `0.0`.
- Meaning: optional wait after withdraw before starting NMR.

`pump.channel`

- Type: integer.
- Allowed values: `0`, `1`, `2`.
- Default in workflow 01: `1`.

`pump.syringe_diameter_mm`

- Type: number, millimeters.
- Allowed range from current code: `0.103` to `40.000`.
- Default in workflow 01: `28.6`.

`pump.units`

- Type: string or unit code.
- Allowed values: `mL/min`, `mL/hr`, `uL/min`, `uL/hr`, or codes `0` to `3`.
- Default in workflow 01: `mL/min`.

`pump.rate_ml_min`

- Type: number.
- Units: mL/min for workflow 01.
- Default in workflow 01: `5.0`.
- Validated against the Chemyx unit-specific ranges in code.

`nmr.route`

- Type: string.
- Allowed values: `iflow`, `experiment`.
- Default in workflow 01: `iflow`.

`nmr.scans`

- Type: integer.
- Default in workflow 01: `2`.

`nmr.receiver_gain`

- Type: number or null.
- Default in workflow 01: `12.0`.

`nmr.auto_gain`

- Type: boolean.
- Default in workflow 01: `false`.

`nmr.spectral_center`

- Type: number, ppm.
- Default in workflow 01: `5.0`.

`nmr.sweep_width`

- Type: number, ppm.
- Default in workflow 01: `20.0`.

`nmr.target_ppm`

- Type: number, ppm.
- Default in workflow 01: `6.1`.

`output.nmr_save_dir`

- Type: path string.
- Default in workflow 01: `results/raw/nmr/generated`.

## Machine Fields

`chemyx.serial_port`

- Type: string.
- Example placeholder: `REPLACE_WITH_COM_PORT` on Windows.
- Required for real Chemyx hardware runs.

`chemyx.baud_rate`

- Type: integer.
- Default template value: `115200`.

`chemyx.timeout_seconds`

- Type: number, seconds.
- Default template value: `2.0`.

`chemyx.response_delay_seconds`

- Type: number, seconds.
- Default template value: `0.2`.
- Meaning: delay after writing a command before reading the Chemyx response.

`nmr.host`

- Type: string.
- Example: the NMR instrument IP address on the work laptop network.
- Required for real NMR RPC runs.

`nmr.port`

- Type: integer.
- Default template value: `5000`.

`nmr.scheme`

- Type: string.
- Default template value: `http`.

`nmr.timeout_seconds`

- Type: number, seconds.
- Default template value: `10.0`.

`nmr.poll_seconds`

- Type: number, seconds.
- Default template value: `2.0`.

`nmr.max_wait_seconds`

- Type: number, seconds.
- Default template value: `300.0`.

`valve.serial_port`, `valve.baud_rate`, and `valve.positions`

- Used only by the numbered MX valve diagnostics, not workflow 01.
- Default template baud: `19200`; default positions: `2`.
- The valve serial port remains a machine-specific local value.

## Safe Commands

Home laptop dry-run:

```powershell
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run
```

Work laptop with local machine config:

```powershell
copy configs\machines\00_machine.example.yaml configs\machines\00_machine.local.yaml
conda run -n ai python scripts\01_first_real_chemyx_nmr.py --dry-run --machine-config configs\machines\00_machine.local.yaml
```

Real hardware should only be run on the work laptop after the dry-run plan
matches the physical setup.
