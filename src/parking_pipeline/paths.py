from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def data_path(filename: str) -> Path:
    return DATA_DIR / filename


def cache_dir() -> Path:
    return DATA_DIR / '.geo_cache'
