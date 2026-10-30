"""Prepare a pinned TraceLab release as a tool-response-time proxy dataset.

The outcome is a sanitized log span, not a measured execution time or KV event.
The module never imports or runs TraceLab code.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime
import gzip
import hashlib
import io
from itertools import zip_longest
import json
import math
from pathlib import Path
import tempfile

import numpy as np

REPOSITORY = "https://github.com/uw-syfi/TraceLab"
TAG = "v0.0.2"
COMMIT = "61fea8f97277d0aa247cc2e4c7e31f90ed2bea1c"
ASSET_ID = 488289841
ASSET_NAME = "syfi_coding_trace.jsonl.gz"
ASSET_BYTES = 100_939_722
ASSET_SHA256 = "11ce51ec0a25e3d1d95b025bca2f7d1647e47571eb7cc968acd5fc64d4b4fb65"
ENDPOINT = "tool_result_log_span_proxy"
SPLITS = ("train", "validation", "test")
CHECKPOINTS_S = (0, 1, 5, 30, 120)
FEATURES = ("provider", "tool_name", "round_index", "tool_index", "input_chars",
            "executable_parse_status", "executables", "command_skeleton", "elapsed_s")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source(source: Path) -> None:
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size != ASSET_BYTES or sha256_file(source) != ASSET_SHA256:
        raise ValueError("TraceLab release asset size or SHA-256 mismatch")


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _split_groups(source: Path, seed: int):
    """Connect users sharing a named project, then greedily balance whole components."""
    project_users = defaultdict(set)
    session_users = defaultdict(set)
    user_weights = Counter()
    user_projects = defaultdict(set)
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            user, project = row.get("user"), row.get("project")
            if user is None:
                raise ValueError("missing user prevents leakage-safe splitting")
            user_weights[user] += len(row.get("tools") or [])
            session_users[(row.get("provider"), row.get("session_id"))].add(user)
            if project is not None:
                project_users[project].add(user)
                user_projects[user].add(project)
    union = _UnionFind()
    for users in (*project_users.values(), *session_users.values()):
        users = sorted(users)
        for user in users[1:]:
            union.union(users[0], user)
    components = defaultdict(set)
    for user in user_weights:
        components[union.find(user)].add(user)
    weighted = []
    for users in components.values():
        projects = sorted({project for user in users for project in user_projects[user]})
        weight = sum(user_weights[user] for user in users)
        digest = hashlib.sha256((str(seed) + "|" + "|".join(sorted(users))).encode()).hexdigest()
        group_id = "component_" + digest[:16]
        weighted.append((weight, digest, group_id, sorted(users), projects))
    weighted.sort(key=lambda item: (-item[0], item[1]))
    totals = Counter()
    targets = {"train": 0.8, "validation": 0.1, "test": 0.1}
    assignments, groups = {}, []
    overall = sum(item[0] for item in weighted)
    for weight, digest, group_id, users, projects in weighted:
        # Assign largest groups first to the split with the most unfilled rows.
        # This avoids forcing the second-largest user into a small holdout.
        split = max(targets, key=lambda name: targets[name] * overall - totals[name])
        totals[split] += weight
        for user in users:
            assignments[user] = (split, group_id)
        groups.append({"group_id": group_id, "split": split, "users": users,
                       "projects": projects, "tool_rows": weight})
    return assignments, groups, dict(totals), overall


def _feature_row(row, tool, sample_id, group_id):
    features = {
        "provider": row.get("provider"),
        "tool_name": tool.get("tool_name"),
        "round_index": row.get("round_index"),
        "tool_index": tool.get("tool_index"),
        "input_chars": tool.get("input_chars"),
        "executable_parse_status": tool.get("executable_parse_status"),
        "executables": tool.get("executables"),
        "command_skeleton": tool.get("command_skeleton"),
        "elapsed_s": 0,
    }
    return {"sample_id": sample_id, "group_id": group_id,
            "metadata": {"user": row.get("user"), "project": row.get("project"),
                         "session_id": row.get("session_id"), "round_id": row.get("round_id"),
                         "tool_call_id": tool.get("tool_call_id"), "emitted_at": tool.get("emitted_at"),
                         "continuation_of_tool_call_id": tool.get("continuation_of_tool_call_id")},
            "features": features}


def _target_row(tool, sample_id):
    start, end = tool.get("emitted_at"), tool.get("result_at")
    elapsed_ms = None
    if start is not None and end is not None:
        elapsed_ms = (_timestamp(end) - _timestamp(start)).total_seconds() * 1000
    raw_ms = tool.get("tool_wall_latency_ms")
    agrees = (elapsed_ms is None or raw_ms is None or
              math.isclose(elapsed_ms, raw_ms, abs_tol=1.01, rel_tol=0))
    valid = elapsed_ms is not None and math.isfinite(elapsed_ms) and elapsed_ms >= 0 and agrees
    status = "observed" if valid else "missing_or_inconsistent_result"
    return ({"sample_id": sample_id, "endpoint": ENDPOINT, "status": status,
             "observed_log_span_ms": elapsed_ms if valid else None,
             "result_at": end, "source_wall_latency_ms": raw_ms,
             "source_internal_latency_ms": tool.get("tool_internal_latency_ms"),
             "is_error": tool.get("is_error"), "command_status": tool.get("command_status"),
             "command_exit_code": tool.get("command_exit_code"),
             "exact_kv_outcome": None, "exact_kv_label_supported": False},
            {"sample_id": sample_id, "eligible_for_proxy": valid,
             "zero_span": bool(valid and elapsed_ms == 0),
             "timestamp_duration_agrees": agrees,
             "missing_result": end is None})


def _gzip_writer(stack, path):
    raw = stack.enter_context(path.open("wb"))
    compressed = stack.enter_context(gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0,
                                                  compresslevel=6))
    return stack.enter_context(io.TextIOWrapper(compressed, encoding="utf-8", newline="\n"))


def iter_training_samples(directory, split, checkpoints_s=CHECKPOINTS_S):
    """Yield X/remaining-time Y; only the nested features object may reach a model.

    Fixed checkpoints create a risk set (only tools still waiting at that time).
    Missing and zero spans stay in the archive but cannot enter this risk set.
    Each episode's snapshots have total weight one; no history is inferred.
    """
    if split not in SPLITS:
        raise ValueError("unknown split")
    checkpoints_s = tuple(checkpoints_s)
    if not checkpoints_s or any(not math.isfinite(x) or x < 0 for x in checkpoints_s):
        raise ValueError("checkpoints must be nonnegative finite numbers")
    if sorted(set(checkpoints_s)) != list(checkpoints_s):
        raise ValueError("checkpoints must be unique and sorted")
    with ExitStack() as stack:
        streams = [stack.enter_context(gzip.open(Path(directory) / f"{split}.{kind}.jsonl.gz", "rt"))
                   for kind in ("features", "labels", "quality")]
        for lines in zip_longest(*streams):
            if None in lines:
                raise ValueError("unaligned dataset lengths")
            feature, label, quality = map(json.loads, lines)
            if len({x["sample_id"] for x in (feature, label, quality)}) != 1:
                raise ValueError("unaligned sample IDs")
            if set(feature["features"]) != set(FEATURES):
                raise ValueError("feature allowlist violation")
            if not quality["eligible_for_proxy"] or quality["zero_span"]:
                continue
            duration_s = label["observed_log_span_ms"] / 1000
            checkpoints = [x for x in checkpoints_s if x < duration_s]
            for elapsed in checkpoints:
                yield {"sample_id": f"{feature['sample_id']}@{elapsed}s",
                       "episode_id": feature["sample_id"], "group_id": feature["group_id"],
                       "features": {**feature["features"], "elapsed_s": elapsed},
                       "label": {"endpoint": ENDPOINT, "remaining_s": duration_s - elapsed},
                       "weight": 1 / len(checkpoints)}


def verify_prepared_dataset(directory):
    """Read all three exports, verify hashes/alignment and enforce group isolation."""
    directory = Path(directory)
    summary = json.loads((directory / "summary.json").read_text())
    for name, digest in summary["derived_files_sha256"].items():
        if Path(name).name != name or sha256_file(directory / name) != digest:
            raise ValueError(f"derived file changed: {name}")
    group_split = {group["group_id"]: group["split"] for group in summary["groups"]}
    membership = {"user": {}, "project": {}, "session": {}}
    with (directory / "sessions.jsonl").open() as stream:
        for line in stream:
            session = json.loads(line)
            split = session["split"]
            if group_split[session["group_id"]] != split:
                raise ValueError("session group/split mismatch")
            for kind, key in (("user", session["user"]), ("project", session["project"]),
                              ("session", (session["provider"], session["session_id"]))):
                if key is None:
                    continue
                previous = membership[kind].setdefault(key, split)
                if previous != split:
                    raise ValueError(f"{kind} crosses splits")
    checks, sample_ids = {}, set()
    for split in SPLITS:
        counts = Counter()
        with ExitStack() as stack:
            streams = [stack.enter_context(gzip.open(directory / f"{split}.{kind}.jsonl.gz", "rt"))
                       for kind in ("features", "labels", "quality")]
            for lines in zip_longest(*streams):
                if None in lines:
                    raise ValueError("unaligned dataset lengths")
                feature, label, quality = map(json.loads, lines)
                identifier = feature["sample_id"]
                if label["sample_id"] != identifier or quality["sample_id"] != identifier:
                    raise ValueError("unaligned sample IDs")
                if identifier in sample_ids:
                    raise ValueError("duplicate sample ID")
                sample_ids.add(identifier)
                if set(feature["features"]) != set(FEATURES):
                    raise ValueError("feature allowlist violation")
                if group_split[feature["group_id"]] != split:
                    raise ValueError("sample group/split mismatch")
                metadata = feature["metadata"]
                for kind, key in (("user", metadata["user"]), ("project", metadata["project"]),
                                  ("session", (feature["features"]["provider"], metadata["session_id"]))):
                    if key is not None and membership[kind].get(key) != split:
                        raise ValueError(f"sample {kind}/split mismatch")
                if label["endpoint"] != ENDPOINT or label["exact_kv_label_supported"] or label["exact_kv_outcome"] is not None:
                    raise ValueError("wrong prediction target")
                counts["tools"] += 1
                if quality["eligible_for_proxy"]:
                    duration = label["observed_log_span_ms"]
                    if label["status"] != "observed" or duration is None or duration < 0 or not math.isfinite(duration):
                        raise ValueError("invalid proxy label")
                    counts["observed"] += 1
                    counts["zero"] += duration == 0
                    counts["positive"] += duration > 0
                    counts["snapshots"] += sum(x < duration / 1000 for x in CHECKPOINTS_S)
                else:
                    if label["observed_log_span_ms"] is not None:
                        raise ValueError("excluded sample has fitted target")
                    counts["missing"] += 1
        for name in ("tools", "observed", "zero", "positive", "snapshots", "missing"):
            if counts[name] != summary["counts"].get(f"{split}_{name}", 0):
                raise ValueError(f"{split} {name} count mismatch")
        checks[split] = dict(counts)
    return {"passed": True, "endpoint": ENDPOINT, "split_counts": checks,
            "user_overlap": 0, "named_project_overlap": 0, "session_overlap": 0,
            "duplicate_sample_ids": 0, "samples_aligned": len(sample_ids),
            "verified_file_count": len(summary["derived_files_sha256"]),
            "checks": ["sha256", "full_gzip_jsonl_read", "feature_allowlist", "sample_alignment",
                       "label_semantics", "user_project_session_isolation", "snapshot_counts"]}


def prepare_dataset(source: Path, output: Path, seed: int = 20261029):
    """Create aligned, split-specific features, labels, quality and provenance files."""
    source, output = Path(source), Path(output)
    verify_source(source)
    assignments, groups, split_tools, total_tools = _split_groups(source, seed)
    output.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    sessions, durations = {}, []
    provider_rounds, tool_names, statuses = Counter(), Counter(), Counter()
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=output) as staging, ExitStack() as stack:
        staging = Path(staging)
        writers = {split: {kind: _gzip_writer(stack, staging / f"{split}.{kind}.jsonl.gz")
                           for kind in ("features", "labels", "quality")}
                   for split in SPLITS}
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            for row_ordinal, line in enumerate(stream, 1):
                row = json.loads(line)
                user = row["user"]
                split, group_id = assignments[user]
                key = (user, row.get("provider"), row.get("project"), row.get("session_id"))
                session = sessions.setdefault(key, {"user": user, "provider": row.get("provider"),
                    "project": row.get("project"), "session_id": row.get("session_id"),
                    "group_id": group_id, "split": split, "rounds": 0, "tools": 0})
                session["rounds"] += 1
                session["tools"] += len(row.get("tools") or [])
                provider_rounds[row.get("provider")] += 1
                if row.get("input_tokens_total") != row.get("prefix_tokens", 0) + row.get("newly_append_tokens", 0):
                    counts["token_equation_mismatch"] += 1
                counts[f"{split}_rounds"] += 1
                for tool in row.get("tools") or []:
                    sample_id = f"row_{row_ordinal}:tool_{tool['tool_index']}"
                    feature = _feature_row(row, tool, sample_id, group_id)
                    label, quality = _target_row(tool, sample_id)
                    for kind, record in (("features", feature), ("labels", label), ("quality", quality)):
                        writers[split][kind].write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                    counts[f"{split}_tools"] += 1
                    counts[f"{split}_observed"] += label["status"] == "observed"
                    counts[f"{split}_missing"] += label["status"] != "observed"
                    counts[f"{split}_zero"] += quality["zero_span"]
                    counts["missing_internal_latency"] += tool.get("tool_internal_latency_ms") is None
                    counts["zero_internal_latency"] += tool.get("tool_internal_latency_ms") == 0
                    counts["linked_continuations"] += bool(tool.get("continuation_of_tool_call_id"))
                    counts["wall_timestamp_mismatch"] += not quality["timestamp_duration_agrees"]
                    counts["missing_result"] += quality["missing_result"]
                    tool_names[tool.get("tool_name")] += 1
                    statuses[tool.get("command_status") or "unknown"] += 1
                    if quality["eligible_for_proxy"]:
                        duration_s = label["observed_log_span_ms"] / 1000
                        durations.append(duration_s)
                        checkpoints = [x for x in CHECKPOINTS_S if x < duration_s]
                        counts[f"{split}_snapshots"] += len(checkpoints)
                        counts[f"{split}_positive"] += duration_s > 0
                counts["source_rows_seen"] += 1
        stack.close()
        for path in sorted(staging.iterdir()):
            path.replace(output / path.name)
    (output / "sessions.jsonl").write_text("".join(
        json.dumps(session, ensure_ascii=False, separators=(",", ":")) + "\n"
        for session in sessions.values()))
    derived_hashes = {
        path.name: sha256_file(path)
        for path in sorted(output.iterdir()) if path.name.endswith((".jsonl.gz", ".jsonl"))
    }
    if sum(counts[f"{split}_tools"] for split in SPLITS) != total_tools:
        raise ValueError("source/export row counts differ")
    summary = {
        "dataset": "TraceLab v0.0.2 adapted by LeasePath",
        "endpoint": ENDPOINT,
        "source": {"repository": REPOSITORY, "tag": TAG, "commit": COMMIT,
                   "asset": ASSET_NAME, "asset_id": ASSET_ID,
                   "bytes": source.stat().st_size, "sha256": sha256_file(source)},
        "license": "CC BY 4.0; attribution to TraceLab (SyFI Lab, University of Washington)",
        "seed": seed, "split_policy": "connected users sharing any named project or session; null projects do not connect users; largest components first into largest target deficit (80/10/10); seed breaks equal-size ties; not a chronological holdout",
        "groups": groups, "split_tool_targets": split_tools, "source_tool_rows": total_tools,
        "counts": dict(counts), "derived_files_sha256": derived_hashes,
        "adapter_sha256": sha256_file(Path(__file__)),
        "users": len(assignments), "scoped_sessions": len(sessions),
        "distinct_session_ids": len({key[3] for key in sessions}),
        "named_projects": len({key[2] for key in sessions if key[2] is not None}),
        "provider_rounds": dict(provider_rounds), "tool_names": dict(tool_names),
        "command_statuses": dict(statuses),
        "duration_s": dict(zip(("min", "p50", "p90", "p95", "p99", "max"),
                               map(float, np.percentile(durations, [0, 50, 90, 95, 99, 100])))) if durations else {},
        "feature_allowlist": list(FEATURES), "checkpoints_s": CHECKPOINTS_S,
        "snapshot_loader": "leasepath.tracelab.iter_training_samples",
        "snapshot_policy": "strictly pending at fixed elapsed checkpoints; zero and missing spans excluded; each episode total weight 1",
        "label_fields": ["observed_log_span_ms", "is_error", "command_status", "command_exit_code"],
        "limitations": ["Tool-result log span is not isolated execution time.",
                        "No legal prefix identity, KV lookup or explicit retirement is present; exact U/D/C labels are unsupported.",
                        "Rows missing a coherent result time have no proxy duration label and are excluded from fitting.",
                        "Zero-duration records are retained and flagged, excluded from pending snapshots.",
                        "Codex running calls are not command completion; command_status is a future outcome label, not a feature."],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


