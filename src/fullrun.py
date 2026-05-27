import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent / 'scripts'


def run_script(script_path: Path) -> int:
    """Run a Python script and return its exit code."""
    try:
        print(f'Running {script_path.name}...')
        result = subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f'✓ {script_path.name} completed successfully')
        if result.stdout:
            print(result.stdout)
        return 0
    except subprocess.CalledProcessError as e:
        print(f'✗ {script_path.name} failed with exit code {e.returncode}')
        if e.stdout:
            print('STDOUT:', e.stdout)
        if e.stderr:
            print('STDERR:', e.stderr)
        return e.returncode


def main() -> int:
    """Execute pipeline stages and failure analysis in sequence."""
    pipeline = [
        ROOT / 'clean_data.py',
        ROOT / 'parse_between.py',
        ROOT / 'parse_schedule.py',
        ROOT / 'geometry_engine.py',
    ]
    analysis = [
        SCRIPTS / 'analyze_intersection_failures.py',
        SCRIPTS / 'analyze_geometry_failures.py',
        SCRIPTS / 'analyze_street_failures.py',
        SCRIPTS / 'triage_failure_ledger.py',
    ]

    print('Starting full run of parking pipeline...\n')

    failed: list[str] = []
    for script_path in pipeline + analysis:
        exit_code = run_script(script_path)
        if exit_code != 0:
            failed.append(script_path.name)
        print()

    print('=' * 50)
    if not failed:
        print('All scripts completed successfully!')
        return 0
    print(f'Failed scripts: {", ".join(failed)}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
