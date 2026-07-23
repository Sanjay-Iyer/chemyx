# Instrument Commands

This reference is derived from the current working code and repository
documentation only. Unverified meanings are labeled unresolved.

## Chemyx Fusion 4000X

Source: `chemyx_lab/instruments/chemyx.py`,
`chemyx_lab/workflows/instrument_operations.py`, and archived baseline evidence
listed in `docs/ARCHIVE_MANIFEST.csv`.

All Chemyx commands are ASCII and are terminated with carriage return (`\r`).
When `channel` is `1` or `2`, commands are prefixed as `<channel> <command>`.
When `channel` is `0`, no prefix is used.

| Operation | Command template | Parameters | Expected response | Physical state change | Used in Workflow 02 |
| --- | --- | --- | --- | --- | --- |
| Set units | `set units <code>` | `0` mL/min, `1` mL/hr, `2` uL/min, `3` uL/hr | Echo containing units label or code | No direct movement | Setup |
| Set syringe diameter | `set diameter <mm>` | `0.103` to `40.000` mm from code validation | Echo `diameter = <value>` | No direct movement | Setup |
| Set rate | `set rate <rate>` | Range depends on units in `chemyx_lab.config.RATE_LIMITS` | Echo `rate = <value>` | No direct movement | Setup and optional move prep |
| Set volume | `set volume <signed_ml>` | Positive infuses, negative withdraws, zero rejected | Echo `volume = <value>` | Arms target volume; movement starts only after `start` | Each pump event |
| Start | `start` | None | Response is read and logged | Starts pump movement | Adapter capability |
| Start with delay | `start 0` | Zero delay | Response is read and logged | Starts pump movement | Workflow 02 |
| Stop | `stop` | None | Response is read and logged | Stops pump movement | After each pump event |
| Pause | `pause` | None | Response is read and logged | Pauses pump movement | Legacy demo |
| Set delay | `set delay <value>` | Unresolved; command exposed by wrapper but unused by Workflow 02 | Response is read | Unresolved | No |
| Set prime rate | `set primerate <value>` | Unresolved; command exposed by wrapper but unused by Workflow 02 | Response is read | Unresolved | No |
| Echo on/off | `echo on`, `echo off` | None | Response is read | No direct movement | No |
| Help | `help` | None | Response is read | No direct movement | No |

Offline example:

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --dry-run
```

## NMR RPC

Source: `chemyx_lab/instruments/nmr.py`,
`docs/reference/nmr_rpc_api/html/rpc-api.md`, and archived baseline evidence
listed in `docs/ARCHIVE_MANIFEST.csv`.

The baseline workflow uses the `iflow` route.

| Operation | HTTP request | Parameters | Expected response | Physical state change | Used in Workflow 02 |
| --- | --- | --- | --- | --- | --- |
| Ping spectrometer | `GET /interfaces/iStatus/PingSpectrometer` | None | JSON/text status | No | Status scripts |
| Read RPC enabled | `GET /interfaces/iStatus/RpcEnabled` | None | JSON/text status | No | Status scripts |
| Read spectrometer status | `GET /interfaces/iStatus/SpectrometerStatus` | None | JSON/text status | No | Status scripts |
| Read iFlow 1D settings | `GET /interfaces/iFlow/Settings/1D` | None | Settings mapping | No | Workflow 02 |
| Set iFlow 1D settings | `PUT /interfaces/iFlow/Settings/1D` | Receiver gain, auto gain, optional export filename | RPC response | Changes NMR acquisition settings | Workflow 01 |
| Read iFlow experiment settings | `GET /interfaces/iFlow/ExperimentSettings` | None | Settings mapping | No | Workflow 01 |
| Run iFlow experiment | `PUT /interfaces/iFlow/RunExperiment` | Scan count, receiver gain, spectral center, sweep width, optional export filename | Run status/payload | Starts NMR acquisition | Workflow 01 |
| Read iFlow experiment status | `GET /interfaces/iFlow/ExperimentStatus` | None | Status mapping | No direct state change | Workflow 01 polling |
| Cancel iFlow experiment | `PUT /interfaces/iFlow/CancelExperiment` | Empty object | RPC response | Stops/cancels acquisition | Not in workflow 01 |
| Read experiment settings | `GET /interfaces/Experiment/<name>/Settings` | Experiment name | Settings mapping | No | Alternate route |
| Start experiment | `PUT /interfaces/Experiment/<name>/Start` | Patched settings mapping | RPC response | Starts NMR acquisition | Alternate route |
| Read experiment status | `GET /interfaces/Experiment/Status` | None | Status mapping | No direct state change | Alternate route polling |
| Read experiment results | `GET /interfaces/Experiment/Results` | None | Result list | No | Alternate route |
| Read experiment result | `GET /interfaces/Experiment/Results/<name>?format=jdx&type=<type>` | Result name, format, data type | JCAMP/text payload | No | Alternate route |

Timeout behavior is controlled by machine config fields `nmr.timeout_seconds`,
`nmr.poll_seconds`, and `nmr.max_wait_seconds`. The home laptop must use
dry-run or mocked tests only; these RPC calls are work-laptop hardware actions
when pointed at a real instrument.
