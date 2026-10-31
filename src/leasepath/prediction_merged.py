"""Read frozen multi-round development views with explicit parent identities."""
from collections import Counter, defaultdict
import math
from pathlib import Path

from .d2_hazards import derive_hazard_rows
from .prediction_training import load_development_dataset, read_json, read_jsonl, sha256


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_inventory(root, inventory_path, *, expected_inventory_sha256=None,
                   expected_result_manifests=None):
    """Return workflow-balanced base features and separate evaluation metadata.

    No raw journal concatenation, feature imputation, or parent-name inference.
    Eventual execution quality and episode duration are evaluation metadata only.
    """
    root, inventory_path = Path(root), Path(inventory_path)
    if expected_inventory_sha256 is not None:
        require(sha256(inventory_path) == expected_inventory_sha256, 'inventory hash mismatch')
    inventory = read_json(inventory_path)
    loaded = {scope: {'samples': [], 'targets': [], 'metadata': {}, 'episodes': {},
                       'run_groups': {}, 'workflow_metadata': {}}
              for scope in ['all-observed', 'qualified']}
    input_hashes = {str(inventory_path.relative_to(root)): sha256(inventory_path)}
    round_names = set()
    for record in inventory['rounds']:
        name, result_dir = record['round'], root / record['result_root']
        require(name not in round_names, 'duplicate round')
        round_names.add(name)
        result_manifest = result_dir / 'SHA256SUMS.json'
        if expected_result_manifests is not None:
            require(name in expected_result_manifests and
                    sha256(result_manifest) == expected_result_manifests[name], 'result manifest hash mismatch')
        input_hashes[str(result_manifest.relative_to(root))] = sha256(result_manifest)
        for filename, expected in read_json(result_manifest).items():
            path = result_dir / filename
            require(sha256(path) == expected, 'round result hash mismatch: ' + str(path))
        quality_path = result_dir / 'execution-quality.json'
        input_hashes[str(quality_path.relative_to(root))] = sha256(quality_path)
        quality = read_json(quality_path)
        quality = quality['executions'] if isinstance(quality, dict) else quality
        by_workflow = {q['workflow_id']: q for q in quality}
        require(len(by_workflow) == len(quality), 'duplicate quality workflow')
        groups_path = result_dir / 'workflow-groups.json'
        if groups_path.exists():
            group_rows = read_json(groups_path)
            require(len({g['workflow_id'] for g in group_rows}) == len(group_rows), 'duplicate workflow group')
            require(all(by_workflow[g['workflow_id']]['group_id'] == g['group_id'] for g in group_rows),
                    'quality/group mapping mismatch')
            input_hashes[str(groups_path.relative_to(root))] = sha256(groups_path)
        for scope, view in record['views'].items():
            require(scope in loaded, 'unknown inventory view')
            data, merged = root / view['dataset_path'], loaded[scope]
            summary_path = data / 'summary.json'
            require(sha256(summary_path) == view['summary_sha256'], 'inventory summary hash mismatch')
            input_hashes[str(summary_path.relative_to(root))] = sha256(summary_path)
            for filename, expected in view['file_sha256'].items():
                path = data / filename
                require(sha256(path) == expected, 'inventory data hash mismatch: ' + str(path))
                input_hashes[str(path.relative_to(root))] = sha256(path)
            expected = {q['workflow_id'] for q in quality
                        if q['criteria'].get('backend_audit', q['criteria'].get('backend_event_audit', False))
                        and (scope == 'all-observed' or q['quality_passed'])}
            require(expected == set(view['workflow_ids']), 'quality/inventory workflow mismatch')
            samples, targets, summary = load_development_dataset(data, expected)
            require(not set(merged['run_groups']) & expected, 'duplicate workflow across rounds')
            for workflow in sorted(expected):
                q = by_workflow[workflow]
                merged['run_groups'][workflow] = q['group_id']
                merged['workflow_metadata'][workflow] = {
                    'round': name, 'quality_passed': q['quality_passed'], 'group_id': q['group_id']}
            groups = {by_workflow[w]['group_id'] for w in expected}
            require(groups == set(view['parent_group_ids']), 'parent group identity mismatch')
            labels = read_jsonl(data / 'episode_labels.jsonl')
            episodes = {(l['run_id'], l['idle_episode_id']): l for l in labels}
            require(len(episodes) == len(labels) and not merged['episodes'].keys() & episodes.keys(),
                    'duplicate episode identity')
            require(len(episodes) == view['episodes'] == summary['idle_episodes'], 'episode count mismatch')
            require(Counter(l['outcome'] for l in labels) == Counter(view['outcomes']), 'outcome count mismatch')
            sidecars = [filename for filename in view['file_sha256']
                        if filename in {'features-expansion-v1.jsonl', 'features-repair-v2.jsonl'}]
            require(len(sidecars) <= 1, 'ambiguous feature sidecar')
            extras = read_jsonl(data / sidecars[0]) if sidecars else []
            extra_by_id = {e['sample_id']: e for e in extras}
            ids = {s['sample_id'] for s in samples}
            require(len(ids) == len(samples) == view['snapshots'] == summary['snapshots'], 'sample count mismatch')
            require(not merged['metadata'].keys() & ids, 'duplicate sample across rounds')
            if sidecars:
                require(len(extra_by_id) == len(extras) and extra_by_id.keys() == ids, 'sidecar identity mismatch')
            # The exporter validates targets; cross-check their time origin against episodes too.
            derive_hazard_rows(samples, targets)
            target_by_id = {t['sample_id']: t for t in targets}
            require(len(derive_hazard_rows(samples, targets)) == view['risk_rows'], 'risk row count mismatch')
            weights, sampled_episodes = defaultdict(float), set()
            for s in samples:
                sid, workflow = s['sample_id'], s['run_id']
                ep = episodes[workflow, s['idle_episode_id']]
                sampled_episodes.add((workflow, s['idle_episode_id']))
                require(ep['start_ns'] <= s['snapshot_ns'] < ep['end_ns'], 'snapshot outside open episode')
                require(math.isclose(s['features']['elapsed_idle_s'],
                                     (s['snapshot_ns'] - ep['start_ns']) / 1e9, abs_tol=1e-9),
                        'elapsed time origin mismatch')
                target = target_by_id[sid]
                require(target['outcome'] == ep['outcome'] and math.isclose(
                    target['remaining_s'], (ep['end_ns'] - s['snapshot_ns']) / 1e9, abs_tol=1e-9),
                    'remaining time origin mismatch')
                require(by_workflow[workflow]['group_id'].startswith(s['source_group'] + ':'),
                        'parent/source identity mismatch')
                extra = extra_by_id[sid] if sidecars else None
                if extra is not None:
                    require(all(extra['features'][k] == v for k, v in s['features'].items()),
                            'sidecar/base features differ')
                    require(extra.get('group_id', by_workflow[workflow]['group_id']) == by_workflow[workflow]['group_id'],
                            'sidecar parent identity mismatch')
                merged['metadata'][sid] = {
                    **merged['workflow_metadata'][workflow], 'source_group': s['source_group'],
                    'episode_duration_s': ep['duration_ns'] / 1e9,
                    'prior_failed_tests': extra['features'].get('prior_failed_tests') if extra else None,
                    'command_category': extra['features'].get('command_category') if extra else None,
                    'current_suite_is_retry': extra['features'].get('current_suite_is_retry') if extra else None}
                weights[workflow] += s['sample_weight']
            require(sampled_episodes == episodes.keys(), 'episode without snapshots')
            require(all(math.isclose(w, 1.) for w in weights.values()), 'workflow weights must sum to one')
            merged['samples'].extend(samples)
            merged['targets'].extend(targets)
            merged['episodes'].update(episodes)
    if expected_result_manifests is not None:
        require(round_names == set(expected_result_manifests), 'round membership mismatch')
    for scope, merged in loaded.items():
        expected = inventory['combined_views'][scope]
        actual = {'workflows': len(merged['run_groups']), 'parent_tasks': len(set(merged['run_groups'].values())),
                  'episodes': len(merged['episodes']), 'snapshots': len(merged['samples']),
                  'risk_rows': len(derive_hazard_rows(merged['samples'], merged['targets']))}
        require(all(actual[k] == expected[k] for k in actual), 'combined inventory count mismatch')
        require(set(merged['run_groups'].values()) == set(expected['parent_group_ids']), 'combined parent mismatch')
        merged['summary'] = {**actual, 'outcomes': dict(Counter(l['outcome'] for l in merged['episodes'].values()))}
        require(Counter(merged['summary']['outcomes']) == Counter(expected['outcomes']), 'combined outcome mismatch')
    primary, qualified = loaded['all-observed'], loaded['qualified']
    require(qualified['run_groups'].items() <= primary['run_groups'].items(), 'qualified workflow not subset')
    by_id = {s['sample_id']: s for s in primary['samples']}
    targets = {t['sample_id']: t for t in primary['targets']}
    require(all(s == by_id.get(s['sample_id']) for s in qualified['samples']), 'qualified sample differs')
    require(all(t == targets.get(t['sample_id']) for t in qualified['targets']), 'qualified target differs')
    return loaded, input_hashes


def evaluation_strata(samples, metadata):
    """IDs for evaluation only; eventual-duration/quality bins never enter fit."""
    strata = defaultdict(list)
    for sample in samples:
        sid = sample['sample_id']
        meta = metadata[sid]
        duration = meta['episode_duration_s']
        duration_bin = 'lt1s' if duration < 1 else '1to3s' if duration < 3 else '3to10s' if duration < 10 else 'ge10s'
        keys = ['round:' + meta['round'], 'episode_duration:' + duration_bin,
                'execution_quality:' + ('passed' if meta['quality_passed'] else 'failed')]
        failure = meta['prior_failed_tests']
        keys.append('observed_prior_failure:' + ('unknown' if failure is None else 'yes' if failure > 0 else 'no'))
        if sample['features']['elapsed_idle_s'] >= 1:
            keys.append('elapsed_idle:ge1s')
        if meta['current_suite_is_retry'] is not None:
            keys.append('observed_current_retry:' + ('yes' if meta['current_suite_is_retry'] else 'no'))
        for key in keys:
            strata[key].append(sid)
    return dict(strata)
