"""Wait for the one registered pilot, audit worker access, score, and preserve it.

Never starts or retries model execution. All timing is diagnostic only.
"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def process_live(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def report(plan, state):
    root = Path(__file__).resolve().parents[1]
    work = Path(plan['work'])
    task_rows = []
    for file in sorted(work.glob('batch-*/batch/*-quality.json')):
        r = read(file)
        task_rows.append({'task': r['task'], 'success': r['success'], 'generations': r['generations'],
                          'tools': len(r['tools']), 'error': r.get('error')})
    loops = sum(read(f)['certified_loops'] for f in work.glob('batch-*/loop-audit.json'))
    reservations = sum(len(p.read_text().splitlines()) for p in work.rglob('*ledger.jsonl'))
    bjt = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
    text = ('# 独立开发题真实 agent 循环试点\n\n'
            f'更新时间：{bjt}。状态：**{state.get("status", "starting")}**。\n\n'
            '本轮使用 Qwen2.5-Coder-32B F16 原生后端，32 块、3 并发、冻结等成本分布 H30/tau10。'
            '只运行 6 个独立 development 来源族，旧50题排除，6个 holdout 来源族尚未进入模型。\n\n'
            f'模型完整校验进度：{state.get("model_bytes_verified", 0):,} / {plan["model"]["bytes"]:,} 字节。'
            f'生成预约（含协议检查与失败）：{reservations} / 85；任务轨迹 {len(task_rows)} / 6；'
            f'任务成功 {sum(r["success"] for r in task_rows)}；已认证实质修订循环 {loops}。\n\n'
            '**本轮不作严格自然 JCT 验收。** 磁盘读取和缺页完整记录；计时受追踪与共享主机影响。'
            '模型来源、预算、真实反馈、原生输出与合法 KV 动作仍需核验。\n\n')
    if task_rows:
        text += '| 任务 | 成功 | 生成 | 工具调用 | 错误 |\n|---|---|---:|---:|---|\n'
        for r in task_rows:
            text += f'| {r["task"]} | {r["success"]} | {r["generations"]} | {r["tools"]} | {r["error"] or ""} |\n'
    if state.get('error'):
        text += '\n停止原因：`' + state['error'] + '`。\n'
    evaluation = Path(plan['evidence']) / 'prediction-evaluation.json'
    if evaluation.exists():
        d = read(evaluation)
        text += (f'\n冻结预测器迁移评估：{d["episodes"]} 个观测 idle episode、{d["snapshots"]} 个决策时快照，'
                 f'事件计数 {d["event_counts"]}。每个 episode 总权重相同；这些是相关开发样本，不能作为留出泛化结论。\n')
        text += '\n| 预测器 | 快照 | 未见状态回退快照 | 3 秒 Brier(U) | 30 秒 Brier(U) |\n|---|---:|---:|---:|---:|\n'
        for name, m in d['metrics'].items():
            text += f'| {name} | {m["snapshots"]} | {m["unseen_state_fallback_snapshots"]} | {m["horizons_s"]["3"]["Brier_U"]} | {m["horizons_s"]["30"]["Brier_U"]} |\n'
    text += '\n详细记录：results/agent/independent_loop_pilot_v1_2026-11-10/。\n'
    path = root / 'reports/independent_loop_pilot_2026-11-10.md'
    temp = path.with_suffix('.tmp'); temp.write_text(text); temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    evidence, work = Path(plan['evidence']), Path(plan['work'])
    root = Path(__file__).resolve().parents[1]
    deadline = time.monotonic() + plan['overall_wall_budget_s'] + 1200
    finish = {'status': 'waiting_for_registered_pilot', 'new_model_generations': 0}
    write(evidence / 'finalization-state.json', finish)
    try:
        while True:
            state = read(evidence / 'state.json')
            report(plan, state)
            if (work / 'run-manifest.json').exists():
                launch = read(evidence / 'launch.json')
                if not process_live(launch['supervisor_pid']):
                    break
            if time.monotonic() > deadline:
                raise TimeoutError('pilot finalization wait exceeded; no retry')
            time.sleep(10)
        proof = []
        for file in sorted(work.glob('batch-*/batch/*-quality.json')):
            task = read(file)
            for call in task['tools']:
                if 'child_pid' not in call:
                    continue
                trace = Path(plan['trace_prefix'] + '.' + str(call['child_pid']))
                text = trace.read_text(errors='replace')
                if 'leasepath.independent_agent_worker' not in text or 'execve(' not in text:
                    raise ValueError('candidate worker exec trace missing')
                if '+++ exited with 0 +++' not in text:
                    raise ValueError('candidate worker did not exit cleanly')
                if 'references.private.json' in text or 'independent_agent_reference' in text:
                    raise ValueError('reference material accessed by candidate worker')
                proof.append({'task': task['task'], 'child_pid': call['child_pid'], 'trace_sha256': sha(trace),
                              'no_reference_access_observed': True, 'worker_evidence': call.get('worker_evidence'),
                              'child_lifetime': call.get('child_lifetime')})
        write(evidence / 'worker-trace-audit.json', {'status': 'passed', 'workers': proof,
            'scope': 'actual candidate-worker file trace; no claim of a filesystem security sandbox',
            'natural_JCT_acceptance': False})
        if list(work.glob('batch-*/decision-audit.json')):
            command = [sys.executable, str(root / 'scripts/evaluate_independent_predictions_v1.py'),
                       '--run', str(work), '--plan', str(args.plan), '--output', str(evidence / 'prediction-evaluation.json')]
            with (evidence / 'prediction-evaluation.log').open('x') as log:
                result = subprocess.run(command, stdout=log, stderr=log, timeout=300)
            if result.returncode:
                raise RuntimeError('frozen prediction evaluation failed; preserve existing outputs')
        dest = evidence / 'raw'
        shutil.copytree(work, dest)
        files = {str(p.relative_to(work)): sha(p) for p in work.rglob('*') if p.is_file()}
        for relative, expected in files.items():
            if sha(dest / relative) != expected:
                raise ValueError('raw evidence copy mismatch')
        traces = {str(p): sha(p) for p in evidence.glob('native.strace.*')}
        write(evidence / 'raw-sha256.json', {'files': files, 'traces': traces,
              'source_retained': str(work), 'copy_verified': True})
        finish.update(status='completed', raw_copy=str(dest), raw_files=len(files), trace_files=len(traces))
        report(plan, read(evidence / 'state.json'))
    except BaseException as exc:
        finish.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        write(evidence / 'finalization-state.json', finish)


if __name__ == '__main__':
    main()
