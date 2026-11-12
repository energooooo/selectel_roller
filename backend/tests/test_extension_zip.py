"""ZIP расширения: версия из manifest, одна папка внутри, без скрытых файлов, кэш по изменениям."""
from __future__ import annotations

import io
import json
import zipfile

from app import extension_zip


def test_zip_from_folder(tmp_path, monkeypatch):
    ext = tmp_path / "chrome-extension"
    (ext / "icons").mkdir(parents=True)
    (ext / "manifest.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
    (ext / "popup.js").write_text("// ok", encoding="utf-8")
    (ext / "icons" / "i.png").write_bytes(b"png")
    (ext / ".DS_Store").write_bytes(b"junk")
    monkeypatch.setattr(extension_zip, "CANDIDATES", (tmp_path / "missing", ext))
    extension_zip._cache.clear()

    data, version = extension_zip.build()
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert version == "9.9.9"
    assert sorted(names) == ["toffee-roller-extension/icons/i.png", "toffee-roller-extension/manifest.json",
                             "toffee-roller-extension/popup.js"]
    assert extension_zip.build()[0] is data  # без изменений — из кэша

    (ext / "background.js").write_text("// new", encoding="utf-8")
    assert "toffee-roller-extension/background.js" in zipfile.ZipFile(io.BytesIO(extension_zip.build()[0])).namelist()


def test_zip_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(extension_zip, "CANDIDATES", (tmp_path / "nope",))
    extension_zip._cache.clear()
    try:
        extension_zip.build()
    except LookupError:
        return
    raise AssertionError("ожидали LookupError")
