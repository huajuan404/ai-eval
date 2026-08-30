"""Reject exact or base64-embedded reuse of the visible reference screenshots."""

from __future__ import annotations

import argparse
import base64
import hashlib
from pathlib import Path

_ALWAYS_IGNORED_DIRS = {".git", "ai_eval", "node_modules"}
_MAX_SCAN_BYTES = 25 * 1024 * 1024


def _is_within(path: Path, root: Path) -> bool:
    try:
        # Compare lexical locations, not symlink targets. A public/ symlink that
        # points back into the allowed input directory is still contestant reuse.
        path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return True


def detect_reuse(
    project_root: Path,
    reference_dir: Path,
    allowed_dir: Path,
    *,
    include_build: bool,
) -> list[str]:
    references = []
    for path in sorted(reference_dir.glob("*.png")):
        content = path.read_bytes()
        references.append(
            (
                path.name,
                hashlib.sha256(content).hexdigest(),
                base64.b64encode(content),
            )
        )

    findings: list[str] = []
    for candidate in project_root.rglob("*"):
        if not candidate.is_file() or _is_within(candidate, allowed_dir):
            continue
        relative = candidate.relative_to(project_root)
        ignored = _ALWAYS_IGNORED_DIRS | (set() if include_build else {".next"})
        if any(part in ignored for part in relative.parts):
            continue
        try:
            if candidate.stat().st_size > _MAX_SCAN_BYTES:
                continue
            content = candidate.read_bytes()
        except OSError:
            continue

        digest = hashlib.sha256(content).hexdigest()
        for reference_name, reference_digest, encoded in references:
            if digest == reference_digest:
                findings.append(f"exact copy of {reference_name}: {relative.as_posix()}")
            elif encoded in content:
                findings.append(f"base64 embed of {reference_name}: {relative.as_posix()}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    parser.add_argument("reference_dir", type=Path)
    parser.add_argument("allowed_dir", type=Path)
    parser.add_argument("--include-build", action="store_true")
    args = parser.parse_args()

    findings = detect_reuse(
        args.project_root,
        args.reference_dir,
        args.allowed_dir,
        include_build=args.include_build,
    )
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
