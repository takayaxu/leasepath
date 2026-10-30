"""Development-only native timing surface with exact placement features."""
from copy import deepcopy
import math

import numpy as np

from .kv_layout import Layout
from .source_replay import keys_for


def features(layout):
    miss=layout['missed_tokens']/1024
    return [1,miss,miss*layout['mean_prompt_span']/1024,
            layout['mean_generation_span']/1024,layout['prompt_microbatches']]


def fit(rows):
    if not rows:raise ValueError('no development request measurements')
    x=np.array([features(r['device']['layout']) for r in rows]);y=np.array([r['device']['service_ms'] for r in rows])
    if np.linalg.matrix_rank(x)!=5:raise ValueError('development timing features lack rank')
    coefficients=np.linalg.lstsq(x,y,rcond=None)[0]
    control=float(np.median([r['control_ms'] for r in rows]))
    return {'method':'development_physical_span_linear_v1','coefficients':coefficients.tolist(),
            'control_ms':max(0,control),'training_requests':len(rows),'fixed_output_tokens':32,
            'features':['constant','missed/1024','missed*mean_prompt_physical_span/1024^2',
                        'mean_generation_physical_span/1024','prompt_microbatches'],
            'training_service_median_ape_percent':float(np.median(abs(x@coefficients-y)/y)*100),
            'training_control_median_ms':control,'test_measurements_used_for_fit':False}


class Model:
    def __init__(self,model,factors=None):
        self.model=deepcopy(model);self.layout=Layout();self.factors=factors or [1,1,1];self.records=[]

    def controller_cost(self,request,cache,previous,hits):
        self.layout.drop(set(previous)-set(cache.blocks))
        wanted=keys_for(request['tokens'],128);layout=self.layout.step(wanted,hits,len(request['tokens']))
        service=float(np.dot(features(layout),self.model['coefficients']))
        if not math.isfinite(service) or service<=0:raise ValueError('invalid fitted native timing')
        fraction=hits/max(1,len(wanted));state=0 if hits==0 else 2 if fraction>=.75 else 1
        service*=self.factors[state]
        self.records.append({'request_id':request['id'],'layout':layout,'native_service_ms':service,'reuse_state':state})
        return service+self.model['control_ms']
