# Figure variant comparison

| Variant | Scientific question | Strengths | Weaknesses | Best use |
|---|---|---|---|---|
| 01 full baseline overlay | What baseline did ALS place under the phased spectrum? | Direct, compact, full range | Dominant peaks compress subtle baseline detail | Routine audit / SI |
| 02 before/after | How much did baseline subtraction change the spectrum? | Clearest paired comparison; matched and independent scales | Two panels require more space | Best before/after figure |
| 03 baseline magnified | What slope, curvature, and near-zero residual are hidden by autoscaling? | Most sensitive baseline inspection | Intentional peak clipping needs explanation | Troubleshooting / scientific audit |
| 04 processing steps | How does the raw FID become the quantitative spectrum? | Separates time-domain, FFT, phase, and baseline operations | Dense | Teaching / meeting walkthrough |
| 05 full plus regions | How do complete spectra connect to product, starting-material, and silane numbers? | Presentation friendly; distinguishes integration definitions | Less detail on ALS shape | Presentation / compact report |
| 06 ALS sensitivity | Are results robust to reasonable ALS choices? | Quantitative absolute and percent comparisons | Diagnostic rather than publication-ready | Validation / regression |
| 07 phase and baseline | Which change is phase and which is baseline? | Prevents conceptual conflation | Four panels | Troubleshooting / explanation |

## Recommendations

- **Best scientific-audit figure:** Variant 03 plus Variant 06 metrics.
- **Best before/after baseline figure:** Variant 02B matched scale, with 02A available for weak-feature inspection.
- **Best presentation figure:** Variant 05.
- **Best compact routine-report figure:** Variant 01.
- **Best processing explanation:** Variant 04, supplemented by Variant 07.
