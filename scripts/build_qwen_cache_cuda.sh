#!/usr/bin/env bash
set -euo pipefail
if (( $# != 3 )); then
    echo "usage: $0 PINNED_LLAMA_SOURCE PINNED_LLAMA_BIN OUTPUT_SO" >&2
    exit 2
fi
source_dir=$(realpath "$1")
lib_dir=$(realpath "$2")
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
g++ -std=c++17 -O2 -Wall -Wextra -fPIC -shared -Wl,--no-undefined \
  -I"$source_dir/include" -I"$source_dir/src" -I"$source_dir/ggml/include" \
  -I"$source_dir/vendor/nlohmann" "$script_dir/../native/qwen_cache_cuda.cpp" \
  -L"$lib_dir" -Wl,-rpath,"$lib_dir" -lllama -lggml -lggml-base -o "$3"
