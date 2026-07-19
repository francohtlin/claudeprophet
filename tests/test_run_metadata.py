import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from api_service.run_metadata import (
    append_evidence_item,
    initialize_request_metadata,
    load_active_variant,
    metadata_paths,
    write_trace,
)


def _append_evidence_for_concurrency(args: tuple[str, int]) -> int:
    path, index = args
    append_evidence_item(
        work_dir=Path(path),
        item={"kind": "web", "source": "test", "query": f"query-{index}"},
    )
    return index


def test_load_active_variant_from_registry() -> None:
    variant = load_active_variant(Path("."))

    assert variant["variant_id"] == "v1_market_prior_claude"
    assert variant["model"] == "claude-opus-4-8"
    assert "market_lookup" in variant["tools_enabled"]


def test_initialize_request_metadata_writes_manifest_and_trace(tmp_path) -> None:
    variant = {"variant_id": "test_variant", "model": "gpt-test", "api_key": "secret"}
    paths = initialize_request_metadata(
        work_dir=tmp_path,
        request_id="request-1",
        event={"title": "Will X happen?", "event_ticker": "EVT"},
        variant=variant,
    )

    manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    assert manifest["request_id"] == "request-1"
    assert manifest["event_title"] == "Will X happen?"
    assert manifest["variant"]["variant_id"] == "test_variant"
    assert manifest["variant"]["api_key"] == "[redacted]"
    assert manifest["items"] == []
    assert paths["trace"].exists()


def test_write_trace_and_evidence_redact_secrets(tmp_path) -> None:
    write_trace(
        work_dir=tmp_path,
        request_id="request-2",
        stage="tool_called",
        payload={"api_key": "secret", "message": "called market lookup"},
    )
    append_evidence_item(
        work_dir=tmp_path,
        item={
            "kind": "prediction_market",
            "source": "Kalshi",
            "query": "test",
            "token": "secret",
        },
    )

    trace_line = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    manifest = json.loads((tmp_path / "evidence_manifest.json").read_text(encoding="utf-8"))

    assert trace_line["stage"] == "tool_called"
    assert trace_line["api_key"] == "[redacted]"
    assert manifest["items"][0]["token"] == "[redacted]"
    assert manifest["items"][0]["usable"] is True
    assert "retrieved_at" in manifest["items"][0]


def test_metadata_paths_are_request_local(tmp_path) -> None:
    paths = metadata_paths(tmp_path)

    assert paths["request_workspace"] == str(tmp_path)
    assert paths["evidence_manifest_path"].endswith("evidence_manifest.json")
    assert paths["trace_path"].endswith("trace.jsonl")


def test_append_evidence_item_preserves_parallel_writes(tmp_path) -> None:
    initialize_request_metadata(
        work_dir=tmp_path,
        request_id="request-concurrent",
        event={"title": "Will X happen?"},
        variant={"variant_id": "test"},
    )

    with ProcessPoolExecutor(max_workers=6) as pool:
        written = list(pool.map(_append_evidence_for_concurrency, [(str(tmp_path), index) for index in range(12)]))

    manifest = json.loads((tmp_path / "evidence_manifest.json").read_text(encoding="utf-8"))
    queries = {item["query"] for item in manifest["items"]}

    assert sorted(written) == list(range(12))
    assert len(manifest["items"]) == 12
    assert queries == {f"query-{index}" for index in range(12)}
