"""Read-only behavioral reaudit of the first independent pilot; no inference.

Preserve the original audit failure. Verify Qwen's documented continuation
colon handling against the exact private runtime and actual prompt suffix.
"""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, file_digest
from audit_independent_agent_loop_v3 import audit, parse_native_final
from run_independent_loop_pilot_v1 import audit_decisions


def main():
    evidence = ROOT / 'results/agent/independent_loop_pilot_v1_2026-11-10'
    plan = json.loads((evidence / 'plan.json').read_text())
    old_hashes = json.loads((evidence / 'raw-sha256.json').read_text())['files']
    for name, digest in old_hashes.items():
        if file_digest(evidence / 'raw' / name) != digest:
            raise ValueError('original raw evidence changed')
    for path, digest in plan['source_sha256'].items():
        if file_digest(path) != digest:
            raise ValueError('original pilot source changed')
    out = evidence / 'reaudit-v1'
    out.mkdir(exist_ok=False)
    view = out / 'verified-view'
    shutil.copytree(evidence / 'raw', view)
    from qwen_agent.llm.fncall_prompts.qwen_fncall_prompt import QwenFnCallPrompt
    from qwen_agent.llm.schema import Message, ContentItem
    import qwen_agent.llm.fncall_prompts.qwen_fncall_prompt as parser_module
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(plan['tokenizer'], local_files_only=True)
    source = Path(parser_module.__file__)
    runtime = json.loads(Path(plan['runtime_manifest']).read_text())
    relative = str(source.relative_to(runtime['dst']))
    expected = next(row['sha256'] for row in runtime['files'] if row['relative'] == relative)
    if file_digest(source) != expected:
        raise ValueError('actual Qwen parser differs from frozen runtime')
    baseline = FrozenEqualCostBaseline(ROOT, ROOT / plan['baseline_manifest'])
    state = json.loads((view / 'run-manifest.json').read_text())
    receipts = []
    for directory in sorted(view.glob('batch-*')):
        batch = directory / 'batch'
        native = [json.loads(l)['data'] for l in (batch / 'raw/backend.jsonl').read_text().splitlines()
                  if json.loads(l)['kind'] == 'engine_request']
        inputs = {json.loads(l)['data']['request_id']: json.loads(l)['data']
                  for l in (batch / 'raw/controller.jsonl').read_text().splitlines()
                  if json.loads(l)['kind'] == 'request_input'}
        for quality in batch.glob('*-quality.json'):
            row = json.loads(quality.read_text())
            if not row['success']:
                continue
            last = [r for r in native if r['owner'] == row['owner']][-1]
            prompt = inputs[last['request_id']]['prompt']
            processed = QwenFnCallPrompt.postprocess_fncall_messages(
                [Message(role='assistant', content=[ContentItem(text=last['text'])])], parallel_function_calls=False)
            answer = ''.join(item.text for msg in processed for item in msg.content)
            if json.loads(answer.strip()) != row['final'] or parse_native_final(last['text'], prompt) != row['final']:
                raise ValueError('native final differs from actual Qwen parsing')
            receipts.append({'task': row['task'], 'request_id': last['request_id'],
                'actual_prompt_ends_FN_EXIT': prompt.rstrip().endswith('✿RETURN✿'),
                'actual_native_starts_colon': last['text'].startswith(':'),
                'actual_frozen_parser_matches': True})
        result = audit(batch, Path(plan['suite']), directory / 'ledger.jsonl', 32, tokenizer)
        (directory / 'loop-audit.json').write_text(json.dumps(result, indent=2) + '\n')
        decisions = audit_decisions(batch, baseline, state['runtime_compatibility'])
        (directory / 'decision-audit.json').write_text(json.dumps(decisions, indent=2) + '\n')
    for name, digest in old_hashes.items():
        if file_digest(evidence / 'raw' / name) != digest or file_digest(view / name) != digest:
            raise ValueError('raw contents changed during reaudit')
    summary = {'status': 'passed', 'model_generations': 0, 'original_status': state['status'],
        'original_error': state.get('error'), 'original_raw_unchanged': True,
        'original_failed_task_retained': True, 'holdout_model_execution': False,
        'frozen_qwen_parser_sha256': expected, 'final_parser_receipts': receipts,
        'audit_source_sha256': file_digest(ROOT / 'scripts/audit_independent_agent_loop_v3.py'),
        'derived_view': str(view), 'natural_JCT_acceptance': False}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
