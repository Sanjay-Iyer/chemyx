# Command Mapping

The maintained command path is now:

```text
scripts/                 runnable workflows and checks
chemyx_lab/pump.py       reusable Chemyx serial wrapper
chemyx_lab/nmr.py        local DX/JDX analysis
chemyx_lab/nmr_rpc.py    NMReady/Nanalysis RPC client
configs/chemyx.local.json optional local Chemyx port/baud/channel/syringe settings
configs/nmr.local.json   optional local NMR IP/scans/gain overrides
```

The old one-command example folders were archived locally under `archive/` and
are ignored by git.

## Pump Command Facts

| Operation | Python method | Wire command |
|---|---|---|
| Set units | `pump.set_units(0)` | `set units 0` |
| Set diameter | `pump.set_diameter(28.6)` | `set diameter 28.6` |
| Set rate | `pump.set_rate(2.0)` | `set rate 2.0` |
| Infuse | `pump.infuse(1.5, start_delay=0)` | `set volume 1.5`, then `start 0` |
| Withdraw | `pump.withdraw(1.5, start_delay=0)` | `set volume -1.5`, then `start 0` |
| Stop | `pump.stop()` | `stop` |
| Pause | `pump.pause()` | `pause` |
| Help | `pump.help()` | `help` |

## Key Rules

- Commands are ASCII and carriage-return terminated: `\r`.
- Channel 1 commands are prefixed with `1`, for example `1 set rate 2.0`.
- Positive volume means infuse.
- Negative volume means withdraw.
- The pump echo is checked after set commands so rejected values are caught.
