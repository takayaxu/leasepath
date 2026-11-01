"""Offline audit of v2 failures, delivered observations and actual chat tokens."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from transformers import AutoTokenizer

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--previous-run', type=Path, required=True)
parser.add_argument('--model', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
sha = lambda b: hashlib.sha256(b).hexdigest()
raw = [json.loads(line) for p in (args.previous_run/'raw').glob('*.jsonl') for line in p.read_text().splitlines()]
enqueues = {row['data']['request_id']: row['data']['prompt_token_ids'] for row in raw if row['kind']=='request_enqueue'}
tasks, token_checks = [], []
for task in sorted((args.previous_run/'debug-round2').glob('*/trajectory.json')):
    target = args.output/task.parent.name; target.mkdir()
    trajectory = json.loads(task.read_text())
    result = json.loads((task.parent/'result.json').read_text())
    behavior = json.loads((task.parent/'behavior-review.json').read_text())['automated_flags_not_correctness_or_manual_review']
    observations = [m for m in trajectory['messages'] if m.get('role')=='user' and 'raw_output' in m.get('extra',{})]
    actions = []
    for number, tool in enumerate(result['tools'], 1):
        observation = observations[number-1]
        raw_output = observation['extra']['raw_output']
        delivered = observation['content']
        omission = ([600, len(raw_output)-600] if len(raw_output)>1200 else None)
        syntax = any(word in raw_output for word in ['SyntaxError','IndentationError','TabError'])
        a = {'action':number, **tool, 'patch_changed':behavior[number-1]['patch_changed'],
             'raw_output_sha256':sha(raw_output.encode()), 'raw_chars':len(raw_output),
             'delivered_chars':len(delivered), 'omitted_raw_char_interval':omission,
             'syntax_failure_in_raw':syntax,
             'syntax_failure_marker_delivered':any(word in delivered for word in ['SyntaxError','IndentationError','TabError']),
             'delivered_observation':delivered}
        (target/f'action-{number:02d}-raw.txt').write_text(raw_output)
        actions.append(a)
    (target/'actions-and-delivered-observations.json').write_text(json.dumps(actions,indent=2)+'\n')
    for request_file in sorted(task.parent.glob('request-*.json')):
        request = json.loads(request_file.read_text())
        rendered = tokenizer.apply_chat_template(request['messages'], tokenize=False, add_generation_prompt=True)
        tokens = tokenizer.encode(rendered, add_special_tokens=False)
        ids = [v for k,v in enqueues.items() if k.startswith('chatcmpl-'+request['request_id']+'-')]
        roles = [m['role'] for m in request['messages']]
        # mini-swe appends format feedback without persisting the malformed
        # assistant action in messages. Consecutive user messages are legal
        # for this pinned Qwen template; report them, don't invent alternation.
        good_roles = roles[:2]==['system','user'] and roles[-1]=='user' and all(role in {'assistant','user'} for role in roles[2:])
        check = {'task_id':task.parent.name,'request_file':str(request_file),
                 'request_sha256':sha(request_file.read_bytes()),'request_id':request['request_id'],
                 'roles':roles,'role_sequence_valid':good_roles,
                 'consecutive_user_messages':sum(a==b=='user' for a,b in zip(roles,roles[1:])),
                 'templated_input_tokens':len(tokens),
                 'output_reservation':request['max_tokens'], 'backend_token_ids_exact_match':len(ids)==1 and ids[0]==tokens,
                 'rendered_sha256':sha(rendered.encode()),
                 'assistant_generation_suffix':rendered.endswith('<|im_start|>assistant\n'),
                 'tools_field_present':'tools' in request,'explicit_stop':request.get('stop')}
        token_checks.append(check)
    first_edit = next((a['action'] for a in actions if a['patch_changed']),None)
    first_syntax = next((a['action'] for a in actions if a['syntax_failure_in_raw']),None)
    tasks.append({'task_id':task.parent.name,'first_patch_change_action':first_edit,
                  'first_syntax_failure_action':first_syntax,
                  'first_syntax_failure_delivered':next((a['syntax_failure_marker_delivered'] for a in actions if a['syntax_failure_in_raw']),None),
                  'recovery_actions_after_first_syntax':[{'action':a['action'],'command':a['command'],
                      'returncode':a['returncode'],'patch_changed':a['patch_changed']} for a in actions if first_syntax and a['action']>first_syntax],
                  'truncated_observations':sum(a['omitted_raw_char_interval'] is not None for a in actions),
                  'finish_reasons':dict(Counter(x['finish_reason'] for x in result['requests']))})
(args.output/'request-template-checks.json').write_text(json.dumps(token_checks,indent=2)+'\n')
configs = {name:json.loads((args.model/name).read_text()) for name in ['config.json','tokenizer_config.json','generation_config.json']}
assert all(x['role_sequence_valid'] and x['backend_token_ids_exact_match'] and x['assistant_generation_suffix'] for x in token_checks)
summary = {'scope':'offline evidence, no new model calls','tasks':tasks,'requests_checked':len(token_checks),
           'all_backend_prompt_tokens_equal_pinned_tokenizer_template':True,
           'model_small_files_sha256':{p.name:sha(p.read_bytes()) for p in args.model.glob('*.json') if p.name in configs or p.name=='tokenizer.json'},
           'tokenizer_special_tokens':tokenizer.special_tokens_map,
           'template_sha256':sha(tokenizer.chat_template.encode()),
           'effective_generation':{'service_generation_config':'vllm','temperature':0,'top_p':1,'seed':23,'max_tokens':768,
               'eos_token_id_from_model_config':configs['config.json'].get('eos_token_id'),
               'tokenizer_eos_id':tokenizer.eos_token_id,'explicit_stop':None,
               'note':'repository generation_config sampling defaults not loaded because service uses --generation-config vllm'},
           'limitations':['Exact prompt token equality verifies historical template/tokenizer input, not model competence.',
               'Stored raw output is not assumed visible; omitted intervals and delivered content saved per action.',
               'First patch change is not always first erroneous edit; manual review required.']}
(args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'requests_checked':len(token_checks),'exact_prompt_match':True,
                  'tasks':[{k:v for k,v in x.items() if k!='recovery_actions_after_first_syntax'} for x in tasks]}))
