"""Compare the actual frontend tokenizer with local rendering, without generation."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

from transformers import AutoTokenizer

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--config',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
manifest=json.loads((args.run/'service-manifest.json').read_text())
config=json.loads(args.config.read_text())
command=manifest['command'];model=command[command.index('--model')+1]
served=command[command.index('--served-model-name')+1]
tokenizer=AutoTokenizer.from_pretrained(model,local_files_only=True)
http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
messages=[{'role':'system','content':'Return exactly one JSON action.'},
          {'role':'user','content':'Template boundary check.'}]
rendered=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,
                                     **config.get('chat_template_kwargs',{}))
local=tokenizer.encode(rendered,add_special_tokens=False)
payload={'model':served,'messages':messages,'add_generation_prompt':True,
         **config.get('request_extra',{})}
request=urllib.request.Request(f"http://127.0.0.1:{manifest['port']}/tokenize",
    data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
with http.open(request,timeout=20) as response:actual=json.loads(response.read())
result={'status':'passed' if local==actual['tokens'] else 'failed',
        'inference_calls':0,'templated_token_count':len(local),'frontend_tokens_exact_match':local==actual['tokens'],
        'template_sha256':hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
        'rendered_prompt':rendered,'tokenizer_eos':tokenizer.eos_token_id,
        'explicit_stop_token_ids':config.get('request_extra',{}).get('stop_token_ids'),
        'chat_template_kwargs':config.get('chat_template_kwargs',{}),
        'config_sha256':hashlib.sha256(args.config.read_bytes()).hexdigest(),
        'scope':'input template boundary check, no capability score and no backend request'}
args.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
if result['status']!='passed':raise ValueError('template mismatch; no diagnostic generation allowed')
