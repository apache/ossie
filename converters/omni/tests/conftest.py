import pathlib
import sys

# Make the converter modules in ../src importable from the tests.
_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))
