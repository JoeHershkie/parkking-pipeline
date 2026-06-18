"""Shared logging configuration for pipeline CLI stages."""

from __future__ import annotations

import logging
import os


def setup_logging(*, verbose: bool | None = None) -> None:
    """Configure root logging for pipeline scripts."""
    if verbose is None:
        verbose = os.environ.get('PARKING_VERBOSE', '').lower() in {'1', 'true', 'yes'}
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(message)s',
        force=True,
    )


def add_verbose_arg(parser) -> None:
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable debug logging',
    )
