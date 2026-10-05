"""Presentation labels only; historical method identifiers remain unchanged."""
DX_METADATA_HELP = 'Phase correction using PHC0/PHC1 values stored in the NMReady .dx file metadata.'
DISPLAY = {'unphased': 'Unphased FFT', 'production': 'DX metadata phase',
           'acme': 'ACME', 'peak_minima': 'Peak minima',
           'combined_objective_v1': 'Combined', 'symmetry_objective': 'Symmetry',
           'ernst_integral_p0': 'Ernst P0', 'deep_phaser': 'DEEP Phaser',
           'manual': 'Manual/current'}
LEGEND = {**DISPLAY, 'unphased': 'Unphased FFT [before phase correction]',
          'production': 'DX metadata phase [PHC0/PHC1 from .dx]',
          'manual': 'Manual/current [live]'}
TABLE_DISPLAY = {**DISPLAY, 'production': 'DX metadata'}


def display_review_status(status):
    """Render old saved status strings without rewriting their provenance."""
    return status.replace('PRODUCTION', 'DX METADATA PHASE').replace('Production', 'DX metadata phase')
