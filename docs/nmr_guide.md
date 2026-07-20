# NMR Guide

NMR RPC communication is isolated in `chemyx_lab/instruments/nmr.py`.

## Configuration

Set the NMR endpoint in ignored local machine config:

```yaml
nmr:
  host: "REPLACE_WITH_NMR_IP"
  port: 5000
  scheme: http
  timeout_seconds: 10.0
  poll_seconds: 2.0
  max_wait_seconds: 300.0
```

Workflow settings such as scans and receiver gain live in
`configs/experiments/01_first_real_chemyx_nmr.yaml`.

## Baseline Route

Workflow 01 uses the iFlow route:

```text
GET /interfaces/iFlow/Settings/1D
GET /interfaces/iFlow/ExperimentSettings
PUT /interfaces/iFlow/Settings/1D
PUT /interfaces/iFlow/RunExperiment
GET /interfaces/iFlow/ExperimentStatus
```

## Troubleshooting

- Cannot connect: verify network interface, NMR host, port, and RPC setting.
- Timeout: increase `nmr.timeout_seconds` or `nmr.max_wait_seconds`.
- Unexpected payload: save the raw response and inspect before changing parser
  behavior.
- Hardware validation must be performed on the work laptop only.
