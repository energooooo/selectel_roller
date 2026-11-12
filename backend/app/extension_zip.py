"""ZIP браузерного расширения — всегда из той версии, что лежит рядом с сервером.

В Docker папка chrome-extension попадает в образ при сборке (additional_contexts),
поэтому после каждого деплоя панель отдаёт ровно задеплоенную версию. Локально берётся
папка из репозитория. Архив собирается в памяти и пересобирается, когда файлы меняются.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.core.config import BACKEND_DIR

CANDIDATES = (BACKEND_DIR / "extension", BACKEND_DIR.parent / "chrome-extension")
TOP = "toffee-roller-extension"  # папка внутри архива — её и выбирают в «Загрузить распакованное»
_cache: dict[str, object] = {}


def source_dir() -> Path | None:
    return next((d for d in CANDIDATES if (d / "manifest.json").is_file()), None)


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts))


def build() -> tuple[bytes, str]:
    """(байты zip, версия из manifest.json). LookupError — расширения рядом с сервером нет."""
    root = source_dir()
    if root is None:
        raise LookupError("extension sources not found")
    files = _files(root)
    signature = (str(root), len(files), max((p.stat().st_mtime_ns for p in files), default=0))
    if _cache.get("signature") == signature:
        return _cache["data"], _cache["version"]  # type: ignore[return-value]
    version = str(json.loads((root / "manifest.json").read_text(encoding="utf-8")).get("version", "0"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, f"{TOP}/{p.relative_to(root).as_posix()}")
    _cache.update(signature=signature, data=buf.getvalue(), version=version)
    return buf.getvalue(), version
