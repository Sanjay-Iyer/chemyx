# Troubleshooting

## Conda

If `conda` is not recognized, open an Anaconda/Miniconda prompt or add Conda to
PATH, then retry:

```powershell
conda run -n ai python --version
```

## YAML

If YAML loading fails, install dependencies in the `ai` environment:

```powershell
conda run -n ai python -m pip install -r requirements.txt
```

## Config

Use validation before hardware:

```powershell
conda run -n ai python -B scripts\02_si6_automated_nmr.py --validate-only
```

Error messages should name the bad field, value, expected type or range, and
config path.

## Chemyx

- Port not found: check cable, power, and local machine config.
- Access denied: close serial monitors and vendor applications.
- Echo mismatch: the pump likely rejected a value.

## NMR

- Connection failure: verify network, host, port, and RPC enablement.
- Timeout: check acquisition duration and `max_wait_seconds`.
- Result parsing warning: inspect the saved payload before changing code.
