"""Run the isolated API suite using this project's environment on any platform."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
raise SystemExit(subprocess.call([str(python), "-m", "pytest", "tests", "-q"], cwd=root))
