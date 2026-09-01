"""Execute pipeline stages and failure analysis in sequence."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def _isolated_argv() -> Iterator[None]:
    """Keep parking-run flags from leaking into stage argparse parsers."""
    old = sys.argv
    sys.argv = [old[0]]
    try:
        yield
    finally:
        sys.argv = old


def apply_refresh_env(*, skip: bool, force: bool, verbose: bool, workers: str | None = None) -> None:
    """Propagate parking-run flags to child stages via environment variables."""
    if skip:
        os.environ['PARKING_SKIP_OPENDATA'] = '1'
    if force:
        os.environ['PARKING_FORCE_OPENDATA'] = '1'
    if verbose:
        os.environ['PARKING_VERBOSE'] = '1'
    if workers is not None:
        os.environ['GEO_WORKERS'] = str(workers)


def run_module(module: str) -> int:
    """Run a pipeline stage by importing and calling its main()."""
    try:
        log.info('Running %s...', module)
        mod = importlib.import_module(module)
        main_fn = getattr(mod, 'main')
        with _isolated_argv():
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


def ensure_auxiliary_datasets(*, force: bool = False, skip: bool = False) -> None:
    """Ensure auxiliary geospatial layers (municipal boundaries, permit zones, hydrants) are available."""
    try:
        from .municipal_rules import ensure_former_municipality_boundaries
        ensure_former_municipality_boundaries(force=force, skip=skip)
    except Exception as exc:
        log.warning('Could not refresh municipal boundaries: %s', exc)

    try:
        from .permit_zones import ensure_permit_parking_areas
        ensure_permit_parking_areas(force=force, skip=skip)
    except Exception as exc:
        log.warning('Could not refresh permit parking areas: %s', exc)

    try:
        from .hydrants import ensure_fire_hydrants
        ensure_fire_hydrants(force=force, skip=skip)
    except Exception as exc:
        log.warning('Could not refresh fire hydrants: %s', exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_verbose_arg(parser)
    parser.add_argument(
        '--keep-going',
        action='store_true',
        help='Continue remaining stages after a failure (default: stop at first failed stage)',
    )
    parser.add_argument(
        '--skip-refresh',
        action='store_true',
        help='Use the existing local bylaw dump; do not contact Toronto Open Data',
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Re-download the bylaw dump even if local CKAN metadata still matches',
    )
    parser.add_argument(
        '-w', '--workers',
        type=str,
        default=None,
        help='Worker processes for geometry slicing (e.g. 4, auto; default: GEO_WORKERS env or 0 for sequential)',
    )
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)
    apply_refresh_env(
        skip=args.skip_refresh,
        force=args.force_refresh,
        verbose=args.verbose,
        workers=args.workers,
    )

    log.info('Starting full run of parking pipeline...\n')
    ensure_auxiliary_datasets(force=args.force_refresh, skip=args.skip_refresh)

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
