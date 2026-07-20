# Valve Bring-Up Checklist

## Home Laptop

```powershell
conda run -n ai python -m pytest tests\test_valve.py
conda run -n ai python scripts\diagnostics\06_check_mx_valve.py --mock
conda run -n ai python scripts\diagnostics\06_check_mx_valve.py --mock --mock-level-logic --motion-timeout 2
```

These commands must not open a real serial port.

## Work Laptop

1. Copy `configs/machines/00_machine.example.yaml` to the ignored
   `configs/machines/00_machine.local.yaml` and set the valve serial port.
2. Close vendor software that may own the port.
3. List ports with diagnostic 01.
4. Run diagnostic 05 with `--identify` to read status, firmware, profile,
   command mode, and last error.
5. Run diagnostic 06. It reads identity/status, verifies invalid-position
   rejection, homes, and toggles 1 -> 2 -> 1 -> 2 with readback.
6. If command mode is not BCD, rerun diagnostic 06 with `--set-bcd`, power-cycle
   the controller, then rerun without `--set-bcd`.

```powershell
conda run -n ai python scripts\diagnostics\01_list_serial_ports.py
conda run -n ai python scripts\diagnostics\05_inspect_mx_controller.py --port REPLACE_WITH_VALVE_COM_PORT --identify
conda run -n ai python scripts\diagnostics\06_check_mx_valve.py --machine-config configs\machines\00_machine.local.yaml
```

Do not run movement commands until the attached valve model, selectable
positions, tubing, and safe physical path are confirmed.
