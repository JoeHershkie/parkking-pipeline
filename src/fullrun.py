import subprocess
import sys


def run_script(script_name):
    """Run a Python script and return its exit code."""
    try:
        print(f"Running {script_name}...")
        result = subprocess.run(
            [sys.executable, script_name],
            check=True,
            capture_output=True,
            text=True
        )
        print(f"✓ {script_name} completed successfully")
        if result.stdout:
            print(result.stdout)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"✗ {script_name} failed with exit code {e.returncode}")
        if e.stdout:
            print("STDOUT:", e.stdout)
        if e.stderr:
            print("STDERR:", e.stderr)
        return e.returncode


def main():
    """Execute all scripts in sequence."""
    scripts = [
        "clean_data.py",
        "parse_between.py",
        "parse_schedule.py",
        "geometry_engine.py",
        "triage_failure_ledger.py"
    ]

    print("Starting full run of all scripts...\n")

    failed_scripts = []
    for script in scripts:
        exit_code = run_script(script)
        if exit_code != 0:
            failed_scripts.append(script)
        print()

    print("=" * 50)
    if not failed_scripts:
        print("All scripts completed successfully!")
        return 0
    else:
        print(f"Failed scripts: {', '.join(failed_scripts)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
