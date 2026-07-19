from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VARIANTS_PATH = Path("config") / "variants.json"
EVIDENCE_MANIFEST_NAME = "evidence_manifest.json"
TRACE_NAME = "trace.jsonl"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_active_variant(root: Path, variant_id: str | None = None) -> dict[str, Any]:
    """Load active forecast variant metadata from config/variants.json.

    The returned object is intentionally self-contained so it can be logged,
    passed to the Claude subprocess through env, and written into request files.
    """
    configured_id = variant_id or os.getenv("CLAUDE_FORECAST_VARIANT")
    path = root / VARIANTS_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        payload = {"active_variant": "default", "variants": {}}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid variant registry JSON at {path}: {exc}") from exc

    active_id = configured_id or payload.get("active_variant") or "default"
    variants = payload.get("variants") if isinstance(payload.get("variants"), dict) else {}
    metadata = variants.get(active_id, {})
    if not isinstance(metadata, dict):
        metadata = {}

    result = {
        "variant_id": active_id,
        "registry_path": str(path),
        **metadata,
    }
    result.setdefault("description", "Unregistered forecast variant.")
    result.setdefault("model", os.getenv("CLAUDE_FORECAST_MODEL", "claude-opus-4-8"))
    result.setdefault("prompt_version", "unknown")
    result.setdefault("calibration_policy", "unknown")
    result.setdefault("output_policy", "unknown")
    result.setdefault("tools_enabled", [])
    return result


def initialize_request_metadata(
    *,
    work_dir: Path,
    request_id: str,
    event: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / EVIDENCE_MANIFEST_NAME
    trace_path = work_dir / TRACE_NAME
    if not manifest_path.exists():
        manifest = {
            "request_id": request_id,
            "created_at": iso_now(),
            "event_title": event.get("title") or event.get("event_ticker") or event.get("market_ticker"),
            "event_ticker": event.get("event_ticker"),
            "market_ticker": event.get("market_ticker"),
            "variant": redact(variant),
            "items": [],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not trace_path.exists():
        trace_path.touch()
    return {"evidence_manifest": manifest_path, "trace": trace_path}


def write_trace(
    *,
    work_dir: Path,
    request_id: str,
    stage: str,
    payload: dict[str, Any] | None = None,
    mirror_dir: Path | None = None,
) -> dict[str, Any]:
    entry = {
        "ts": iso_now(),
        "request_id": request_id,
        "stage": stage,
        **(payload or {}),
    }
    safe_entry = redact(entry)
    work_dir.mkdir(parents=True, exist_ok=True)
    _append_jsonl(work_dir / TRACE_NAME, safe_entry)
    if mirror_dir is not None:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(mirror_dir / f"{request_id}.jsonl", safe_entry)
    return safe_entry


def append_evidence_item(*, work_dir: Path, item: dict[str, Any]) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = work_dir / EVIDENCE_MANIFEST_NAME
    evidence_item = dict(item)
    evidence_item.setdefault("retrieved_at", iso_now())
    evidence_item.setdefault("usable", True)
    evidence_item = redact(evidence_item)
    with _file_lock(manifest_path):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            manifest = {
                "request_id": work_dir.name,
                "created_at": iso_now(),
                "items": [],
            }

        items = manifest.setdefault("items", [])
        if not isinstance(items, list):
            manifest["items"] = items = []
        items.append(evidence_item)
        manifest["updated_at"] = iso_now()
        _write_json_atomic(manifest_path, manifest)
    return evidence_item


def metadata_paths(work_dir: Path) -> dict[str, str]:
    return {
        "request_workspace": str(work_dir),
        "evidence_manifest_path": str(work_dir / EVIDENCE_MANIFEST_NAME),
        "trace_path": str(work_dir / TRACE_NAME),
    }


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lower = str(key).lower()
            if "key" in lower or "token" in lower or "secret" in lower or "authorization" in lower:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _append_jsonl(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _file_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


@contextmanager
def _file_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temp_name = handle.name
    os.replace(temp_name, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api_service.run_metadata")
    subparsers = parser.add_subparsers(dest="command", required=True)

    trace_parser = subparsers.add_parser("trace", help="Append one trace event to a request workspace.")
    trace_parser.add_argument("--workspace", required=True)
    trace_parser.add_argument("--request-id", default=None)
    trace_parser.add_argument("--stage", required=True)
    trace_parser.add_argument("--message", default=None)
    trace_parser.add_argument("--payload-json", default="{}")

    evidence_parser = subparsers.add_parser("evidence", help="Append one evidence item to a request workspace.")
    evidence_parser.add_argument("--workspace", required=True)
    evidence_parser.add_argument("--kind", required=True)
    evidence_parser.add_argument("--source", required=True)
    evidence_parser.add_argument("--query", default=None)
    evidence_parser.add_argument("--notes", default=None)
    evidence_parser.add_argument("--usable", default="true")
    evidence_parser.add_argument("--extra-json", default="{}")

    args = parser.parse_args(argv)
    work_dir = Path(args.workspace)
    if args.command == "trace":
        payload = _json_arg(args.payload_json)
        if args.message:
            payload["message"] = args.message
        write_trace(
            work_dir=work_dir,
            request_id=args.request_id or work_dir.name,
            stage=args.stage,
            payload=payload,
        )
        return 0

    if args.command == "evidence":
        item = _json_arg(args.extra_json)
        item.update(
            {
                "kind": args.kind,
                "source": args.source,
                "query": args.query,
                "notes": args.notes,
                "usable": str(args.usable).lower() not in {"0", "false", "no", "off"},
            }
        )
        append_evidence_item(work_dir=work_dir, item={k: v for k, v in item.items() if v is not None})
        return 0
    return 2


def _json_arg(value: str) -> dict[str, Any]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise SystemExit("JSON argument must decode to an object.")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
