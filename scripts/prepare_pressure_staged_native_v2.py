"""Create new native manifests only after complete verified model staging."""
import argparse
import json
from pathlib import Path
import shutil

from pressure_staged_model_v2 import verify_staged_model, admit_staged_model
from stage_pressure_model_v1 import digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def prepare(stage_plan_path, checkpoint, output):
    stage_plan_path = stage_plan_path.resolve()
    checkpoint = checkpoint.resolve()
    registration = {'plan': str(stage_plan_path), 'plan_sha256': digest(stage_plan_path),
                    'checkpoint': str(checkpoint), 'checkpoint_sha256': digest(checkpoint)}
    old_root = Path('/dev/shm/leasepath-preexec-v2-20261110')
    plans = []
    for concurrency in (3, 6, 10):
        for capacity in (24, 32, 64):
            prior = ROOT / f'configs/prediction/preexec_pressure_cells_v2/capacity-{capacity}-concurrency-{concurrency}.json'
            plan = json.loads(prior.read_text())
            plan['staged_model'] = registration
            plan['model_preparation_recipe'] = 'resumable-two-pass-verification-v2'
            plan['prior_cell_plan'] = {'path': str(prior), 'sha256': digest(prior)}
            plan['model_memory_accounting'] = 'original220GiB total; subtract only already allocated owned mincore-resident model from additional MemAvailable requirement'
            verification = verify_staged_model(plan)
            plans.append((capacity, concurrency, plan))
    admitted = admit_staged_model(plans[0][2], verification)
    # No path mutation before all prerequisite evidence is accepted.
    output.mkdir(parents=True, exist_ok=False)
    preparation = output / 'preparation'; preparation.mkdir()
    for name in ('system-residency.json', 'native-identity.json', 'runtime-copy.json'):
        shutil.copyfile(old_root / 'preparation' / name, preparation / name)
    write_json(preparation / 'model-copy.json', {
        'dst': verification['model_path'], 'bytes': verification['model_bytes'],
        'sha256': verification['full_sha256'], 'storage': 'verified owned tmpfs copy; native keeper locks before load',
        'verification_origin': str(checkpoint), 'verification': verification})
    cells = output / 'cells'; cells.mkdir()
    for capacity, concurrency, plan in plans:
        write_json(cells / f'capacity-{capacity}-concurrency-{concurrency}.json', plan)
    record = {'status': 'prepared', 'staged_model': verification, 'memory_admission': admitted,
              'new_model_generations': 0, 'tool_qualification': str(old_root / 'qualification'),
              'input_preparation': str(old_root / 'tool-preflight-v2/inputs'),
              'runtime_copy': str(preparation / 'runtime-copy.json'),
              'next_run': '24blocks/10tasks diagnostic, max13probes+60live; independently audit before any natural JCT',
              'policy_or_tool_parameters_changed': False,
              'runner_sha256': digest(ROOT / 'scripts/run_qwen_preexec_pressure_v5.py'),
              'checker_sha256': digest(ROOT / 'scripts/check_qwen_preexec_pressure_v4.py')}
    write_json(output / 'preparation.json', record)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage-plan', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.stage_plan, args.checkpoint, args.output)), flush=True)
