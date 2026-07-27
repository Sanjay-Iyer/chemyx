# Revised June 9 chemical-shift reference audit

## 1. Audit scope and preservation

This audit reprocessed 8 untouched JCAMP-DX FIDs. SHA-256 hashes and
metadata are in `metadata.csv`. The production axis is preserved; diagnostic
models are overlays, not silent mutations.

## 2. What is actually in the June 9 data

One resolved peak family is detected in 7/8 spectra at
5.78530 ppm (range 5.77695–5.78775)
on the metadata-derived axis. The 09:00 spectrum has no QC-passed member.
There is no second family at 6.1 ppm in these raw FIDs.

## 3. Instrument metadata

The files report an NMReady 60 observe frequency near
60.550651 MHz, a 1250 Hz sweep,
O1P/spectral center 5.0 ppm, 8192 acquired complex points, and 65536 processed
points. The metadata says `Toluene`; `$LOCKOFFSET` is about 2.08 ppm. That is
evidence for the selected instrument profile, not independent proof of the
sample's isotopic composition or an absolute internal standard.

## 4. Reference models tested

Three source-qualified hypotheses were tested in both methyl and aromatic
regions:

- protonated toluene, low-field/neat: 2.09 and 7.00 ppm;
- protonated toluene under a dilute CDCl3 example: 2.34 and about 7.19 ppm;
- residual proton signals associated with toluene-d8: 2.089 and 7.014 ppm.

Expected values are hypotheses tied to measurement conditions, not universal
toluene constants.

## 5. Multi-region fitting and QC

Each methyl and aromatic region is independently restricted-window peak-picked
and fitted with a three-point quadratic maximum. `reference_region_fits.csv`
records observed/expected ppm, shift in ppm and Hz, height, width, SNR,
prominence SNR, fit quality, overlap risk, and failure reason.

## 6. Candidate-model results

- Low-field protonated-toluene median proposed shift:
  +0.01098 ppm;
  regional disagreement
  0.00147 ppm.
- Dilute-CDCl3 example median proposed shift:
  +0.23098 ppm;
  disagreement
  0.05882 ppm.
- Toluene-d8 residual model median proposed shift:
  +0.01748 ppm;
  disagreement
  0.01618 ppm.

The low-field protonated-toluene model is the best numerical match to the
metadata axis. The very intense toluene bands are also more consistent with
ordinary protonated toluene as a major component than with trace residual
protons in clean toluene-d8, but intensity alone is not definitive proof.

## 7. Fail-closed decision

All applied model shifts are exactly 0.0 ppm because the repository contains no
independent sample-preparation record confirming protonated toluene versus
toluene-d8, and no verified TMS/internal-standard identity. A candidate can fit
spectrally and still fail physical-identity QC. See `reference_models.csv`.

## 8. PDF image comparison

The supplied raster was read approximately as target 6.10,
methyl 2.40, and aromatic 7.30 ppm,
each with at least ±0.08 ppm graphical
uncertainty. Relative to the raw-data metadata axis, the consensus image offset
is +0.315 ppm (+19.07 Hz). Exact values
cannot be recovered from a screenshot; the original vector PDF or its processed
coordinate table is required.

## 9. Is the PDF peak a different physical peak?

No separate peak is needed to explain it. On the image-read coordinates,
target-minus-methyl is 3.700 ppm.
In the raw-data spectra it is 3.706 ppm.
Those invariant separations agree within screenshot-reading uncertainty.
The PDF therefore most likely displays the same physical 5.78 ppm family on a
uniformly shifted horizontal axis.

## 10. Why phase, FFT, and apodization cannot explain 5.78 to 6.10

Phasing changes real/imaginary mixing and line shape, and apodization changes
resolution/ringing. Neither legitimately translates every resonance by a
uniform chemical-shift offset. A 5.78-to-6.10 change is a reference/axis
operation (about +0.32 ppm), not peak creation by Fourier processing.

## 11. Legacy-script arithmetic

The legacy formula is `new_axis = old_axis + (1.97 - selected_peak)`. If the
selected peak were 2.40 ppm, the shift would be -0.43 ppm, not +0.32 ppm.
With the observed June 9 methyl maximum near 2.079
ppm, it would apply about -0.109 ppm. Therefore
that exact equation cannot explain a rightward +0.32 ppm display shift. The
1.97 ppm constant is labeled DMAc in the legacy code and must not be treated
as a general toluene or acetone-d6 reference.

## 12. Ringing/truncation assessment

The 10:45 trace contains strong oscillatory structure beside the solvent line,
consistent with truncation/ringing. `14_ringing_window_sensitivity.png` and
`15_fid_endpoint.png` compare the actual raw-FID result with a half-cosine
taper. The legacy stale-variable bug does discard its calculated windows, so
it can worsen ringing. The screenshot alone, however, cannot uniquely assign
all oscillations to that one bug; phase, FID endpoint, and instrument digital
filtering can contribute.

## 13. Production recommendation

Keep the metadata-derived axis as the production default. Preserve original
and candidate axes side by side. Only enable a model after documenting sample
solvent/isotopic form or adding a verified internal standard, requiring at
least two agreeing reference regions, and reviewing aromatic-envelope overlap.
Never calibrate from the first index above a global intensity threshold.

## 14. Final scientific conclusion

The June 9 FIDs contain one reproducible physical family near 5.78 ppm on the
current metadata axis. The supplied PDF most likely shows that same family near
6.0–6.1 ppm after a global display/reference shift. The current processing is
internally consistent and independently supported by the observed ~2.08 and
~6.99 ppm toluene regions, but absolute assignment remains explicitly
conditional until sample identity or an internal standard is documented.

### Source-qualified reference context

- Nanalysis NMReady 60/100 manual (manual chemical-shift entry and processed
  PDF export):
  https://www.wpi.edu/sites/default/files/2025-07/Nanalysis-100-60-user-manual.pdf
- Thermo Fisher low-field neat-toluene teaching spectrum (2.09, 7.00 ppm):
  https://assets.thermofisher.com/TFS-Assets/CAD/Reference-Materials/pS45-pS80-Simple-Distillation-of-Cyclohexane-and-Toluene.pdf
- PubChem/NMRShiftDB toluene spectrum under different conditions:
  https://pubchem.ncbi.nlm.nih.gov/compound/toluene
- Residual-solvent reference context:
  https://chem.ch.huji.ac.il/nmr/whatisnmr/chemshift.html
