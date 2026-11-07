#!/usr/bin/env bash
# Frozen order: physical/termination checks -> two protocol tasks -> one twelve-task batch.
set -euo pipefail
if (( $# != 6 )); then
  echo "usage: $0 AGENT_PYTHON NATIVE_SO MODEL_GGUF TOKENIZER_DIR TASK_DIR NEW_BATCH_DIR" >&2
  exit 2
fi
# Preserve the venv's interpreter symlink: dereferencing it loses pyvenv.cfg.
agent_python=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")
native_so=$(realpath "$2")
model_gguf=$(realpath "$3")
tokenizer_dir=$(realpath "$4")
task_dir=$(realpath "$5")
batch_dir=$(realpath -m "$6")
model_identity="${model_gguf%.gguf}.identity.json"
test ! -e "$batch_dir"
test -f "$model_identity"
test -f "$task_dir/preflight.json"
test -n "${CUDA_VISIBLE_DEVICES:-}"
mkdir -p "$batch_dir"
export CUDA_DEVICE_ORDER=PCI_BUS_ID PYTHONPATH=src
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
ledger=.artifacts/qwen-data-batch-v1/ledger.jsonl
"$agent_python" -u scripts/probe_qwen_data_backend.py \
 --library "$native_so" --model "$model_gguf" --tokenizer "$tokenizer_dir" \
 --model-identity "$model_identity" --output "$batch_dir/backend" --ledger "$ledger" \
 > "$batch_dir/backend.log" 2>&1
"$agent_python" -u scripts/run_qwen_data_pilot.py \
 --library "$native_so" --model "$model_gguf" --tokenizer "$tokenizer_dir" \
 --model-identity "$model_identity" --backend-pass "$batch_dir/backend" \
 --tasks "$task_dir" --task-ids ship_groups air_count --output "$batch_dir/protocol" \
 --ledger "$ledger" --lane probe > "$batch_dir/protocol.log" 2>&1
# The runner enforces the successful protocol summary before first pilot generation.
"$agent_python" -u scripts/run_qwen_data_pilot.py \
 --library "$native_so" --model "$model_gguf" --tokenizer "$tokenizer_dir" \
 --model-identity "$model_identity" --backend-pass "$batch_dir/backend" \
 --tasks "$task_dir" --output "$batch_dir/pilot" --protocol-pass "$batch_dir/protocol" \
 --ledger "$ledger" --lane pilot > "$batch_dir/pilot.log" 2>&1
"$agent_python" scripts/finalize_qwen_data_run.py \
 --run "$batch_dir/pilot" --tasks "$task_dir" --output "$batch_dir/delivery" \
 > "$batch_dir/finalize.log" 2>&1
