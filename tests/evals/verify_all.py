"""Run all portable offline checks; never calls the model API."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
COMMANDS = [
    ['tests/evals/run.py', '--mode', 'baseline'],
    ['docs/quality/golden-dataset/validate.py'],
    ['-m', 'unittest', 'discover', '-s', 'tests/evals', '-p', 'test_*.py', '-v'],
    ['tests/evals/run.py', '--mode', 'replay'],
    ['tests/evals/check_live.py'],
    ['tests/evals/check_iteration.py'],
    ['tests/evals/retrieval.py'],
]
for command in COMMANDS:
    subprocess.run([sys.executable, *command], cwd=ROOT, check=True)
print('ALL OFFLINE QUALITY CHECKS PASSED; API calls: 0')
