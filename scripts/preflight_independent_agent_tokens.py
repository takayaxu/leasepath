"""Exact initial batch messages through Qwen-Agent template, stopping before reserve."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.pressure_data_agent import PressureObservedQwen
from leasepath.independent_agent_protocol import SYSTEM,schemas


def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    from transformers import AutoTokenizer
    raw=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    class Capture:
        def __getattr__(self,k):return getattr(raw,k)
        def encode(self,prompt,**kwargs):self.tokens=raw.encode(prompt,**kwargs);return self.tokens
    class StopBeforeReserve(RuntimeError):pass
    class NoGenerationBudget:
        calls=0
        def reserve(self,*args,**kwargs):self.calls+=1;raise StopBeforeReserve()
    rows=[]
    for task in json.loads((a.suite/'tasks.json').read_text()):
        tokenizer=Capture();budget=NoGenerationBudget();engine=SimpleNamespace(capacity=32,block_size=128)
        llm=PressureObservedQwen(engine,tokenizer,budget,None,None,lane='pilot');llm.owner=task['id']
        messages=[{'role':'system','content':SYSTEM},{'role':'user','content':task['intent']+'\nUse inspect_problem to read the actual instance data. You decide subsequent calls and stopping.'}]
        try:llm.chat(messages,functions=schemas(task),stream=False)
        except StopBeforeReserve:pass
        else:raise AssertionError('must stop before reservation')
        assert budget.calls==1
        rows.append({'task':task['id'],'actual_initial_prompt_tokens':len(tokenizer.tokens),'cached_blocks':(len(tokenizer.tokens)-1)//128})
        engine.capacity=1;budget.calls=0
        try:llm.chat(messages,functions=schemas(task),stream=False)
        except ValueError as exc:assert 'actual prompt requires' in str(exc)
        else:raise AssertionError('capacity should reject before reserve')
        assert budget.calls==0
    a.output.write_text(json.dumps({'status':'passed_initial_prompt_only','model_generations':0,'reservations':0,'rows':rows,
        'template':'exact qwen_cache_independent_agent_batch initial messages','capacity_guard_rejections_before_reservation':12,
        'full_actual_agent_history_feasibility':'not established; runtime guard required'},indent=2)+'\n')
    print('Exact batch initial prompt token counts:',[r['actual_initial_prompt_tokens'] for r in rows])

if __name__=='__main__':main()
