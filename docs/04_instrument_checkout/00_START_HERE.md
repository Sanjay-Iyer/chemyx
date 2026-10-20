# 04 — Standalone instrument checkout

[All documentation](../00_START_HERE.md)

Read [01 — Bench checkout](01_bench_checkout.md). It tests Arduino, both pump
channels and a 2-scan NMR acquisition independently. It is not the chemistry
experiment and is not the fixed-time 8-scan NMR worker.

| Item | File |
|---|---|
| Python | [03_simple_instrument_checkout.py](../../scripts/03_simple_instrument_checkout.py) |
| Checkout settings | [si6_simple_instrument_check.yaml](../../configs/checkouts/si6_simple_instrument_check.yaml) |
| Machine | [COM4](../../configs/machines/si6_real_COM4.yaml) or [COM6](../../configs/machines/si6_real_COM6.yaml) |
| Update instructions | [02 — Offline deployment](02_offline_deployment.md) |

No-hardware configuration check:

```powershell
python scripts\03_simple_instrument_checkout.py --validate-config --config configs\checkouts\si6_simple_instrument_check.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

WORK only, after preparing the isolated bench paths described in document 01:

```powershell
python scripts\03_simple_instrument_checkout.py --live --config configs\checkouts\si6_simple_instrument_check.yaml --machine-config configs\machines\si6_real_COM4.yaml
```

For the current full experiment and its own `--nmr-check`, use
[03 — Fixed-time experiment](../03_fixed_time_experiment/00_START_HERE.md).
