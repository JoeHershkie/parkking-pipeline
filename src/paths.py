from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


def data_path(filename: str) -> Path:
    return DATA_DIR / filename
