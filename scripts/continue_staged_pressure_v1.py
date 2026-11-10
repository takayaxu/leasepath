"""Bounded unattended continuation of the frozen matrix; stop on first failure.

Preparation may resume only on its preregistered per-invocation alarm. Native
failures, audit failures and resource/quality failures never trigger a retry.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OLD = Path('/dev/shm/leasepath-preexec-v2-20261110')


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def schedule(cells):
    expected = [(c, n) for n in (3, 6, 10) for c in (24, 32, 64)]
    if [(r['capacity_blocks'], r['concurrency']) for r in cells] != expected:
        raise ValueError('matrix order differs from prospective registration')
    steps, traced = [], set()
    for c, n in expected:
        if c not in traced:
            steps.append({'capacity': c, 'concurrency': 10, 'diagnostic': True})
            traced.add(c)
        steps.append({'capacity': c, 'concurrency': n, 'diagnostic': False})
    return steps


def run_command(command, log, env, timeout_s):
    with log.open('w') as stream:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return child.wait(timeout=max(1, timeout_s))
        except BaseException:
            os.killpg(child.pid, signal.SIGINT)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise


def same_process(pid, start_ticks):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return int(stat[19]) == start_ticks and stat[0] != 'Z'
    except FileNotFoundError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    def interrupt(signum, frame):
        raise InterruptedError('continuation interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    plan = read(args.plan)
    evidence = Path(plan['evidence']); evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (evidence / 'state.json').exists():
            raise ValueError('an existing supervised attempt cannot be rerun/replaced')
        deadline = time.monotonic() + plan['overall_wall_budget_s']
        prepared = OLD / 'staged-native-v1'
        state = {'status': 'preparing_model', 'pid': os.getpid(), 'started_wall_ns': time.time_ns(),
                 'plan_sha256': sha(args.plan), 'steps': [], 'new_model_generations': 0,
                 'natural_JCT_passed': False}
        def save():
            try:
                live = read(plan['staging_checkpoint'])
                state['model_preparation'] = {key: live[key] for key in ('status', 'bytes', 'total_elapsed_s')}
            except FileNotFoundError:
                pass
            state['updated_utc'] = datetime.now(timezone.utc).isoformat()
            write(evidence / 'state.json', state)
            if plan.get('report'):
                model = state.get('model_preparation', {})
                rows = ['| 步骤 | 容量 | 并发 | 状态 |', '|---|---:|---:|---|']
                rows += [f"| {r['name']} | {r['capacity']} | {r['concurrency']} | {r['status']} |" for r in state['steps']]
                text = ('# 固定压力矩阵续接状态\n\n'
                    f"更新时间：{state['updated_utc']}。当前状态：**{state['status']}**。\n\n"
                    f"模型准备：{model.get('status', 'pending')}；已确认 {model.get('bytes', 0):,} / 65,535,970,144 字节。"
                    f"累计准备时间 {model.get('total_elapsed_s', 0):.1f} 秒，独立于自然 JCT。\n\n"
                    f"已汇总新增生成预约：{state['new_model_generations']}（运行中步骤可能尚未更新，失败预约也计入预算）。"
                    f"九格自然 JCT 全部通过：{state['natural_JCT_passed']}。暂不宣称新增端到端收益；完整单元需再做配对统计。\n\n"
                    '等成本分布 H30/tau10、GBDT、LRU/3秒点预测对照、128-token 块、FCFS、输入及工具控制均沿用冻结方案。'
                    '次序为容量24/32/64 × 并发3/6/10，先完成每个容量的10并发原生诊断，再进入自然计时；任一执行或独立审计失败即停。'
                    '共享服务器不具有独占 CPU/内存带宽。本轮是已知开发题压力测试，独立母题与真实 agent 循环尚未进入模型验收。\n\n'
                    + '\n'.join(rows) + '\n\n'
                    f"详细状态：[state.json]({evidence / 'state.json'})；运行计划：[plan.json]({args.plan.resolve()})。\n")
                if state.get('error'):
                    text += '\n停止原因：`' + state['error'] + '`。\n'
                if state.get('archive'):
                    text += '\n原始证据已校验归档，审计在清理前完成：`' + state['archive'] + '`。\n'
                if state.get('archive_or_cleanup_error'):
                    text += '\n归档/清理异常：`' + state['archive_or_cleanup_error'] + '`；保留自有 tmpfs 供核对。\n'
                report = Path(plan['report'])
                temporary = report.with_suffix('.tmp')
                temporary.write_text(text)
                temporary.replace(report)
        def frozen():
            if sha(args.plan) != state['plan_sha256']:
                raise ValueError('continuation registration changed')
            for path, digest in plan['source_sha256'].items():
                if sha(path) != digest:
                    raise ValueError('prospective source/config changed: ' + path)
        def remaining(cap):
            value = min(cap, deadline - time.monotonic())
            if value <= 0:
                raise TimeoutError('overall continuation budget exhausted')
            return value
        save()
        try:
            frozen()
            stage_plan = Path(plan['staging_plan'])
            stage_checkpoint = Path(plan['staging_checkpoint'])
            # Adopt the already running preparation only by PID + kernel start
            # time. No signalling or touching another process on PID reuse.
            while same_process(plan['preparation_pid'], plan['preparation_start_ticks']):
                remaining(1)
                time.sleep(5)
                if int(time.monotonic()) // 60 != int(time.monotonic() - 5) // 60:
                    save()
            staging = read(stage_checkpoint)
            attempt = 0
            while staging['status'] != 'complete':
                if staging['status'] != 'paused' or staging.get('error') != 'TimeoutError: bounded preparation interrupted: signal 14':
                    raise RuntimeError('preparation stopped for a reason other than its scheduled slice budget')
                if staging['total_elapsed_s'] >= read(stage_plan)['total_budget_s']:
                    raise TimeoutError('cumulative model preparation budget exhausted; prefix retained')
                frozen(); attempt += 1
                run_command([sys.executable, str(ROOT / 'scripts/stage_pressure_model_v1.py'),
                             '--plan', str(stage_plan), '--checkpoint', str(stage_checkpoint)],
                            stage_checkpoint.parent / f'staging-continuation-{attempt}.log', os.environ.copy(),
                            remaining(read(stage_plan)['attempt_budget_s'] + 60))
                staging = read(stage_checkpoint)
            # The complete checkpoint becomes immutable before native source
            # hashing; subsequent progress never writes to this file.
            final_checkpoint = evidence / 'model-checkpoint-complete.json'
            shutil.copyfile(stage_checkpoint, final_checkpoint)
            frozen()
            rc = run_command([sys.executable, str(ROOT / 'scripts/prepare_pressure_staged_native_v1.py'),
                              '--stage-plan', str(stage_plan), '--checkpoint', str(final_checkpoint),
                              '--output', str(prepared)], evidence / 'prepare-native.log', os.environ.copy(), remaining(300))
            if rc:
                raise RuntimeError('native preparation rejected; see prepare-native.log')
            runtime = OLD / 'runtime'
            env = {**os.environ, 'CUDA_VISIBLE_DEVICES': '5,6,7', 'PYTHONHOME': str(runtime),
                   'PYTHONPATH': f'{runtime}/lib/python3.13/site-packages:{ROOT}/.artifacts/prediction-holdout-deps:{ROOT}/src',
                   'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                   'MKL_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false'}
            interpreter = str(runtime / 'bin/python3.13')
            base = read(Path(plan['matrix_plan']))
            steps = schedule(base['matrix']['cells'])
            if sum(sum(b['generation_limits']['total'] for b in c['batches']) for c in base['matrix']['cells']) != 6156:
                raise ValueError('natural matrix quota differs')
            diagnostics = {}
            for step in steps:
                frozen()
                c, n, diagnostic = step['capacity'], step['concurrency'], step['diagnostic']
                name = f'diagnostic-{c}-10' if diagnostic else f'natural-{c}-{n}'
                directory = prepared / name
                command = [interpreter, str(ROOT / 'scripts/run_qwen_preexec_pressure_v4.py'),
                           '--plan', str(prepared / f'cells/capacity-{c}-concurrency-{n}.json'),
                           '--preparation', str(prepared / 'preparation'),
                           '--runtime-copy', str(prepared / 'preparation/runtime-copy.json'),
                           '--output', str(directory), '--input-preparation', str(OLD / 'tool-preflight-v2/inputs'),
                           '--tool-preflight', str(OLD / 'qualification')]
                if diagnostic:
                    command += ['--diagnostic']
                    trace = prepared / f'{name}.strace'
                    command = ['strace', '-ff', '-ttt', '-yy', '-s', '4096', '-e',
                               'trace=%file,read,pread64,mmap,execve,wait4,mlock,mlockall,munlock,munlockall,set_mempolicy,get_mempolicy,prctl,sched_setaffinity,prlimit64',
                               '-o', str(trace), *command]
                else:
                    command += ['--trace-run', str(diagnostics[c])]
                record = {**step, 'name': name, 'path': str(directory), 'status': 'running',
                          'started_wall_ns': time.time_ns(), 'command': command}
                state['steps'].append(record); state['status'] = name; save()
                rc = run_command(command, prepared / f'{name}.log', env,
                                 remaining(plan['diagnostic_wall_budget_s'] if diagnostic else plan['natural_cell_wall_budget_s']))
                record.update(returncode=rc, finished_wall_ns=time.time_ns())
                if directory.exists():
                    state['new_model_generations'] = sum(len(p.read_text().splitlines()) for p in prepared.rglob('*ledger.jsonl'))
                if rc:
                    record['status'] = 'failed'; save()
                    raise RuntimeError('first native/resource/quality failure; no retry: ' + name)
                audit = [interpreter, str(ROOT / 'scripts/check_qwen_preexec_pressure_v3.py'),
                         '--run', str(directory), '--output', str(directory / 'acceptance.json')]
                if diagnostic:
                    audit += ['--trace-prefix', str(trace)]
                rc = run_command(audit, prepared / f'{name}-audit.log', env, remaining(1800))
                if rc:
                    record['status'] = 'audit_failed'; save()
                    raise RuntimeError('independent audit rejected; no later cell: ' + name)
                record['status'] = 'accepted'
                shutil.copyfile(directory / 'acceptance.json', evidence / f'{name}-acceptance.json')
                if diagnostic:
                    diagnostics[c] = directory
                save()
            state.update(status='matrix_accepted', natural_JCT_passed=True)
        except BaseException as exc:
            state.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
            if isinstance(exc, InterruptedError) and same_process(plan['preparation_pid'], plan['preparation_start_ticks']):
                os.kill(plan['preparation_pid'], signal.SIGINT)
        finally:
            state['finished_wall_ns'] = time.time_ns()
            state['new_model_generations'] = sum(len(p.read_text().splitlines()) for p in prepared.rglob('*ledger.jsonl'))
            state['generation_count_semantics'] = 'all reserved calls charged, including incomplete calls; no replacement'
            stage_root = Path(plan['staging_checkpoint']).parent
            if Path(plan['staging_checkpoint']).exists():
                shutil.copyfile(plan['staging_checkpoint'], evidence / 'model-checkpoint-last.json')
            for log in stage_root.glob('staging*.log'):
                shutil.copyfile(log, evidence / log.name)
            state['retained_tmpfs_roots'] = [str(OLD), str(Path(plan['staging_checkpoint']).parent)]
            state['cleanup_required_after_review'] = True
            state['archiving'] = bool(state['steps'])
            save()
            # Incomplete storage preparation remains resumable. After native
            # execution, archive every runtime/raw file and verify it again
            # before deleting only these owned tmpfs roots.
            if state['steps']:
                try:
                    archive = Path(plan['archive'])
                    rc = run_command([sys.executable, str(ROOT / 'scripts/archive_startup_diagnostic_v1.py'),
                        '--source', str(OLD), '--base', str(ROOT / '.artifacts/checkpoints/pressure-independent-2026-11-10'),
                        '--output', str(archive)], evidence / 'archive.log', os.environ.copy(), plan['archive_budget_s'])
                    if rc:
                        raise RuntimeError('raw archive verification failed; tmpfs retained')
                    proof = read(archive / 'restore-manifest.json')
                    for row in proof['files']:
                        file = OLD / row['relative']
                        if row['kind'] == 'symlink':
                            if not file.is_symlink() or os.readlink(file) != row['target']:
                                raise ValueError('symlink changed after archive')
                        elif sha(file) != row['sha256']:
                            raise ValueError('raw file changed after archive')
                    shutil.copyfile(archive / 'restore-manifest.json', evidence / 'raw-restore-manifest.json')
                    stage = read(plan['staging_plan'])
                    final = read(evidence / 'model-checkpoint-complete.json')
                    model = Path(stage['destination'])
                    current = model.stat()
                    if any(getattr(current, key) != value for key, value in final['destination_identity'].items()):
                        raise ValueError('model identity changed before owned cleanup')
                    if stage_root != model.parent or stage_root.parent != Path('/dev/shm'):
                        raise ValueError('unexpected model cleanup root')
                    shutil.rmtree(OLD)
                    shutil.rmtree(stage_root)
                    state.update(retained_tmpfs_roots=[], cleanup_required_after_review=False,
                                 archive=str(archive), archived_raw_verified_before_cleanup=True)
                except BaseException as exc:
                    state['archive_or_cleanup_error'] = f'{type(exc).__name__}: {exc}'
            state['archiving'] = False
            save()
        if state['status'] != 'matrix_accepted':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
