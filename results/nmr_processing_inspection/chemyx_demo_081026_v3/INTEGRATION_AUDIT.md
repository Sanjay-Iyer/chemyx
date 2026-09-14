# Integration audit

1. **Stage-1 rule:** the detected center is integrated over ± max(detected linewidth, 0.015 ppm) on the regional quantitative trace after its clipped polynomial detrend. The reported area is the positive-clipped trapezoidal area.
2. **Variable width:** yes. Across this series the full Stage-1 span ranges from 0.0000 to 0.1390 ppm (including zero for the non-detection if shown in the table).
3. **Multiplet exclusion:** the Stage-1 bounds can exclude visible neighboring product signal because they track one detected envelope rather than the fixed 5.70–5.90 ppm window.
4. **Boundary signal:** see `06_integration_audit/integration_master_table.csv` and `E_stage1_boundary_signal_vs_time.png`; the reference lines at 1, 5, 10, and 20% are visual aids, not QC thresholds.
5. **Aug. 10 5:15 pull:** Stage-1 30.69, fixed-window 33.78, and left-line 5.82 differ because they measure, respectively, a linewidth-dependent detected envelope, the full fixed product window, and only the highest-ppm multiplet line above a local valley chord. Its low/high ppm Stage-1 boundaries are 5.7259/5.8574 ppm with signals 92.7/-26.4 a.u.
6. **Trend agreement:** Stage-1 versus fixed-window Pearson r=0.964.
7. **Left-line trend:** Stage-1 versus left-line Pearson r=0.368; it does not reproduce the same time profile.
8. **Day-2 decrease:** changing Stage-1 width/boundaries can contribute, but cannot by itself establish a chemical explanation; compare the fixed-window and boundary plots.
9. **Internal consistency:** the fixed window is the most geometrically consistent whole-product measure across time, while the left-line chord is locally baseline-robust but measures only one component. Stage-1 retains detection specificity but changes its physical span.
10. **Future work:** multiplet fitting/deconvolution is worth investigating as a separate validated method; it was not introduced here.

The collector shading bug is corrected in code: the fill now runs from the local valley-to-valley chord to the positive spectrum residual, matching the numerical `left_peak_area`, rather than filling to zero.
