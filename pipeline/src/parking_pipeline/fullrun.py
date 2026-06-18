"""Execute pipeline stages and failure analysis in sequence."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PIPELINE_ROOT / 'scripts'

PIPELINE_MODULES = (
    'parking_pipeline.clean_data',
    'parking_pipeline.parse_schedule',
    'parking_pipeline.parse_between',
    'parking_pipeline.resolve_rows',
    'parking_pipeline.geometry_engine',
)

ANALYSIS_SCRIPTS = (
    'analyze_intersection_failures.py',
    'analyze_geometry_failures.py',
    'analyze_street_failures.py',
    'triage_failure_ledger.py',
)


def run_module(module: str) -> int:
    """Run a pipeline stage module and return its exit code."""
    try:
        print(f'Running {module}...')
        result = subprocess.run(
            [sys.executable, '-m', module],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f'✓ {module} completed successfully')
        if result.stdout:
            print(result.stdout)
        return 0
    except subprocess.CalledProcessError as exc:
        print(f'✗ {module} failed with exit code {exc.returncode}')
        if exc.stdout:
            print('STDOUT:', exc.stdout)
        if exc.stderr:
            print('STDERR:', exc.stderr)
        return exc.returncode


def run_script(script_name: str) -> int:
    """Run an analysis script from pipeline/scripts/."""
    script_path = SCRIPTS / script_name
    try:
        print(f'Running {script_name}...')
        result = subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f'✓ {script_name} completed successfully')
        if result.stdout:
            print(result.stdout)
        return 0
    except subprocess.CalledProcessError as exc:
        print(f'✗ {script_name} failed with exit code {exc.returncode}')
        if exc.stdout:
            print('STDOUT:', exc.stdout)
        if exc.stderr:
            print('STDERR:', exc.stderr)
        return exc.returncode


def main() -> int:
    print('Starting full run of parking pipeline...\n')

    failed: list[str] = []
    for module in PIPELINE_MODULES:
        if run_module(module) != 0:
            failed.append(module)
        print()

    for script_name in ANALYSIS_SCRIPTS:
        if run_script(script_name) != 0:
            failed.append(script_name)
        print()

    print('=' * 50)
    if not failed:
        print('All scripts completed successfully!')
        return 0
    print(f'Failed: {", ".join(failed)}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
