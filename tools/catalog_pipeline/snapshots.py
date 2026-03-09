from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import ARTIFACT_ROOT


def prepare_snapshot_dirs(base_dir: Path | None = None) -> dict[str, Path | str]:
    root = base_dir or ARTIFACT_ROOT
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / run_id
    latest_dir = root / "latest"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    return {"root": root, "run_id": run_id, "run_dir": run_dir, "latest_dir": latest_dir}


def write_snapshot(source_name: str, payload: Any, dirs: dict[str, Any]) -> str:
    filename = f"{source_name}.json"
    run_path = Path(dirs["run_dir"]) / filename
    latest_path = Path(dirs["latest_dir"]) / filename
    text = json.dumps(payload, indent=2, ensure_ascii=False) if not isinstance(payload, str) else payload
    run_path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    latest_path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
    return str(run_path)


def write_pipeline_report(report: dict[str, Any], dirs: dict[str, Any]) -> str:
    run_path = Path(dirs["run_dir"]) / "pipeline_report.json"
    latest_path = Path(dirs["latest_dir"]) / "pipeline_report.json"
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    run_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return str(run_path)
