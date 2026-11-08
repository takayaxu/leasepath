"""Read the new-server dataset without confusing batch IDs with task IDs."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

from .prediction_state import validate_features
from .prediction_training import read_jsonl, sha256


def require(value, message):
    if not value:
        raise ValueError(message)


def identity(*parts):
    return json.dumps(parts, separators=(',', ':'))


def select_parents(catalog, strata, seed):
    """Selection uses task identities/categories only, never durations or labels."""
    groups = {r['task']: r['source_group'] for r in catalog}
    require(len(groups) == len(catalog) == len(set(groups.values())), 'duplicate parent identity')
    seen, selected = set(), set()
    for name, spec in strata.items():
        tasks = spec['tasks']
        require(len(tasks) == len(set(tasks)) and not seen.intersection(tasks), 'overlapping strata')
        require(set(tasks) <= groups.keys(), 'unknown stratum task')
        count = spec['test_count']
        require(type(count) is int and 0 < count < len(tasks), 'invalid test allocation')
        ordered = sorted(tasks, key=lambda t: (hashlib.sha256(f'{seed}:{groups[t]}'.encode()).hexdigest(), t))
        selected.update(ordered[:count])
        seen.update(tasks)
    require(seen == groups.keys(), 'strata do not cover catalog')
    return {t: 'test' if t in selected else 'train' for t in sorted(groups)}


def load_dataset(path, expected_hashes):
    path = Path(path)
    for name, digest in expected_hashes.items():
        require(Path(name).name == name and sha256(path / name) == digest, 'dataset hash differs: ' + name)
    for line in (path / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split()
        require(name in expected_hashes and expected_hashes[name] == digest, 'manifest differs: ' + name)
    catalog = read_jsonl(path / 'catalog.jsonl')
    owners = {(r['run_id'], r['task']): r for r in catalog}
    require(len(owners) == len(catalog), 'duplicate catalog workflow')
    episode_owners = {}
    for event in read_jsonl(path / 'events.jsonl'):
        if event['event_type'] == 'idle_start':
            key = (event['run_id'], event['idle_episode_id'])
            owner = (event['run_id'], event['workflow_id'])
            require(key not in episode_owners and owner in owners, 'invalid episode owner')
            episode_owners[key] = owner
    episodes = {}
    for row in read_jsonl(path / 'episodes.jsonl'):
        key = (row['run_id'], row['idle_episode_id'])
        require(key not in episodes, 'duplicate episode')
        episodes[key] = row
    require(episodes.keys() == episode_owners.keys(), 'episode mapping differs')
    workflow_episodes = defaultdict(set)
    for key, owner in episode_owners.items():
        workflow_episodes[owner].add(key)
    require(workflow_episodes.keys() == owners.keys(), 'missing catalog workflow')
    for owner, keys in workflow_episodes.items():
        require(len(keys) == owners[owner]['generations'], 'generation/episode count differs')
    features = read_jsonl(path / 'features.jsonl')
    targets = read_jsonl(path / 'targets.jsonl')
    def key(row):
        return row['run_id'], row['idle_episode_id'], row['snapshot_ns']
    labels = {key(r): r for r in targets}
    require(len(labels) == len(targets) == len(features), 'duplicate or missing target')
    require(len({key(r) for r in features}) == len(features) and {key(r) for r in features} == labels.keys(),
            'snapshot/target keys differ')
    counts = Counter((r['run_id'], r['idle_episode_id']) for r in features)
    require(counts.keys() == episodes.keys(), 'episode lacks snapshot')
    samples, converted, metadata, run_groups = [], [], {}, {}
    for row in features:
        k = key(row)
        epkey = k[:2]
        ep = episodes[epkey]
        target = labels[k]
        require(ep['start_ns'] <= k[2] < ep['end_ns'], 'snapshot outside episode')
        require(target['outcome'] == ep['outcome'] and target['remaining_ns'] == ep['end_ns'] - k[2],
                'target inconsistent with episode')
        require(math.isclose(row['features']['elapsed_idle_s'], (k[2] - ep['start_ns']) / 1e9),
                'elapsed time inconsistent with episode')
        validate_features(row['features'])
        owner = episode_owners[epkey]
        parent = owners[owner]
        workflow, sid = identity(*owner), identity(*k)
        run_groups[workflow] = parent['source_group']
        samples.append({'sample_id': sid, 'run_id': workflow, 'idle_episode_id': k[1],
                        'snapshot_ns': k[2], 'features': row['features'],
                        'sample_weight': 1 / len(workflow_episodes[owner]) / counts[epkey],
                        'split': 'internal_parent_holdout_pending_assignment'})
        converted.append({'sample_id': sid, 'outcome': target['outcome'],
                          'remaining_s': target['remaining_ns'] / 1e9})
        metadata[sid] = {'task': parent['task'], 'source_group': parent['source_group'],
                         'source_run_id': owner[0], 'episode_duration_s': ep['duration_ns'] / 1e9}
    return catalog, samples, converted, metadata, run_groups


def partition(samples, targets, metadata, assignments):
    labels = {r['sample_id']: r for r in targets}
    require(len(labels) == len(targets) and labels.keys() == {r['sample_id'] for r in samples},
            'partition label alignment differs')
    parts = {name: ([], []) for name in ['train', 'test']}
    for row in samples:
        split = assignments[metadata[row['sample_id']]['task']]
        sample = {**row, 'split': split}
        parts[split][0].append(sample)
        parts[split][1].append(labels[row['sample_id']])
    train, test = parts['train'][0], parts['test'][0]
    require(train and test and not {s['run_id'] for s in train}.intersection(s['run_id'] for s in test),
            'empty or overlapping parent holdout')
    return parts
