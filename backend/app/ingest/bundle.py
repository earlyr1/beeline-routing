from __future__ import annotations

from pathlib import Path

from app.domain.models import Bundle


def save_bundle(bundle: Bundle, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")


def load_bundle(path: Path) -> Bundle:
    return Bundle.model_validate_json(Path(path).read_text(encoding="utf-8"))
