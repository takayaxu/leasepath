import gzip
import json

import pytest

from leasepath.tracelab import (ENDPOINT, _feature_row, _target_row,
                               iter_training_samples, prepare_dataset,
                               verify_prepared_dataset, verify_source)


def test_target_keeps_result_and_runner_outcome_out_of_features():
    tool = {
        "tool_name": "exec_command", "emitted_at": "2026-01-01T00:00:00Z",
        "result_at": "2026-01-01T00:00:02Z", "tool_wall_latency_ms": 2000,
        "command_status": "running", "is_error": False,
    }
    label, quality = _target_row(tool, "s1")
    assert label["endpoint"] == ENDPOINT
    assert label["observed_log_span_ms"] == 2000
    assert label["exact_kv_label_supported"] is False
    assert quality["eligible_for_proxy"] is True


def test_inconsistent_and_missing_results_are_not_fabricated():
    base = {"emitted_at": "2026-01-01T00:00:00Z", "tool_wall_latency_ms": 1000}
    label, quality = _target_row({**base, "result_at": None}, "missing")
    assert label["observed_log_span_ms"] is None
    assert quality["eligible_for_proxy"] is False
    label, quality = _target_row({**base, "result_at": "2026-01-01T00:00:05Z"}, "bad")
    assert label["status"] == "missing_or_inconsistent_result"
    assert label["observed_log_span_ms"] is None


def test_split_groups_join_shared_project_but_not_null_project(tmp_path):
    from leasepath.tracelab import _split_groups

    # Exercise the grouping helper with a tiny valid gzip corpus.
    rows = [
        {"user": "u1", "project": "p", "provider": "a", "session_id": "s1", "tools": [{}]},
        {"user": "u2", "project": "p", "provider": "b", "session_id": "s2", "tools": [{}]},
        {"user": "u3", "project": None, "provider": "a", "session_id": "s3", "tools": [{}]},
    ]
    # The helper does not verify source hashes; construct its expected row shape directly.
    path = tmp_path / "rows.gz"
    with gzip.open(path, "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    assignment, groups, _, _ = _split_groups(path, 4)
    assert assignment["u1"][1] == assignment["u2"][1]
    assert assignment["u3"][1] != assignment["u1"][1]
    assert len(groups) == 2


def test_export_aligns_three_files_and_keeps_outcomes_out_of_features(tmp_path, monkeypatch):
    source = tmp_path / "source.gz"
    rows = [
        {"user": "u1", "project": "shared", "provider": "claude", "session_id": "s1",
         "round_index": 0, "tools": [{"tool_index": 0, "tool_name": "Read", "input_chars": 4,
           "emitted_at": "2026-01-01T00:00:00Z", "result_at": "2026-01-01T00:00:02Z",
           "tool_wall_latency_ms": 2000, "is_error": False, "command_status": "finished"}]},
        {"user": "u2", "project": "shared", "provider": "codex", "session_id": "s2",
         "round_index": 1, "tools": [{"tool_index": 0, "tool_name": "exec_command", "input_chars": 8,
           "emitted_at": "2026-01-01T00:00:00Z", "result_at": None,
           "tool_wall_latency_ms": None, "command_status": "running"}]},
        {"user": "u3", "project": None, "provider": "claude", "session_id": "s3",
         "round_index": 2, "tools": []},
    ]
    with gzip.open(source, "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    monkeypatch.setattr("leasepath.tracelab.verify_source", lambda _: None)
    output = tmp_path / "prepared"
    summary = prepare_dataset(source, output, seed=7)
    assert summary["source_tool_rows"] == 2
    seen = {}
    for split in ("train", "validation", "test"):
        opened = {}
        for kind in ("features", "labels", "quality"):
            with gzip.open(output / f"{split}.{kind}.jsonl.gz", "rt") as stream:
                opened[kind] = [json.loads(line) for line in stream]
        assert len({len(values) for values in opened.values()}) == 1
        for feature, label, quality in zip(opened["features"], opened["labels"], opened["quality"]):
            assert feature["sample_id"] == label["sample_id"] == quality["sample_id"]
            assert "command_status" not in feature["features"]
            seen[feature["sample_id"]] = (feature, label, quality)
    assert len(seen) == 2
    assert {value[1]["status"] for value in seen.values()} == {
        "observed", "missing_or_inconsistent_result"}
    split = summary["groups"][0]["split"]
    snapshots = list(iter_training_samples(output, split, checkpoints_s=(0, 1)))
    assert len(snapshots) == 2
    assert sum(row["weight"] for row in snapshots) == 1
    assert [row["label"]["remaining_s"] for row in snapshots] == [2, 1]
    audit = verify_prepared_dataset(output)
    assert audit["samples_aligned"] == 2
    assert audit["user_overlap"] == audit["named_project_overlap"] == audit["session_overlap"] == 0
    # Deterministic compressed bytes and metadata, independent of output path and time.
    second = prepare_dataset(source, tmp_path / "second", seed=7)
    assert summary == second
    with gzip.open(output / f"{split}.labels.jsonl.gz", "at") as stream:
        stream.write(json.dumps({"sample_id": "extra"}) + "\n")
    with pytest.raises(ValueError, match="derived file changed"):
        verify_prepared_dataset(output)


def test_features_unchanged_when_future_results_and_usage_are_rewritten():
    row = {"provider": "codex", "round_index": 2, "prefix_tokens": 100,
           "output_tokens": 500, "input_tokens_total": 300}
    tool = {"tool_name": "exec_command", "tool_index": 0, "input_chars": 42,
            "command_skeleton": "pytest", "executables": ["pytest"],
            "tool_wall_latency_ms": 1000, "tool_internal_latency_ms": 900,
            "result_at": "2026-01-01T00:00:01Z", "command_status": "running"}
    before = _feature_row(row, tool, "one", "group")
    tool.update(tool_wall_latency_ms=999999, tool_internal_latency_ms=999990,
                result_at="2027-01-01T00:00:00Z", command_status="finished",
                is_error=True, result_chars=100000, command_exit_code=1)
    row.update(prefix_tokens=99999, output_tokens=123456, input_tokens_total=99999)
    assert _feature_row(row, tool, "one", "group") == before
    assert "prefix_tokens" not in before["features"]
    assert "command_status" not in before["features"]


def test_source_rejects_unpinned_asset(tmp_path):
    source = tmp_path / "changed.gz"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_source(source)


def test_snapshot_loader_uses_strict_pending_boundary_and_rejects_future_field(tmp_path):
    from leasepath.tracelab import FEATURES
    feature = {"sample_id": "e1", "group_id": "g", "features": dict.fromkeys(FEATURES)}
    feature["features"]["elapsed_s"] = 0
    label = {"sample_id": "e1", "observed_log_span_ms": 1000}
    quality = {"sample_id": "e1", "eligible_for_proxy": True, "zero_span": False}
    def write():
        for kind, item in (("features", feature), ("labels", label), ("quality", quality)):
            with gzip.open(tmp_path / f"train.{kind}.jsonl.gz", "wt") as stream:
                stream.write(json.dumps(item) + "\n")
    write()
    snapshots = list(iter_training_samples(tmp_path, "train", checkpoints_s=(0, 1, 5)))
    assert len(snapshots) == 1  # The result is already present at elapsed=1.
    assert snapshots[0]["label"]["remaining_s"] == 1
    feature["features"]["command_status"] = "finished"
    write()
    with pytest.raises(ValueError, match="feature allowlist"):
        list(iter_training_samples(tmp_path, "train"))


def test_snapshot_loader_does_not_treat_zero_or_missing_as_waiting(tmp_path):
    from leasepath.tracelab import FEATURES
    features = [{"sample_id": key, "group_id": "g", "features": dict.fromkeys(FEATURES)}
                for key in ("zero", "missing")]
    labels = [{"sample_id": "zero", "observed_log_span_ms": 0},
              {"sample_id": "missing", "observed_log_span_ms": None}]
    quality = [{"sample_id": "zero", "eligible_for_proxy": True, "zero_span": True},
               {"sample_id": "missing", "eligible_for_proxy": False, "zero_span": False}]
    for kind, items in (("features", features), ("labels", labels), ("quality", quality)):
        with gzip.open(tmp_path / f"train.{kind}.jsonl.gz", "wt") as stream:
            stream.writelines(json.dumps(item) + "\n" for item in items)
    assert list(iter_training_samples(tmp_path, "train")) == []
    with gzip.open(tmp_path / "train.labels.jsonl.gz", "at") as stream:
        stream.write(json.dumps({"sample_id": "extra"}) + "\n")
    with pytest.raises(ValueError, match="unaligned dataset lengths"):
        list(iter_training_samples(tmp_path, "train"))
