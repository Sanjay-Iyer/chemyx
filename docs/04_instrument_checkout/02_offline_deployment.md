# 02 — Update the checkout through Git and the WORK drive

[Checkout index](00_START_HERE.md) | [All documentation](../00_START_HERE.md)

The checkout uses the same deployment route as the rest of the repository:
HOME pushes committed source to Git, WORK pulls it, then WORK transfers to the
second WORK laptop by external hard drive. HOME does not make an offline
package. Existing archives are unused and can remain in place.

Follow the [Git and drive update guide](../03_fixed_time_experiment/04_offline_deployment.md),
then use [01 — Bench checkout](01_bench_checkout.md) for this workflow's Python
command and YAML. Any destination dependencies are handled through the existing
WORK-side setup/copy process.
