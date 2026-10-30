"""Download a pinned Qwen2.5-3B-Instruct snapshot from ModelScope.

Files are streamed to .part paths, resumed with HTTP Range, then verified
against the repository's SHA-256 metadata before becoming visible to loaders.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


MODEL_ID = "Qwen/Qwen2.5-3B-Instruct"
REVISION = "master"
API = f"https://modelscope.cn/api/v1/models/{MODEL_ID}/repo/files?Revision={REVISION}&Recursive=true"
BASE = f"https://modelscope.cn/models/{MODEL_ID}/resolve/{REVISION}/"
FILES = {
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "model.safetensors.index.json",
    "model-00001-of-00002.safetensors",
    "model-00002-of-00002.safetensors",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_piece(url, path, start, end):
    expected = end - start + 1
    if path.exists() and path.stat().st_size == expected:
        return
    for attempt in range(8):
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            request = Request(url, headers={"User-Agent": "LeasePath/0.1", "Range": f"bytes={start}-{end}"})
            with urlopen(request, timeout=60) as response:
                if response.status != 206:
                    raise ValueError(f"HTTP Range not honored: {path.name}")
                with temp.open("wb") as out:
                    while chunk := response.read(4 * 1024 * 1024):
                        out.write(chunk)
            if temp.stat().st_size != expected:
                raise IOError(f"short segment: {path.name}")
            os.replace(temp, path)
            return
        except (HTTPError, URLError, TimeoutError, ConnectionError, IOError) as exc:
            print(f"retry {path.name} after {type(exc).__name__}: {exc}", flush=True)
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"could not download segment: {path.name}")


def download_parallel(entry, destination, workers):
    name = entry["Path"]
    expected_size = entry["Size"]
    expected_hash = entry["Sha256"]
    final = destination / name
    if final.exists() and final.stat().st_size == expected_size and digest(final) == expected_hash:
        print(f"verified existing {name}", flush=True)
        return
    if final.exists():
        raise ValueError(f"existing {final} has the wrong size/hash; inspect it before retrying")
    pieces = destination / (name + ".segments")
    pieces.mkdir(exist_ok=True)
    segment_size = 64 * 1024 * 1024
    count = math.ceil(expected_size / segment_size)
    url = BASE + quote(name)
    tasks = [(url, pieces / f"{i:04d}", i * segment_size,
              min(expected_size - 1, (i + 1) * segment_size - 1)) for i in range(count)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(lambda task: download_piece(*task), tasks), 1):
            if i % max(1, workers) == 0 or i == count:
                print(f"{name}: {i}/{count} segments", flush=True)
    part = destination / (name + ".part")
    with part.open("wb") as out:
        for _, path, _, _ in tasks:
            with path.open("rb") as source:
                while chunk := source.read(8 * 1024 * 1024):
                    out.write(chunk)
    if part.stat().st_size != expected_size or digest(part) != expected_hash:
        raise ValueError(f"size/SHA-256 mismatch: {name}")
    os.replace(part, final)
    for _, path, _, _ in tasks:
        path.unlink()
    pieces.rmdir()
    print(f"verified {name} ({expected_size} bytes)", flush=True)


def download(entry, destination):
    name = entry["Path"]
    expected_size = entry["Size"]
    expected_hash = entry["Sha256"]
    final = destination / name
    part = destination / (name + ".part")
    if final.exists() and final.stat().st_size == expected_size and digest(final) == expected_hash:
        print(f"verified existing {name}", flush=True)
        return
    if final.exists():
        raise ValueError(f"existing {final} has the wrong size/hash; inspect it before retrying")
    url = BASE + quote(name)
    for attempt in range(8):
        offset = part.stat().st_size if part.exists() else 0
        if offset > expected_size:
            raise ValueError(f"partial file larger than expected: {part}")
        if offset == expected_size:
            break
        headers = {"User-Agent": "LeasePath/0.1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urlopen(Request(url, headers=headers), timeout=45) as response:
                if offset and response.status != 206:
                    raise ValueError(f"server did not honor HTTP Range for {name}")
                with part.open("ab") as out:
                    while chunk := response.read(4 * 1024 * 1024):
                        out.write(chunk)
            print(f"received {name}: {part.stat().st_size}/{expected_size}", flush=True)
        except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
            print(f"retry {name} after {type(exc).__name__}: {exc}", flush=True)
            time.sleep(min(2 ** attempt, 30))
    if not part.exists() or part.stat().st_size != expected_size:
        raise RuntimeError(f"download incomplete: {name}")
    if digest(part) != expected_hash:
        raise ValueError(f"SHA-256 mismatch: {name}")
    os.replace(part, final)
    print(f"verified {name} ({expected_size} bytes)", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("models/Qwen2.5-3B-Instruct"))
    parser.add_argument("--weights", action="store_true", help="Include the 6.17 GB FP16 weight shards")
    parser.add_argument("--jobs", type=int, default=6, help="Parallel weight segments (default: 6)")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 16:
        parser.error("--jobs must be between 1 and 16")
    args.output.mkdir(parents=True, exist_ok=True)
    with urlopen(Request(API, headers={"User-Agent": "LeasePath/0.1"}), timeout=30) as response:
        metadata = json.load(response)
    entries = {entry["Path"]: entry for entry in metadata["Data"]["Files"]}
    selected = FILES if args.weights else {n for n in FILES if not n.endswith(".safetensors")}
    for name in sorted(selected):
        if name not in entries:
            raise ValueError(f"missing from ModelScope manifest: {name}")
    manifest_path = args.output / "leasepath_source_manifest.json"
    manifest = {
        "model_id": MODEL_ID,
        "source": "ModelScope",
        "revision": REVISION,
        "source_file_revisions": {name: entries[name]["Revision"] for name in sorted(selected)},
        "files": {name: {"size": entries[name]["Size"], "sha256": entries[name]["Sha256"]}
                  for name in sorted(selected)},
        "weight_download_requested": args.weights,
    }
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        for name, info in old["files"].items():
            if name in manifest["files"] and manifest["files"][name] != info:
                raise ValueError(f"source file changed since previous fetch: {name}")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    for name in sorted(selected):
        if name.endswith(".safetensors"):
            download_parallel(entries[name], args.output, args.jobs)
        else:
            download(entries[name], args.output)


if __name__ == "__main__":
    main()
