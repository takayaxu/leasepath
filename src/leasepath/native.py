"""Direct dense-F16 llama device KV references; only load on an authorized GPU."""
import ctypes as C
from pathlib import Path
import time


class Usage(C.Structure):
    _fields_=[(name,C.c_uint64) for name in ('capacity_cells','resident_cells','active_cells','shared_cells',
              'bytes_per_cell','payload_capacity_bytes','reserved_buffer_bytes','resident_payload_bytes',
              'active_payload_bytes','idle_payload_bytes')]


class Native:
    def __init__(self,root,context_cells=32768,threads=8):
        root=Path(root)
        self.lib=C.CDLL(str(root/'.artifacts/d6-native/liblp_native.so'))
        lib=self.lib;ptr=C.c_void_p;integer=C.c_int;tokens=C.POINTER(C.c_int32)
        lib.lp_open.argtypes=[C.c_char_p,integer,integer];lib.lp_open.restype=ptr
        lib.lp_error.restype=C.c_char_p
        lib.lp_close.argtypes=[ptr];lib.lp_clear.argtypes=[ptr];lib.lp_drop.argtypes=[ptr,integer]
        lib.lp_copy.argtypes=[ptr,integer,integer,integer,integer]
        lib.lp_usage.argtypes=[ptr,integer,C.POINTER(Usage)]
        lib.lp_span.argtypes=[ptr]
        lib.lp_tokenize.argtypes=[ptr,C.c_char_p,integer,tokens,integer]
        lib.lp_detokenize.argtypes=[ptr,tokens,integer,C.c_void_p,integer]
        lib.lp_run.argtypes=[ptr,tokens,integer,integer,integer,tokens,C.POINTER(C.c_double)]
        self.handle=lib.lp_open(str(root/'models/Qwen2.5-3B-Instruct/Qwen2.5-3B-Instruct-f16.gguf').encode(),context_cells,threads)
        if not self.handle:raise RuntimeError(lib.lp_error().decode())

    def close(self):
        if self.handle:self.lib.lp_close(self.handle);self.handle=None

    def clear(self):self.lib.lp_clear(self.handle)
    def drop(self,sequence):
        if not self.lib.lp_drop(self.handle,sequence):raise RuntimeError('native sequence removal failed')
    def copy(self,source,target,lo,hi):self.lib.lp_copy(self.handle,source,target,lo,hi)

    def tokenize(self,text):
        data=text.encode();capacity=max(128,len(data)+8);out=(C.c_int32*capacity)()
        n=self.lib.lp_tokenize(self.handle,data,len(data),out,capacity)
        if n<0:raise RuntimeError('token capacity exceeded')
        return list(out[:n])

    def text(self,tokens):
        values=(C.c_int32*len(tokens))(*tokens);out=C.create_string_buffer(len(tokens)*32+64)
        n=self.lib.lp_detokenize(self.handle,values,len(tokens),out,len(out))
        if n<0:raise RuntimeError('detokenization capacity exceeded')
        return out.raw[:n].decode(errors='replace')

    def usage(self,active=False):
        value=Usage()
        if not self.lib.lp_usage(self.handle,int(active),C.byref(value)):raise RuntimeError('device payload unavailable')
        result={name:getattr(value,name) for name,_ in value._fields_}
        result['attention_span']=self.lib.lp_span(self.handle)
        if result['bytes_per_cell']!=36864:raise RuntimeError('unexpected Qwen F16 geometry')
        return result

    def run(self,tokens,cached=0,output_tokens=32):
        inputs=(C.c_int32*len(tokens))(*tokens);out=(C.c_int32*output_tokens)();timing=(C.c_double*4)()
        started=time.perf_counter()
        code=self.lib.lp_run(self.handle,inputs,len(tokens),cached,output_tokens,out,timing)
        if code:raise RuntimeError(self.lib.lp_error().decode())
        expected_prompt=len(tokens)-cached
        # llama_perf counts one-token input in n_eval, not n_p_eval.
        executed=int(timing[2])+int(timing[3])-(output_tokens-1)
        if executed!=expected_prompt:raise RuntimeError(f'native executed {executed}, expected {expected_prompt}')
        return {'prompt_tokens':len(tokens),'cached_tokens':cached,'executed_prefill_tokens':executed,
                'output_tokens':list(out),'prompt_ms':timing[0],'generation_ms':timing[1],
                'service_ms':timing[0]+timing[1],'call_ms':1000*(time.perf_counter()-started),
                'native_n_p_eval':int(timing[2]),'native_n_eval':int(timing[3])}
