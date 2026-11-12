"""Tool identity plus causal state; new target-data predictor, frozen KV score.

Training inputs remain the nine-field causal contract. Unknown tools get their
own category and still use state features; no whole-prediction OOD fallback.
"""
from collections import Counter
import copy
import json
from pathlib import Path

import numpy as np

from .agent_risk_predictor_v2 import AgentGBDTV2,AgentEmpiricalV2,encode as state_encode,FEATURE_NAMES as STATE_NAMES
from .d2_hazards import derive_hazard_rows
from .reuse_predictor import EmpiricalRiskPredictor,LABELS

FEATURE_NAMES=['tool_category']+STATE_NAMES


class ContinuousEmpirical(AgentEmpiricalV2):
    def _key(self,features):
        return json.dumps([features['tool_name'],super()._key(features)],separators=(',',':'))

    @classmethod
    def load(cls,path):
        support=EmpiricalRiskPredictor.load(path)
        model=cls(**support.to_dict()['configuration'])
        model.__dict__.update(support.__dict__)
        return model


class ContinuousGBDT(AgentGBDTV2):
    def __init__(self):
        super().__init__();self.tools=[]

    def _encode(self,features,interval):
        tool=features['tool_name']
        return [self.tools.index(tool)+1 if tool in self.tools else 0]+state_encode(features,interval)

    def fit(self,samples,targets):
        import lightgbm as lgb
        self.support.fit(samples,targets)
        names={s['features']['tool_name'] for s in samples}
        self.tools=([None] if None in names else [])+sorted(v for v in names if v is not None)
        by_id={s['sample_id']:s for s in samples}
        rows=[r for r in derive_hazard_rows(samples,targets,self.support.right_edges) if r['loss_mask']]
        matrix=np.asarray([self._encode(by_id[r['sample_id']]['features'],r['interval_index']) for r in rows],dtype=float)
        # Deterministic training-only category masking makes unknown-tool behavior
        # learnable. All snapshots of an episode share the mask, not random rows.
        import hashlib
        for i,row in enumerate(rows):
            episode=by_id[row['sample_id']]['idle_episode_id']
            if int(hashlib.sha256(episode.encode()).hexdigest()[:8],16)%10==0:matrix[i,0]=0
        train=lgb.Dataset(matrix,label=np.asarray([LABELS.index(r['label']) for r in rows]),
            weight=np.asarray([r['sample_weight'] for r in rows]),feature_name=FEATURE_NAMES,
            categorical_feature=['tool_category'],free_raw_data=True)
        parameters={k:v for k,v in self.parameters.items() if k!='num_boost_round'}
        parameters.update(objective='multiclass',num_class=3,verbosity=-1,num_threads=1,deterministic=True,force_col_wise=True,
            seed=23,data_random_seed=23,feature_fraction_seed=23,bagging_seed=23,feature_fraction=1.,bagging_fraction=1.,bagging_freq=0)
        self.booster=lgb.train(parameters,train,num_boost_round=self.parameters['num_boost_round']);self.training_rows=len(rows)
        return self

    def predict(self,features,horizons=None):
        from .prediction_state import validate_features
        validate_features(features)
        if self.booster is None:raise ValueError('predictor not fitted')
        output=self.support.predict(features,horizons=horizons)
        if features['compatibility_id'] not in self.support.compatibilities:return output
        full=self.support.predict(features);supported=[r for r in full if r['supported']]
        if not supported:return output
        probabilities=self.booster.predict(np.asarray([self._encode(features,r['interval_index']) for r in supported]),num_threads=1)
        cumulative_u,cumulative_d,survival=0.,0.,1.
        for row,values in zip(supported,probabilities):
            if len(values)!=3 or not np.all(np.isfinite(values)) or np.any(values<0) or values.sum()<=0:raise ValueError('invalid hazards')
            hu,hd,h0=(values/values.sum()).tolist();cumulative_u+=survival*hu;cumulative_d+=survival*hd;survival*=h0
            row.update(hU=hu,hD=hd,h0=h0,F_U=cumulative_u,F_D=cumulative_d,survival=survival,
                estimate_source='continuous_tool_state_gbdt',unknown_tool=features['tool_name'] not in self.tools)
        chosen={r['interval_index'] for r in output}
        return [r for r in full if r['interval_index'] in chosen]

    def save(self,path):
        if self.booster is None:raise ValueError('predictor not fitted')
        Path(path).write_text(json.dumps(dict(version=1,kind='continuous_tool_state_gbdt',features=FEATURE_NAMES,
            parameters=self.parameters,tools=self.tools,training_rows=self.training_rows,support=self.support.to_dict(),
            booster=self.booster.model_to_string(),unknown_tool_mask='sha256(episode)%10==0'),indent=2,allow_nan=False)+'\n')

    @classmethod
    def load(cls,path):
        import lightgbm as lgb
        import tempfile
        data=json.loads(Path(path).read_text());model=cls()
        if data['version']!=1 or data['kind']!='continuous_tool_state_gbdt' or data['features']!=FEATURE_NAMES or data['parameters']!=model.parameters:raise ValueError('candidate schema differs')
        model.tools=data['tools']
        if len(set(model.tools))!=len(model.tools) or any(v is not None and not isinstance(v,str) for v in model.tools):raise ValueError('invalid tool categories')
        with tempfile.TemporaryDirectory() as tmp:
            support=Path(tmp)/'support.json';support.write_text(json.dumps(data['support']));model.support=EmpiricalRiskPredictor.load(support)
        model.booster=lgb.Booster(model_str=data['booster'])
        if model.booster.feature_name()!=FEATURE_NAMES or model.booster.num_model_per_iteration()!=3:raise ValueError('native schema differs')
        model.training_rows=data['training_rows'];return model


def training_data(observations,*,domain,cutoff_s):
    """Only already observed interval/event labels; no retroactive full outcomes.

    Caller supplies a terminal_s observation timestamp even for censoring.
    Samples observed at/after cutoff are excluded. Later outcomes are censored
    at cutoff, never relabeled as already known U/D.
    """
    selected=[]
    for row in observations:
        if row['snapshot_s']>=cutoff_s:continue
        if row['terminal_s']<=cutoff_s:
            event,remaining=row['event'],row['remaining_s']
        else:event,remaining='C',cutoff_s-row['snapshot_s']
        if remaining<=0:continue
        selected.append({**row,'training_event':event,'training_remaining_s':remaining})
    counts=Counter(r['episode_id'] for r in selected);episodes={}
    for r in selected:episodes.setdefault(r['owner'],set()).add(r['episode_id'])
    # Equal total project weight, then equal episodes and snapshots. Normalize
    # the average snapshot weight to one so fixed L2/Hessian parameters retain
    # their intended numerical scale; this does not increase support counts.
    scale=len(selected)/len(episodes) if episodes else 1.
    samples=[];targets=[]
    for i,row in enumerate(selected):
        sample_id=row.get('sample_id',row['episode_id']+':'+str(row['snapshot_s']))
        samples.append(dict(sample_id=sample_id,run_id=row['owner'],idle_episode_id=row['episode_id'],split='development',
            features={**row['features'],'compatibility_id':domain},
            sample_weight=scale/len(episodes[row['owner']])/counts[row['episode_id']]))
        targets.append(dict(sample_id=sample_id,outcome={'U':'legal_demand','D':'retire','C':'right_censored'}[row['training_event']],remaining_s=row['training_remaining_s']))
    return samples,targets
