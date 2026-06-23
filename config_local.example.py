"""
config_local.example.py — TEMPLATE for per-machine settings.

HOW TO USE
----------
1. Copy this file to  config_local.py   (same folder):
       Windows : copy config_local.example.py config_local.py
       Mac/Lin : cp   config_local.example.py config_local.py
2. Edit the values below for THIS machine.
3. That's it — config.py imports config_local.py automatically and these values
   win over the committed defaults and any environment variables.

config_local.py is listed in .gitignore, so your machine-specific port never
gets committed or pushed.  Each machine keeps its own copy.

(You only need this for REAL hardware.  The mock/dry-run path works without it.)
"""

# Serial port for THIS machine.  Find it with hardware_bringup/list_ports.py.
#   Windows : "COM5"   Mac : "/dev/tty.usbserial-XXXX"   Linux : "/dev/ttyUSB0"
PORT = "COM3"

# Baud rate shown on the pump's screen (commonly 9600 or 38400).
BAUD_RATE = 9600

# 0 = default/both, 1 = channel 1, 2 = channel 2 (Fusion 4000X is dual-channel).
CHANNEL = 0

# Optional: override the syringe diameter for this rig (mm).
# DIAMETER = 4.5
