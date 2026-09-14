# Global baseline-method comparison

All methods branch from the identical stored-phase, metadata-axis spectrum. The local valley-to-valley integration chord is not a global baseline method.

| Method | Description | Main assumption |
|---|---|---|
| none | No global correction control | Regional/local integration baselines handle remaining background |
| production AsLS | lambda 1e6, p 0.001, 10 iterations | Most data are background and positive resonances should receive low fitting weight |
| ABD polynomial 1–3 | ABD-selected low-variation points, 128 sections, noise factor 3, 60-point window | A low-order global polynomial describes background |
| arPLS | lambda 1e7, logistic reweighting | Negative residual distribution estimates background adaptively |

Maximum absolute change versus production over the six acquisitions:

| Method | Fixed product | Starting material | Silane |
|---|---:|---:|---:|
| `abd_polynomial_degree1` | 170.2% | 50.1% | 25.8% |
| `abd_polynomial_degree2` | 170.2% | 50.1% | 25.8% |
| `abd_polynomial_degree3` | 170.2% | 50.1% | 25.8% |
| `arpls_lambda1e7` | 38.6% | 38.7% | 21.0% |
| `none` | 170.2% | 50.1% | 25.8% |
| `production_asls_lambda1e6_p0.001` | 0.0% | 0.0% | 0.0% |

Visual review shows that production AsLS follows substantial broad/dispersive structure near the dominant solvent resonances. Its correction is therefore visually large in those regions, while the small 5.8 ppm target is affected much less in absolute intensity but can change materially in percentage terms when weak. The flattest-looking result is not automatically the most defensible quantitative result: broad genuine signal can be absorbed by a penalized baseline. The fixed-window and Stage-1 metrics should be treated as baseline-sensitive for low-SNR spectra; the local left-line integral is less sensitive in this tested set.
