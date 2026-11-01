#!/usr/bin/env bash
# Local CPU preparation reproduction; never launches an NPU service.
set -euo pipefail
cd /home/ma-user/takaya/leasepath
leasepath_stage0_root=/home/ma-user/work/leasepath/real-development-v1/stage0-20261031-a
leasepath_stage0_repro=$(mktemp -d /home/ma-user/work/leasepath/real-development-v1/stage0-reproduce.XXXXXX)

# Resource refusal is expected while other workloads remain. All remaining
# commands are CPU-only and cannot bypass the resource gate to start a model.
if PYTHONPATH= .venv/bin/python scripts/preflight_real_development.py --output "$leasepath_stage0_repro/preflight"; then
    printf 'Preflight ready; this script still performs CPU preparation only.\n'
else
    leasepath_preflight_code=$?
    if [ "$leasepath_preflight_code" -ne 2 ]; then exit "$leasepath_preflight_code"; fi
fi

PYTHONPATH= .venv/bin/python scripts/prepare_real_development_model.py \
    --hf-metadata "$leasepath_stage0_root/manifests/model-source.json" \
    --mirror-metadata "$leasepath_stage0_root/manifests/modelscope-source.json" \
    --small-files "$leasepath_stage0_root/model-metadata" \
    --output /home/ma-user/work/leasepath/real-development-v1/models/Qwen2.5-Coder-32B-Instruct-381fc969

PYTHONPATH= .venv-agent/bin/python scripts/prepare_real_development_tasks.py \
    --parquet "$leasepath_stage0_root/raw/swebench-lite-test.parquet" \
    --dataset-revision 6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2 \
    --output "$leasepath_stage0_repro/tasks"

# Existing isolated environments and final dependency locks are listed beside
# this script. Python 3.9 is needed for the historical pytest source task.
PYTHONPATH= .venv-agent/bin/python scripts/check_real_development_tasks.py \
    --tasks "$leasepath_stage0_repro/tasks/tasks.json" \
    --environments "$leasepath_stage0_root/environments" \
    --pytest-python "$leasepath_stage0_root/environments/pytest-py39/bin/python" \
    --reference-parquet "$leasepath_stage0_root/raw/swebench-lite-test.parquet" \
    --output "$leasepath_stage0_repro/environment-checks"

PYTHONPATH= .venv/bin/python scripts/check_real_development_protocol.py \
    --output "$leasepath_stage0_repro/protocol"
PYTHONPATH= .venv/bin/python -m pytest -q
PYTHONPATH= .venv/bin/python scripts/check_prediction_d1.py --output "$leasepath_stage0_repro/d1"
PYTHONPATH= .venv/bin/python scripts/check_event_contract.py --output "$leasepath_stage0_repro/event-contract"
