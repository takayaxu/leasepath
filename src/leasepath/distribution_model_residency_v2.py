"""Bounded startup for verified disk-backed model pages on a shared host.

A cold model mmap can need longer than the old tmpfs keeper's 60-second reply
window. Only startup gets a larger bound; subsequent residency checks stay 60s.
"""
import json
import select
from pathlib import Path

from .prediction_training import sha256
from .resident_runtime import ModelResidentKeeper


class DistributionModelKeeper(ModelResidentKeeper):
    def __init__(self,path,*,startup_timeout_s=1800):
        if type(startup_timeout_s) is not int or not 60<=startup_timeout_s<=1800:
            raise ValueError('startup timeout out of bounded range')
        self.reply_timeout_s=startup_timeout_s
        super().__init__(path)
        self.reply_timeout_s=60

    def _read(self):
        ready,_,_=select.select([self.proc.stdout],[],[],self.reply_timeout_s)
        if not ready:raise TimeoutError('model residency keeper exceeded bounded startup/check timeout')
        line=self.proc.stdout.readline()
        if not line:raise RuntimeError('model residency keeper exited')
        result=json.loads(line)
        if result['status']!='ready' or result['residency']['nonresident_pages']:
            raise RuntimeError('model residency keeper failed')
        return result


def reuse_verified_model(plan,model):
    """Reuse a completed full hash only with the preserved source and file stat.

    No GPU generation occurred in the prior failed keeper startup. Its runner
    records model_identity only after matching the complete expected SHA256.
    A changed inode/size/mtime/ctime or a different verifier invalidates reuse.
    """
    origin=Path(plan['verified_model_origin'])
    if sha256(origin)!=plan['verified_model_origin_sha256']:
        raise ValueError('prior model-verification manifest changed')
    manifest=json.loads(origin.read_text())
    prior_plan=origin.parent/'frozen-plan.json'
    prior=json.loads(prior_plan.read_text())
    if prior['model_sha256']!=plan['model_sha256'] or manifest['status']!='failed':
        raise ValueError('prior verification scope differs')
    if manifest.get('error')!='TimeoutError: model residency keeper did not respond':
        raise ValueError('prior startup did not reach verified model stage')
    if any(b['generations'] for b in manifest['budgets'].values()):
        raise ValueError('prior experiment has generated requests')
    verifier=Path(plan['verified_model_runner'])
    if sha256(verifier)!=manifest['source_sha256'][str(verifier.resolve())]:
        raise ValueError('prior verification source changed')
    original_plan=Path(plan['prior_plan_path']).resolve()
    if sha256(original_plan)!=manifest['source_sha256'][str(original_plan)] or json.loads(original_plan.read_text())!=prior:
        raise ValueError('prior frozen plan changed')
    current=model.stat()
    if any(getattr(current,k)!=value for k,value in manifest['model_identity'].items()):
        raise ValueError('verified model file identity changed; full hash required')
    return {'full_sha256':plan['model_sha256'],'origin':str(origin),'origin_sha256':sha256(origin),
            'model_identity':manifest['model_identity'],'reuse_condition':'same device/inode/size/mtime/ctime after prior complete SHA256'}
