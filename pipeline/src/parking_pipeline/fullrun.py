"""Execute pipeline stages and failure analysis in sequence."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from .log_config import add_verbose_arg, setup_logging

log = logging.getLogger(__name__)

PIPELINE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PIPELINE_ROOT / 'scripts'
SRC_ROOT = Path(__file__).resolve().parents[1]

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
    """Run a pipeline stage by importing and calling its main()."""
    try:
        log.info('Running %s...', module)
        mod = importlib.import_module(module)
        main_fn = getattr(mod, 'main')
        result = main_fn()
        if result is None:
            result = 0
        if result != 0:
            log.error('✗ %s failed with exit code %s', module, result)
            return int(result)
        log.info('✓ %s completed successfully', module)
        return 0
    except Exception:
        log.exception('✗ %s failed', module)
        return 1


def _script_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(SRC_ROOT)
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = src if not existing else f'{src}{os.pathsep}{existing}'
    return env


def run_script(script_name: str) -> int:
    """Run an analysis script from pipeline/scripts/."""
    script_path = SCRIPTS / script_name
    try:
        log.info('Running %s...', script_name)
        result = subprocess.run(
            [sys.executable, str(script_path)],
            check=True,
            capture_output=True,
            text=True,
            env=_script_env(),
        )
        log.info('✓ %s completed successfully', script_name)
        if result.stdout:
            log.info(result.stdout.rstrip())
        return 0
    except subprocess.CalledProcessError as exc:
        log.error('✗ %s failed with exit code %s', script_name, exc.returncode)
        if exc.stdout:
            log.error('STDOUT: %s', exc.stdout.rstrip())
        if exc.stderr:
            log.error('STDERR: %s', exc.stderr.rstrip())
        return exc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    parser.add_argument(
        '--keep-going',
        action='store_true',
        help='Continue remaining stages after a failure (default: stop at first failed stage)',
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)

    log.info('Starting full run of parking pipeline...\n')

    failed: list[str] = []
    stage_failed = False
    for module in PIPELINE_MODULES:
        if run_module(module) != 0:
            failed.append(module)
            stage_failed = True
            if not args.keep_going:
                log.error('Stopping pipeline after failed stage (use --keep-going to continue)')
                break
        log.info('')

    if not stage_failed or args.keep_going:
        for script_name in ANALYSIS_SCRIPTS:
            if run_script(script_name) != 0:
                failed.append(script_name)
            log.info('')

    log.info('=' * 50)
    if not failed:
        log.info('All scripts completed successfully!')
        return 0
    log.error('Failed: %s', ', '.join(failed))
    return 1


if __name__ == '__main__':
    sys.exit(main())
