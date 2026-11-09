"""Frozen exact-grid monotone prefill fit and independent held-out validation."""
import statistics


def decreasing_fit(values):
    pools=[]
    for value in values:
        pools.append([float(value),1])
        while len(pools)>1 and pools[-2][0]/pools[-2][1] < pools[-1][0]/pools[-1][1]:
            right=pools.pop();left=pools.pop();pools.append([left[0]+right[0],left[1]+right[1]])
    return [total/count for total,count in pools for _ in range(count)]


def fit_table(rows, backend_identity, block_size=128):
    curves=[];errors=[];validation=[];marginals=[]
    for prefix in sorted({r['prefix_tokens'] for r in rows}):
        points=[];raw=[]
        for hit in range(0,prefix+1,block_size):
            fit=[r['prefill_s'] for r in rows if r['prefix_tokens']==prefix and r['hit_tokens']==hit and r['split']=='fit']
            val=[r['prefill_s'] for r in rows if r['prefix_tokens']==prefix and r['hit_tokens']==hit and r['split']=='validation']
            if len(fit)!=2 or len(val)!=1:raise ValueError('incomplete/duplicate calibration grid')
            raw.append(statistics.mean(fit));validation.extend(val)
        fitted=decreasing_fit(raw)
        for index,(hit,value) in enumerate(zip(range(0,prefix+1,block_size),fitted)):
            val=next(r['prefill_s'] for r in rows if r['prefix_tokens']==prefix and r['hit_tokens']==hit and r['split']=='validation')
            points.append({'hit_tokens':hit,'prefill_s':value,'fit_raw_mean_s':raw[index],'validation_prefill_s':val})
            errors.append(abs(value-val))
        marginals.extend(a-b for a,b in zip(fitted,fitted[1:]) if a>b)
        curves.append({'observed_prefix_tokens':prefix,'actual_calibration_prompt_tokens':prefix+1,'points':points})
    mae=statistics.mean(errors);positive=statistics.median(marginals) if marginals else 0.
    relative=mae/statistics.mean(validation)
    passed=relative<=.1 and positive>0 and mae<=.5*positive
    return {'version':1,'backend_identity':backend_identity,'block_tokens':block_size,
        'proxy':'T(P+1,h) for block-aligned already observed P; no future suffix',
        'validation':{'status':'passed' if passed else 'rejected','heldout_grid_points':len(errors),
            'MAE_s':mae,'MAE_over_mean_prefill':relative,'median_positive_marginal_s':positive,
            'MAE_over_median_marginal':mae/positive if positive else None,
            'thresholds':{'MAE_over_mean_prefill_max':.1,'MAE_over_median_marginal_max':.5}},'curves':curves}
