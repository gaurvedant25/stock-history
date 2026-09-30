#!/usr/bin/env python3
"""Build a reproducible upload ZIP containing only the distributed skill files."""

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def package_skill(output):
    root = Path(__file__).resolve().parents[1]
    skill = root / "stock-history"
    files = [skill / "SKILL.md"]
    for folder, extensions in (("references", {".md"}), ("scripts", {".py", ".html"})):
        files.extend(path for path in (skill / folder).rglob("*")
                     if path.is_file() and path.suffix in extensions
                     and "__pycache__" not in path.parts)
    if not files[0].is_file():
        raise FileNotFoundError("stock-history/SKILL.md is required")
    output = Path(output).resolve()
    if output == skill or skill in output.parents:
        raise ValueError("Write the upload archive outside the skill directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            entry = ZipInfo(path.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Archive integrity check failed")
    print(f"Created {output} ({len(files)} files)")


def main():
    default = Path(__file__).resolve().parents[1] / "dist" / "stock-history.zip"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=default)
    package_skill(parser.parse_args().out)


if __name__ == "__main__":
    main()
