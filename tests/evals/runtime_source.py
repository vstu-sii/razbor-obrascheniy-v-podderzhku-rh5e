"""Verify a separately checked-out implementation without publishing other roles."""

import hashlib
import json
import os
from pathlib import Path


def checked_runtime():
    value = os.environ.get("LAB04_RUNTIME_ROOT")
    project = (
        Path(value) if value else Path(__file__).resolve().parents[1] / "runtime"
    ).resolve()
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "runtime-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for name, expected in manifest["files"].items():
        path = (project / name).resolve()
        if not path.is_relative_to(project) or not path.is_file():
            raise ValueError("Missing runtime source: " + name)
        actual = hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Wrong runtime version: " + name)
    return project
