"""Build the shareable ZIP from RELEASE-FILES.txt, without local state."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

from install import release_files


def build(source: Path, destination: Path) -> dict:
    files = release_files(source)
    version = (source / "src/rso_context/__init__.py").read_text(encoding="utf-8")
    if '__version__ = "0.9.3"' not in version:
        raise ValueError("Review the release version before packaging")
    if destination.exists():
        raise FileExistsError(f"Preserve the existing archive before rebuilding: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    prefix = "RSOContextAlpha-0.9.3/"
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            # Fixed ZIP-safe timestamps, UTF-8 names, and POSIX mode metadata.
            info = zipfile.ZipInfo(prefix + relative.as_posix(), (2026, 9, 5, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100755 if relative.as_posix() == "rso-context" else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (source / relative).read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
        if archive.namelist() != [prefix + p.as_posix() for p in files]:
            raise ValueError("Archive contents differ from the explicit manifest")
    return {"archive": str(destination.resolve()), "files": len(files),
            "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/RSOContextAlpha-0.9.3.zip"))
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parent, args.output), indent=2))
