"""Download/verify the pinned TraceLab release and build LeasePath proxy files."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leasepath.tracelab import (ASSET_BYTES, ASSET_NAME, ASSET_SHA256, COMMIT,
                                REPOSITORY, TAG, prepare_dataset, sha256_file,
                                verify_prepared_dataset, verify_source)

ASSET_URL = "https://api.github.com/repos/uw-syfi/TraceLab/releases/assets/488289841?download=1"
REFERENCES = {
    "LICENSE": "122da5085778889f4c36d898b7f7998345876cb75345d66b1418ae5aff0d1843",
    "LICENSE-DATASET.md": "1e96a0ec55f98ee0e4e2432626c0201104ebfb7f98cecf0a31d5313ddad92e9a",
    "README.md": "a3c6d004a2c66eb5e059fb392fab4cafc23923fc4b7b6bacb2de8b736ba52bce",
    "artifacts/llm_generation/timing_feature_ambiguity/README.md": "d030af26a239cfb5c06ea8a3033b3b992dd60d7e9727cf28fec15fa831f08b11",
    "artifacts/session/session_timing_distribution/README.md": "855dc97a423b4bc098c8959e50dfc1dbcafbdfe7c5ac2583aedeb13f7b3a5dac",
    "artifacts/utils/DB_SCHEMA.md": "036abc20eec0ea6a52647f90175b4ed9612de1543c159571d91456e0ed581ac1",
    "artifacts/utils/README.md": "32b5c0639116a4b0c29e17b981a615c01b4332f4c732746a0663506be2467dfc",
    "artifacts/utils/command_chains.py": "1c2173711b7f064cafe9ff0b980ac5ea9763970c6092e46d224f424c856d179b",
    "artifacts/utils/timing.py": "e979ea96163ccc6f01cd115e998b392531215d0aed9fa1e00a55e5bb65ec8d8f",
    "artifacts/utils/tool_stats.py": "ddbe7abd59d9a4d8a132f6c8c108eca52344bfe8cd71cd6807079cc2d7afb30c",
    "docs/prompt_cache_accounting.md": "3b6d340284021dd070f99394e5071e331441df0a368f5a7bf7566d74d12eee32",
    "example_sessions/sanitized/round_trace.jsonl": "314c49483148fe68e230a683f739e3e6e68d478948ecf342739596f30af36b57",
    "scripts/extract_claude_rounds.py": "d73c36cea41a23fe8a5c0a5a94f20abeb08fc325071c819bf2aa362e60788ee5",
    "scripts/extract_codex_rounds.py": "12d5f5730a29d6cc58c4b8f0cdce45791055a6deb598de6bf83db8ef63fd2357",
    "scripts/sanitize_round_trace.py": "a9daa8cd47ef22b0e7b9de9256bfda40036cec6d349332d93214cc5b94a5842c",
}


def acquire(source: Path):
    if source.exists():
        verify_source(source)
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    request = Request(ASSET_URL, headers={
        "Accept": "application/octet-stream",
        "User-Agent": "LeasePath-tracelab-collector",
    })
    with tempfile.NamedTemporaryFile(prefix="tracelab-", dir=source.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        digest = hashlib.sha256()
        size = 0
        try:
            with urlopen(request, timeout=120) as response:
                while block := response.read(1024 * 1024):
                    temp.write(block)
                    digest.update(block)
                    size += len(block)
            if size != ASSET_BYTES or digest.hexdigest() != ASSET_SHA256:
                raise ValueError("downloaded asset size or SHA-256 mismatch")
            temp.flush()
            temp_path.replace(source)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    verify_source(source)


def write_provenance(source: Path, offline: bool):
    root = source.parent
    reference = root / "reference"
    files = {}
    for name, expected in REFERENCES.items():
        path = reference / name
        if not path.exists():
            if offline:
                raise FileNotFoundError(f"missing fixed reference: {path}")
            request = Request(
                f"https://api.github.com/repos/uw-syfi/TraceLab/contents/{name}?ref={COMMIT}",
                headers={"User-Agent": "LeasePath-tracelab-collector"})
            with urlopen(request, timeout=60) as response:
                record = json.load(response)
            data = base64.b64decode(record["content"])
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"reference hash mismatch: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if sha256_file(path) != expected:
            raise ValueError(f"local fixed reference changed: {name}")
        files[f"reference/{name}"] = expected
    existing_path = root / "provenance.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text())
        if (existing["asset_sha256"] != ASSET_SHA256 or existing["commit"] != COMMIT
                or existing["reference_files_sha256"] != files):
            raise ValueError("existing source provenance disagrees with fixed release")
        return
    provenance = {
        "repository": REPOSITORY, "tag": TAG, "commit": COMMIT,
        "release_asset_id": 488289841, "asset_name": ASSET_NAME,
        "asset_url": ASSET_URL, "asset_bytes": source.stat().st_size,
        "asset_sha256": sha256_file(source), "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "reference_url_template": f"https://api.github.com/repos/uw-syfi/TraceLab/contents/PATH?ref={COMMIT}",
        "dataset_license": "CC BY 4.0",
        "attribution": "TraceLab (SyFI Lab, University of Washington)",
        "reference_files_sha256": files,
        "upstream_code_executed": False,
    }
    (root / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=ROOT / "data/sources/tracelab/v0.0.2" / ASSET_NAME)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data/processed/tracelab/v0.0.2")
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--offline", action="store_true", help="verify and adapt local data only")
    parser.add_argument("--verify-only", action="store_true", help="verify existing source and prepared data")
    parser.add_argument("--audit", type=Path, default=ROOT / "results/prediction/tracelab_audit")
    args = parser.parse_args()
    if args.offline and not args.source.exists():
        parser.error("--offline requires the local release asset")
    if not args.offline:
        acquire(args.source)
    verify_source(args.source)
    write_provenance(args.source, args.offline)
    if args.verify_only:
        result = json.loads((args.output / "summary.json").read_text())
    else:
        result = prepare_dataset(args.source, args.output, args.seed)
    verification = verify_prepared_dataset(args.output)
    args.audit.mkdir(parents=True, exist_ok=True)
    verification["summary_sha256"] = sha256_file(args.output / "summary.json")
    (args.audit / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({
        "source_sha256": result["source"]["sha256"],
        "groups": len(result["groups"]),
        "tools": result["source_tool_rows"],
        "splits": {name: {"tools": result["counts"][f"{name}_tools"],
                           "observed": result["counts"][f"{name}_observed"],
                           "missing": result["counts"][f"{name}_missing"]}
                   for name in ("train", "validation", "test")},
        "output": str(args.output), "endpoint": result["endpoint"],
        "verification_passed": verification["passed"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
