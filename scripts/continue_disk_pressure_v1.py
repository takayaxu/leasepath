"""Single bounded attempt: continuous model locking, native diagnostics, matrix.

Only storage admission changes. Failed attempts and all reservations are retained.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from continue_staged_pressure_v2 import schedule
from pressure_disk_keeper_v1 import process_identity
from pressure_disk_model_v1 import verify_disk_model, admit_disk_model
from stage_pressure_model_v1 import digest, write_json

ROOT = Path(__file__).resolve().parents[1]
OLD = Path('/dev/shm/leasepath-preexec-v2-20261110')


def read(path):
    return json.loads(Path(path).read_text())


def comparison(result):
    rows = result['metrics']
    arms = tuple(dict.fromkeys(r['policy'] for r in rows))
    if set(arms) != {'retired_first_lru', 'gbdt_point', 'gbdt_distribution'} or len(rows) != 18:
        raise ValueError('incomplete natural comparison')
    if any(sorted(r['cycle'] for r in rows if r['policy'] == arm) != list(range(6)) for arm in arms):
        raise ValueError('unpaired natural comparison')
    means = {arm: {key: sum(r[key] for r in rows if r['policy'] == arm) / 6
                   for key in ('mean_jct_s', 'cached_tokens', 'prefill_tokens', 'prefill_s', 'decode_s')}
             for arm in arms}
    distribution = means['gbdt_distribution']
    benefits = {}
    for arm in arms:
        if arm == 'gbdt_distribution':
            continue
        baseline = means[arm]
        benefits[arm] = {'additional_hit_tokens_per_batch': distribution['cached_tokens'] - baseline['cached_tokens'],
                         'prefill_s_saved_per_batch': baseline['prefill_s'] - distribution['prefill_s'],
                         'mean_JCT_s_saved': baseline['mean_jct_s'] - distribution['mean_jct_s'],
                         'mean_JCT_reduction_pct': 100*(baseline['mean_jct_s'] - distribution['mean_jct_s']) / baseline['mean_jct_s'],
                         'paired_JCT_s_saved': [next(r['mean_jct_s'] for r in rows if r['cycle'] == i and r['policy'] == arm)
                                               - next(r['mean_jct_s'] for r in rows if r['cycle'] == i and r['policy'] == 'gbdt_distribution')
                                               for i in range(6)]}
    return {'capacity': result['capacity'], 'concurrency': result['concurrency'], 'batches': len(rows),
            'means': means, 'distribution_benefits': benefits,
            'scope': 'six paired crossover cycles on shared server; no exclusive-bandwidth claim'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    evidence = Path(plan['evidence']); evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / 'supervisor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (evidence / 'state.json').exists():
            raise ValueError('existing attempt cannot be rerun')
        work = Path(plan['work']); work.mkdir(exist_ok=False)
        deadline = time.monotonic() + plan['overall_wall_budget_s']
        state = {'status': 'preparing', 'process': process_identity(os.getpid()),
                 'started_wall_ns': time.time_ns(), 'plan_sha256': digest(args.plan),
                 'steps': [], 'new_model_generations': 0, 'natural_JCT_passed': False}
        keeper = None
        def frozen():
            if digest(args.plan) != state['plan_sha256']:
                raise ValueError('supervisor registration changed')
            for path, expected in plan['source_sha256'].items():
                if digest(path) != expected:
                    raise ValueError('registered source/config changed: ' + path)
        def save():
            state['updated_utc'] = datetime.now(timezone.utc).isoformat()
            state['new_model_generations'] = sum(len(p.read_text().splitlines()) for p in work.rglob('*ledger.jsonl'))
            if (work / 'keeper-progress.json').exists():
                state['model_preparation'] = read(work / 'keeper-progress.json')
            if state['steps']:
                progress = Path(state['steps'][-1]['path']) / 'progress.json'
                if progress.exists():
                    state['current_completed_batches'] = read(progress)['completed_batches']
            write_json(evidence / 'state.json', state)
            rows = ['| 步骤 | 状态 |', '|---|---|'] + [f"| {r['name']} | {r['status']} |" for r in state['steps']]
            model = state.get('model_preparation', {})
            body = (f"# 连续驻留压力矩阵\n\n更新时间：{state['updated_utc']}。状态：**{state['status']}**。\n\n"
                    f"模型锁定 {model.get('locked_bytes',0):,} / 65,535,970,144 字节；哈希第 {model.get('pass_number',1)} 遍，"
                    f"本遍已读 {model.get('bytes_hashed',0):,} 字节。新增生成预约 {state['new_model_generations']}。\n\n"
                    '等成本分布 H30/tau10、128-token 块、LRU/3秒点预测、FCFS、工具与题目顺序保持冻结。'
                    '24/32/64 块 × 3/6/10 并发，每格18批；各容量先通过新的完整协议追踪。'
                    '当前仅已知开发题；独立母题/留出及独立 agent 循环尚未执行。\n\n' + '\n'.join(rows) + '\n')
            if state.get('error'):
                body += '\n停止原因：`' + state['error'] + '`。\n'
            body += '\n各已验收自然单元的 comparison.json 分别记录命中 token、prefill 时间及平均 JCT 差值；完整矩阵未通过前不做总体收益结论。\n'
            report = Path(plan['report']); temp = report.with_suffix('.tmp'); temp.write_text(body); temp.replace(report)
        def interrupt(signum, frame):
            raise InterruptedError('supervisor stopped: ' + str(signum))
        signal.signal(signal.SIGINT, interrupt); signal.signal(signal.SIGTERM, interrupt)
        def command(argv, log, env, wall_s, need_keeper=False, archiving=False):
            end = time.monotonic() + wall_s
            if not archiving:
                end = min(deadline, end)
            with Path(log).open('w') as stream:
                child = subprocess.Popen(argv, cwd=ROOT, env=env, stdout=stream,
                                         stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    while child.poll() is None:
                        if time.monotonic() >= end:
                            raise TimeoutError('registered command deadline exceeded')
                        if need_keeper and keeper.poll() is not None:
                            raise RuntimeError('continuous model keeper exited')
                        save()
                        try:
                            child.wait(timeout=min(30, max(.1, end - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            pass
                    return child.returncode
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGINT)
                        try:
                            child.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL); child.wait()
        runtime = OLD / 'runtime'
        interpreter = str(runtime / 'bin/python3.13')
        env = {**os.environ, 'CUDA_VISIBLE_DEVICES': '5,6,7', 'PYTHONHOME': str(runtime),
               'PYTHONPATH': f'{runtime}/lib/python3.13/site-packages:{ROOT}/.artifacts/prediction-holdout-deps:{ROOT}/src',
               'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
               'MKL_NUM_THREADS': '1', 'TOKENIZERS_PARALLELISM': 'false'}
        save()
        try:
            frozen()
            if read(plan['historical_reaudit'])['status'] != 'passed' or not read(plan['tool_acceptance'])['natural_launch_allowed']:
                raise ValueError('independent audit prerequisite missing')
            prep_plan = {**plan['model_preparation'], 'supervisor_process': state['process']}
            prep_path = work / 'keeper-plan.json'; write_json(prep_path, prep_plan)
            ready = work / 'keeper-ready.json'
            log = (evidence / 'keeper.log').open('w')
            keeper = subprocess.Popen([interpreter, str(ROOT / 'scripts/pressure_disk_keeper_v1.py'),
                        '--plan', str(prep_path), '--progress', str(work / 'keeper-progress.json'), '--ready', str(ready)],
                        env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            log.close()
            prep_deadline = min(deadline, time.monotonic() + prep_plan['preparation_wall_budget_s'])
            state['status'] = 'locking_and_hashing_model'; save()
            while not ready.exists():
                if keeper.poll() is not None:
                    raise RuntimeError('model preparation failed; no retry; see keeper.log')
                if time.monotonic() >= prep_deadline:
                    raise TimeoutError('new model preparation budget exhausted; no inference')
                save(); time.sleep(5)
            registration = {'plan': str(prep_path), 'plan_sha256': digest(prep_path),
                            'ready': str(ready), 'ready_sha256': digest(ready)}
            for file in (prep_path, ready):
                shutil.copyfile(file, evidence / file.name)
            frozen()
            preparation = work / 'preparation'; preparation.mkdir()
            for name in ('system-residency.json', 'native-identity.json', 'runtime-copy.json'):
                shutil.copyfile(OLD / 'preparation' / name, preparation / name)
            cells = work / 'cells'; cells.mkdir()
            for n in (3, 6, 10):
                for c in (24, 32, 64):
                    prior = ROOT / f'configs/prediction/preexec_pressure_cells_v2/capacity-{c}-concurrency-{n}.json'
                    cell = read(prior)
                    cell.update(disk_model=registration, model_preparation_recipe='continuously-locked-original-two-full-hashes-v1',
                                prior_cell_plan={'path': str(prior), 'sha256': digest(prior)})
                    verification = verify_disk_model(cell); admit_disk_model(cell, verification)
                    write_json(cells / prior.name, cell)
            write_json(preparation / 'model-copy.json', {'dst': verification['model_path'],
                       'sha256': verification['full_sha256'], 'bytes': verification['model_bytes'],
                       'storage': 'original file; continuously locked by owned keeper; no model copy', 'verification': verification})
            base = read(plan['matrix_plan'])
            if sum(sum(b['generation_limits']['total'] for b in c['batches']) for c in base['matrix']['cells']) != 6156:
                raise ValueError('matrix quota differs')
            diagnostics = {}
            for step in schedule(base['matrix']['cells']):
                frozen()
                c, n, diagnostic = step['capacity'], step['concurrency'], step['diagnostic']
                name = f'diagnostic-{c}-10' if diagnostic else f'natural-{c}-{n}'
                directory = work / name
                argv = [interpreter, str(ROOT / 'scripts/run_qwen_preexec_pressure_v6.py'),
                        '--plan', str(cells / f'capacity-{c}-concurrency-{n}.json'),
                        '--preparation', str(preparation), '--runtime-copy', str(preparation / 'runtime-copy.json'),
                        '--output', str(directory), '--input-preparation', str(OLD / 'tool-preflight-v2/inputs'),
                        '--tool-preflight', plan['tool_qualification']]
                if diagnostic:
                    trace = work / f'{name}.strace'
                    argv = ['strace', '-ff', '-ttt', '-yy', '-s', '4096', '-e',
                            'trace=%file,read,pread64,mmap,execve,wait4,mlock,mlockall,munlock,munlockall,set_mempolicy,get_mempolicy,prctl,sched_setaffinity,prlimit64',
                            '-o', str(trace), *argv, '--diagnostic']
                else:
                    argv += ['--trace-run', str(diagnostics[c])]
                record = {**step, 'name': name, 'path': str(directory), 'status': 'running',
                          'command': argv, 'started_wall_ns': time.time_ns()}
                state['steps'].append(record); state['status'] = name
                state['current_completed_batches'] = 0; save()
                rc = command(argv, work / f'{name}.log', env,
                             plan['diagnostic_wall_budget_s'] if diagnostic else plan['natural_cell_wall_budget_s'], True)
                record.update(returncode=rc, finished_wall_ns=time.time_ns())
                if rc:
                    record['status'] = 'failed'; save()
                    raise RuntimeError('native/resource/quality failure; no replacement: ' + name)
                audit = [interpreter, str(ROOT / 'scripts/check_qwen_preexec_pressure_v6.py'),
                         '--run', str(directory), '--output', str(directory / 'acceptance.json')]
                if diagnostic:
                    audit += ['--trace-prefix', str(trace)]
                record['status'] = 'auditing'; save()
                rc = command(audit, work / f'{name}-audit.log', env, 1800, True)
                if rc:
                    record['status'] = 'audit_failed'; save()
                    raise RuntimeError('independent audit failed; no later cells: ' + name)
                accepted = read(directory / 'acceptance.json')
                shutil.copyfile(directory / 'acceptance.json', evidence / f'{name}-acceptance.json')
                record['status'] = 'accepted'
                if diagnostic:
                    diagnostics[c] = directory
                else:
                    write_json(evidence / f'{name}-comparison.json', comparison(accepted))
                save()
            state.update(status='matrix_accepted', natural_JCT_passed=True)
        except BaseException as exc:
            state.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
        finally:
            if keeper and keeper.poll() is None:
                keeper.terminate()
                try:
                    keeper.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    keeper.kill(); keeper.wait()
            state['finished_wall_ns'] = time.time_ns(); save()
            # Preserve raw failed or accepted samples; cleanup is a later verified
            # operation. No deletion of historical raw/runtime while it is needed.
            state['retained_tmpfs_root'] = str(OLD)
            try:
                rc = command([interpreter, str(ROOT / 'scripts/archive_startup_diagnostic_v1.py'),
                              '--source', str(OLD), '--base', str(ROOT / '.artifacts/checkpoints/pressure-independent-2026-11-10'),
                              '--output', plan['archive']], evidence / 'archive.log', env, plan['archive_budget_s'], archiving=True)
                if rc:
                    raise RuntimeError('archive failed; raw tmpfs retained')
                state['verified_archive'] = plan['archive']
            except BaseException as exc:
                state['archive_error'] = f'{type(exc).__name__}: {exc}'
            save()
        if state['status'] != 'matrix_accepted':
            raise SystemExit(1)


if __name__ == '__main__':
    main()
