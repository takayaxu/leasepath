"""Strict rejection-aware audit; archived model evidence is never live admission."""
import argparse
import json
from pathlib import Path

from audit_pressure_protocol_v3 import audit as protocol_audit, require
from stage_pressure_model_v1 import digest, identity
from verify_pressure_model_v3 import read_state
from pressure_staged_model_v2 import verify_staged_model


def archived_model(directory, archive_manifest, restore_proof):
    archive = json.loads(archive_manifest.read_text())
    restored = json.loads(restore_proof.read_text())
    require(restored['status'] == 'passed' and restored['manifest_sha256'] == digest(archive_manifest), 'restoration proof differs')
    require(restored['root'] == archive['source'], 'restored root differs')
    for kind in ('base', 'delta'):
        row = next(r for r in restored['archives'] if r['kind'] == kind)
        require(row['sha256'] == archive[kind + '_archive_sha256'], 'archive identity differs')
    rows = {r['relative']: r for r in archive['files']}
    root = Path(archive['source'])
    # The manifest and model provenance themselves must be restored exactly,
    # not just a statement that an old archive once existed.
    for name in ('run-manifest.json', 'frozen-plan.json', 'model-keeper.json', 'model-verification-staged.json'):
        path = directory / name
        require(digest(path) == rows[str(path.relative_to(root))]['sha256'], 'archived run evidence changed: ' + name)
    plan = json.loads((directory / 'frozen-plan.json').read_text())
    manifest = json.loads((directory / 'run-manifest.json').read_text())
    registration = plan['staged_model']
    stage_path = Path(registration['plan']); checkpoint = Path(registration['checkpoint'])
    require(digest(stage_path) == registration['plan_sha256'] and digest(checkpoint) == registration['checkpoint_sha256'], 'verification registration changed')
    stage = json.loads(stage_path.read_text()); state = read_state(checkpoint)
    for path, expected in stage['source_sha256'].items():
        require(digest(Path(path)) == expected, 'archived verifier source/evidence differs: ' + path)
    require(state['status'] == 'complete' and state['plan_sha256'] == registration['plan_sha256'], 'incomplete archived verification')
    require(state['bytes'] == stage['bytes'] == plan['memory_budget']['model_bytes'], 'model size differs')
    require(state['sha256'] == stage['sha256'] == plan['model_sha256'] and state['destination_full_hash_verified'], 'model hash differs')
    require(len(state['passes']) == 2, 'two complete passes required')
    for i, record in enumerate(state['passes']):
        require(record['pass'] == i+1 and record['sha256'] == state['sha256'] and record['bytes'] == state['bytes'] and record['all_copy_chunk_hashes_checked'], 'archived verification pass differs')
    require(state['destination_identity'] == stage['model_identity'], 'recorded model stat differs')
    source = json.loads(Path(stage['copy_plan']).read_text())
    require(identity(source['source']) == source['source_identity'], 'original source changed')
    evidence = {'full_sha256':state['sha256'], 'model_identity':state['destination_identity'],
                'verification':state['verification'], 'outside_JCT':True,
                'preparation_wall_s':stage['prior_total_elapsed_s']+state['elapsed_s'],
                'model_path':stage['model_path'], 'model_bytes':stage['bytes'],
                'registration':registration, 'new_model_generations':state['new_model_generations']}
    require(evidence == manifest['staged_model_verification'], 'run and archived verification differ')
    require(json.loads((directory / 'model-verification-staged.json').read_text()) == evidence, 'model verification file differs')
    return evidence


def audit(directory, trace_prefix=None, archive_manifest=None, restore_proof=None):
    plan = json.loads((directory / 'frozen-plan.json').read_text())
    manifest = json.loads((directory / 'run-manifest.json').read_text())
    if archive_manifest is not None:
        require(restore_proof is not None, 'archive mode needs verified restoration')
        verification = archived_model(directory, archive_manifest, restore_proof)
    else:
        verification = verify_staged_model(plan)
    require(manifest['staged_model_verification'] == verification, 'staging evidence differs')
    require(plan['memory_budget']['minimum_MemAvailable_gib'] == 220, 'total memory admission changed')
    require(plan['memory_budget']['max_tool_address_space_gib'] == 120, 'tool reserve changed')
    for key in ('memory_admission_before', 'memory_admission_after_model_lock'):
        row = manifest[key]
        require(row['owned_model_bytes'] == verification['model_bytes'], 'model credited more than once')
        require(row['mincore_all_pages_resident'], 'nonresident model credited')
        require(row['available_bytes']+row['owned_model_bytes'] == row['effective_available_bytes'], 'incorrect memory sum')
        require(row['required_total_bytes'] == 220*1024**3 and row['effective_available_bytes'] >= row['required_total_bytes'], 'memory admission not met')
    result = protocol_audit(directory, trace_prefix)
    result.update(staged_model_verified=verification, memory_accounting_verified=True,
                  model_evidence_scope='historical verified archive; no live model/path claim or inference admission' if archive_manifest else 'live model identity verified',
                  audit_revision='strict-prelaunch-schema-rejection-v1', prior_audit_failure_preserved=True,
                  reaudit_model_generations=0)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--trace-prefix', type=Path)
    parser.add_argument('--archive-manifest', type=Path)
    parser.add_argument('--restore-proof', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run, args.trace_prefix, args.archive_manifest, args.restore_proof)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:result[k] for k in ('status','new_generations','tasks_checked','feedback_consumed','actual_compute_calls_checked','prelaunch_schema_rejections','worker_trace_proof','model_evidence_scope')}), flush=True)
