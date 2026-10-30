"""CPU mirror of b7017 unified dense KV placement (128-token microbatches).

This models storage placement, not KV values. Validate padded attention span
and resident/active unions against the native backend for every measured call.
"""
import math


class Layout:
    def __init__(self,cells=32768,block_size=128):
        self.capacity=cells;self.block_size=block_size;self.head=0;self.owners={};self.used=set();self.highest=-1

    def free(self,cells):
        cells=set(cells);self.used-=cells
        if self.highest in cells:self.highest=max(self.used,default=-1)
        if cells:self.head=min(self.head,min(cells))

    def span(self):return min(self.capacity,max(256,math.ceil((self.highest+1)/256)*256))

    def allocate(self,count):
        if self.head>len(self.used)+2*count:self.head=0
        allocated=[];tested=0
        while len(allocated)<count:
            if self.head==self.capacity:self.head=0
            index=self.head;self.head+=1;tested+=1
            if index not in self.used:
                allocated.append(index);self.used.add(index);self.highest=max(self.highest,index)
            if tested>self.capacity:raise ValueError('virtual KV pool exhausted')
        return allocated

    def step(self,wanted,hits,prompt_tokens):
        prefix=[]
        for index,key in enumerate(wanted):
            if index<hits:prefix.extend(self.owners[key])
            elif key in self.owners:self.free(self.owners.pop(key))
        active=prefix.copy();miss=prompt_tokens-len(prefix);weighted_span=0;steps=0
        for count in [min(128,miss-lo) for lo in range(0,miss,128)]:
            active.extend(self.allocate(count));weighted_span+=count*self.span();steps+=1
        generation_spans=[]
        for _ in range(31):active.extend(self.allocate(1));generation_spans.append(self.span())
        peak_span=self.span();peak_resident=len(self.used)
        for index,key in enumerate(wanted):self.owners[key]=active[index*self.block_size:(index+1)*self.block_size]
        keep={cell for cells in self.owners.values() for cell in cells}
        self.free(set(active)-keep)
        return {'missed_tokens':miss,'mean_prompt_span':weighted_span/miss,'mean_generation_span':sum(generation_spans)/31,
                'prompt_microbatches':steps,'peak_span':peak_span,'idle_span':self.span(),
                'peak_resident':peak_resident,'idle_resident':len(self.used)}

    def drop(self,keys):
        for key in keys:
            if key in self.owners:self.free(self.owners.pop(key))
