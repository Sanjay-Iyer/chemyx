"""
conftest.py — makes the repo root importable so `import pump` / `import config`
work no matter where pytest is launched from.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
