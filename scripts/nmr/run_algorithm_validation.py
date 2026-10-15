"""Offline June 9 validation entry point; maintained source-package adapter."""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / 'chemyx_lab/testing/nmr_validation/algorithm_validation.py'), run_name='__main__')
