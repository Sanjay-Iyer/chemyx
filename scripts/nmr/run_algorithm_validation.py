"""Offline June 9 validation entry point; implementation is output-package owned."""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / 'results/100426_algovalidation/code/algorithm_validation.py'), run_name='__main__')
