from pathlib import Path
from typing import Callable
import hashlib

from ..models import ManifestEntry
from ..utils import sha256_file


def build_manifest(source: Path, verify_sha256: bool = True,
                   progress: Callable[[str], None] | None = None) -> list[ManifestEntry]:
    source = source.resolve()
    entries: list[ManifestEntry] = []

    if source.is_file():
        files = [(source, source.name)]
    elif source.is_dir():
        files = []
        for p in sorted(source.rglob("*")):
            if p.is_file():
                files.append((p, p.relative_to(source).as_posix()))
    else:
        raise FileNotFoundError(source)

    for idx, (p, rel) in enumerate(files, 1):
        if progress:
            progress(f"生成文件清单 {idx}/{len(files)}：{rel}")
        digest = sha256_file(p) if verify_sha256 else ""
        entries.append(ManifestEntry(p, rel, p.stat().st_size, digest))
    return entries


def manifest_digest(entries: list[ManifestEntry]) -> str:
    h = hashlib.sha256()
    for e in sorted(entries, key=lambda x: x.relative_path.lower()):
        line = f"{e.relative_path}\0{int(e.size)}\0{e.sha256}\n".encode("utf-8")
        h.update(line)
    return h.hexdigest()
