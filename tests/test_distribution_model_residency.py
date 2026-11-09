import hashlib
import json
from pathlib import Path

import pytest
from leasepath.distribution_model_residency_v2 import DistributionModelKeeper,reuse_verified_model


def test_prior_hash_reuse_rejects_changed_identity_and_manifest(tmp_path):
    model=tmp_path/'model';model.write_bytes(b'fixed model')
    verifier=tmp_path/'runner.py';verifier.write_text('preserved verifier')
    prior_plan=tmp_path/'original-plan.json';prior_plan.write_text(json.dumps({'model_sha256':'expected'}))
    (tmp_path/'frozen-plan.json').write_text(json.dumps({'model_sha256':'expected'},indent=2)+'\n')
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    origin=tmp_path/'manifest.json'
    manifest={'status':'failed','error':'TimeoutError: model residency keeper did not respond',
        'budgets':{'native':{'generations':0}},'source_sha256':{str(verifier):digest(verifier),str(prior_plan):digest(prior_plan)},
        'model_identity':{k:getattr(model.stat(),k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}}
    origin.write_text(json.dumps(manifest))
    plan={'verified_model_origin':str(origin),'verified_model_origin_sha256':digest(origin),
        'model_sha256':'expected','verified_model_runner':str(verifier),'prior_plan_path':str(prior_plan)}
    assert reuse_verified_model(plan,model)['full_sha256']=='expected'
    model.write_bytes(b'changed file')
    with pytest.raises(ValueError,match='identity changed'):reuse_verified_model(plan,model)
    origin.write_text('{}')
    with pytest.raises(ValueError,match='manifest changed'):reuse_verified_model(plan,model)
    with pytest.raises(ValueError,match='bounded range'):DistributionModelKeeper(model,startup_timeout_s=1801)
