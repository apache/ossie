"""Shared test setup: make validation/validate.py importable as `validate`"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
