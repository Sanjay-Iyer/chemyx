"""Legacy Python template for local hardware settings.

New Chemyx setup should usually use configs/chemyx.local.example.json copied to
configs/chemyx.local.json. This file remains supported for older local clones:
copy it to config_local.py in the repo root and edit it for the laptop connected
to the instruments. config_local.py is ignored by git.
"""

# Work-laptop values from the successful Chemyx Fusion 4000X test.
PORT = "COM4"
BAUD_RATE = 115200
CHANNEL = 1

# Syringe and smoke-test values used in deploy/infuse_withdraw.py.
DIAMETER = 28.6
DEFAULT_RATE = 2.0
DEFAULT_VOLUME = 1.5

# Keep first-light small even if the main smoke test uses larger values.
FIRST_LIGHT_VOLUME = 0.05
FIRST_LIGHT_RATE = 0.5

# Optional NMR defaults. The archived working NMR script used this direct
# ethernet address and the NMReady/Nanalysis RPC server on port 5000.
NMR_HOST = "169.254.30.54"
NMR_PORT = 5000
NMR_DEFAULT_ROUTE = "iflow"
NMR_TARGET_PPM = 6.1
NMR_DEFAULT_EXPERIMENT = "1D"
NMR_DEFAULT_SCANS = 2
NMR_DEFAULT_RECEIVER_GAIN = 12.0
NMR_DEFAULT_AUTO_GAIN = False
NMR_DEFAULT_SOLVENT = "Toluene"
NMR_DEFAULT_SPECTRAL_CENTER = 5.0
NMR_DEFAULT_SWEEP_WIDTH = 20.0
