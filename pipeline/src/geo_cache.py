"""Disk cache for expensive geometry-engine indexes (street graphs, intersection postings)."""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

from paths import cache_dir

_PICKLE_PROTOCOL = 5
_CACHE_VERSION = 1


def cache_enabled() -> bool:
    raw = os.environ.get('GEO_CACHE', '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def file_fingerprint(path: Path) -> str:
    """Stable key from path mtime + size (fast; invalidates on replace)."""
    try:
        st = path.stat()
    except OSError:
        return 'missing'
    return f'{st.st_mtime_ns}_{st.st_size}'


def _cache_path(name: str) -> Path:
    cache_dir().mkdir(parents=True, exist_ok=True)
    return cache_dir() / name


def _load_pickle(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        with path.open('rb') as f:
            payload = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError):
        return None
    if not isinstance(payload, dict) or payload.get('version') != _CACHE_VERSION:
        return None
    return payload


def _save_pickle(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('wb') as f:
        pickle.dump(payload, f, protocol=_PICKLE_PROTOCOL)
    tmp.replace(path)


def load_street_graphs(streets_path: Path) -> dict | None:
    if not cache_enabled():
        return None
    fp = file_fingerprint(streets_path)
    path = _cache_path(f'street_graphs_{fp}.pkl')
    payload = _load_pickle(path)
    if payload is None or payload.get('fingerprint') != fp:
        return None
    graphs = payload.get('graphs')
    return graphs if isinstance(graphs, dict) else None


def save_street_graphs(streets_path: Path, graphs: dict) -> None:
    if not cache_enabled():
        return
    fp = file_fingerprint(streets_path)
    path = _cache_path(f'street_graphs_{fp}.pkl')
    _save_pickle(path, {
        'version': _CACHE_VERSION,
        'fingerprint': fp,
        'graphs': graphs,
    })


def load_intersection_postings(
    intersections_path: Path,
    csv_path: Path,
) -> dict[str, tuple[int, ...]] | None:
    if not cache_enabled():
        return None
    fp_ix = file_fingerprint(intersections_path)
    fp_csv = file_fingerprint(csv_path)
    path = _cache_path(f'ix_postings_{fp_ix}_{fp_csv}.pkl')
    payload = _load_pickle(path)
    if payload is None:
        return None
    if payload.get('fingerprint_ix') != fp_ix or payload.get('fingerprint_csv') != fp_csv:
        return None
    postings = payload.get('postings')
    if not isinstance(postings, dict):
        return None
    return postings


def save_intersection_postings(
    intersections_path: Path,
    csv_path: Path,
    postings: dict[str, tuple[int, ...]],
) -> None:
    if not cache_enabled():
        return
    fp_ix = file_fingerprint(intersections_path)
    fp_csv = file_fingerprint(csv_path)
    path = _cache_path(f'ix_postings_{fp_ix}_{fp_csv}.pkl')
    _save_pickle(path, {
        'version': _CACHE_VERSION,
        'fingerprint_ix': fp_ix,
        'fingerprint_csv': fp_csv,
        'postings': postings,
    })
