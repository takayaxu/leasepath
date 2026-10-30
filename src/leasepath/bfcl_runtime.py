"""Pinned local BFCL simulators and literal-only reference-call interpreter.

These are scripted benchmark trajectories, not autonomous agent accuracy tests.
Names like send_message operate on in-memory BFCL state, never real connectors.
"""
import ast
from copy import deepcopy
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import sys
import time
import types

MODULES={'GorillaFileSystem':'gorilla_file_system','MathAPI':'math_api','MessageAPI':'message_api',
         'TwitterAPI':'posting_api','TicketAPI':'ticket_api','TradingBot':'trading_bot',
         'TravelAPI':'travel_booking','VehicleControlAPI':'vehicle_control'}


def install_namespace(directory):
    prefix='bfcl_eval.eval_checker.multi_turn_eval.func_source_code'
    parts=prefix.split('.')
    for end in range(1,len(parts)+1):
        name='.'.join(parts[:end])
        if name not in sys.modules:
            module=types.ModuleType(name);module.__path__=[str(directory)];sys.modules[name]=module
    return prefix


class Scenario:
    def __init__(self,directory,task):
        prefix=install_namespace(directory);self.instances={};self.methods={}
        for name in task['involved_classes']:
            cls=getattr(importlib.import_module(f'{prefix}.{MODULES[name]}'),name)
            instance=cls()
            if hasattr(instance,'_load_scenario'):instance._load_scenario(deepcopy(task['initial_config'].get(name,{})),long_context=False)
            self.instances[name]=instance
            for method,value in inspect.getmembers(instance,predicate=inspect.ismethod):
                if method.startswith('_'):continue
                if method in self.methods:raise ValueError('ambiguous reference method')
                self.methods[method]=value

    def call(self,expression):
        node=ast.parse(expression,mode='eval').body
        def evaluate(value):
            if isinstance(value,ast.Call):
                if not isinstance(value.func,ast.Name) or value.func.id not in self.methods:raise ValueError('call outside local simulator allowlist')
                if any(k.arg is None for k in value.keywords):raise ValueError('expanded call arguments forbidden')
                args=[evaluate(x) for x in value.args];kwargs={k.arg:evaluate(k.value) for k in value.keywords}
                return self.methods[value.func.id](*args,**kwargs)
            return ast.literal_eval(value)
        started=time.perf_counter_ns()
        try:
            result=evaluate(node);exception=None
        except Exception as exc:
            result={'execution_exception':type(exc).__name__,'message':str(exc)};exception=type(exc).__name__
        ended=time.perf_counter_ns()
        # Preserve outputs verbatim, with deterministic JSON for JSON-native data.
        serialized=json.dumps(result,ensure_ascii=False,sort_keys=True,default=str)
        return {'call':expression,'output':serialized,'execution_ns':ended-started,'exception':exception,
                'output_sha256':hashlib.sha256(serialized.encode()).hexdigest()}

    def descriptions(self):
        return '\n'.join(f'{name}{inspect.signature(method)}: {(inspect.getdoc(method) or "").splitlines()[0]}'
                         for name,method in sorted(self.methods.items()))


def render(messages):
    # Explicit Qwen chat delimiters, preserved in full. No truncation or padding.
    return ''.join(f'<|im_start|>{role}\n{text}<|im_end|>\n' for role,text in messages)+'<|im_start|>assistant\n'
