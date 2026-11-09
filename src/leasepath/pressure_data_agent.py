"""Qwen-Agent model adapter; no compression, fallback model or implicit retries."""
import hashlib
import json
import time

from qwen_agent.llm.function_calling import BaseFnCallModel
from qwen_agent.llm.schema import Message
from .qwen_data_backend import verify_request

class PressureObservedQwen(BaseFnCallModel):
    def __init__(self,engine,tokenizer,budget,controller,backend,*,lane,context=16384):
        super().__init__({'model':'Qwen2.5-Coder-32B-Instruct',
                          'generate_cfg':{'max_retries':0,'max_input_tokens':0,'use_raw_api':False,
                                          'parallel_function_calls':False,'fncall_prompt_type':'qwen'}})
        self.engine,self.tokenizer,self.budget=engine,tokenizer,budget
        self.controller,self.backend=controller,backend
        self.context=context
        self.lane=lane
        self.owner=None
        self.history=[]
        self.generation_attempts=0
        self.task_deadline=None
        self.pending_tool_results=[]
        self.check_sources=lambda:None

    def _continue_assistant_response(self,messages,generate_cfg,stream):
        # Actual template continuation, never simulate it by moving assistant text into user input.
        return self._chat(messages,stream=stream,delta_stream=False,generate_cfg=generate_cfg)

    def _chat_stream(self,*args,**kwargs):
        raise ValueError('sequential non-streaming collection only')

    def _chat_no_stream(self,messages,generate_cfg):
        check_begin=time.monotonic_ns()
        self.check_sources()
        check_ns=time.monotonic_ns()-check_begin
        begin=time.monotonic_ns()
        rendered=[]
        for m in messages:
            if not isinstance(m.content,str):
                raise ValueError('unsupported nontext model input')
            rendered.append({'role':m.role,'content':m.content})
        continuation=rendered[-1]['role']=='assistant'
        prompt=self.tokenizer.apply_chat_template(rendered,tokenize=False,
                  add_generation_prompt=not continuation,continue_final_message=continuation)
        tokens=self.tokenizer.encode(prompt,add_special_tokens=False)
        if len(tokens)+1024>self.context:
            raise ValueError('context limit, history was not compressed')
        for value in self.pending_tool_results:
            if value not in prompt:
                raise ValueError('tool result missing from next backend input')
        self.pending_tool_results=[]
        blocks=(len(tokens)-1)//self.engine.block_size
        if not 1 <= blocks <= self.engine.capacity:
            raise ValueError(f'actual prompt requires {blocks} blocks; capacity {self.engine.capacity}; no generation reserved')
        reservation=self.budget.reserve(self.lane,owner=self.owner)
        self.generation_attempts+=1
        prompt_sha=hashlib.sha256(prompt.encode()).hexdigest()
        request_id='r'+reservation['id']
        self.controller.emit('request_input',owner=self.owner,request_id=request_id,
                             context_version='v1',
                             attempt_id=request_id,logical_request_id=request_id,retry_of=None,
                             prompt=prompt,prompt_sha256=prompt_sha,token_ids=tokens,
                             input_source_check_ns=check_ns,preprocess_ns=time.monotonic_ns()-begin)
        remaining=(self.task_deadline-time.monotonic()) if self.task_deadline else 180
        result=self.engine.generate(prompt,limit=1024,timeout=min(remaining,180),
                                    stops=generate_cfg.get('stop',[]))
        verification_start=time.monotonic_ns()
        verify_request(result,tokens)
        result['verification_ns']=time.monotonic_ns()-verification_start
        result={**result,'owner':self.owner,'context_version':'v1','request_id':request_id,'attempt_id':request_id,
                'logical_request_id':request_id,'retry_of':None,'prompt_sha256':prompt_sha}
        self.backend.emit('engine_request',**result)
        self.history.append(result)
        if result['reason'] not in ('eog','protocol_stop'):
            raise RuntimeError('model attempt ended: '+result['reason'])
        self.check_sources()
        return [Message(role='assistant',content=result['text'])]
