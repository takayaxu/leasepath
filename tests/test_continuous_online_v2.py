import json
from types import SimpleNamespace
from leasepath.continuous_online_v2 import UpdatingPredictor


def test_initial_event_and_static_boundary_do_not_train(tmp_path):
    model=SimpleNamespace(predict=lambda f:[f])
    live=UpdatingPredictor(model,runtime_domain='runtime',training_domain='training',kind='gbdt',output=tmp_path/'training',seed_observations=[],dynamic=False)
    live.boundary()
    assert live.predict({'compatibility_id':'runtime'})==[{'compatibility_id':'training'}]
    live.close()
    rows=[json.loads(s) for s in (tmp_path/'training/training_timeline.jsonl').read_text().splitlines()]
    assert rows[0]['kind']=='initial_deployment' and rows[0]['predictor_kind']=='gbdt'
    assert rows[-1]['kind']=='service_end' and live.deployed==0


def test_dynamic_insufficient_projects_records_skip_without_training(tmp_path):
    live=UpdatingPredictor(object(),runtime_domain='r',training_domain='t',kind='empirical',output=tmp_path/'training',seed_observations=[],dynamic=True,interval_s=0)
    live.boundary()
    assert live.future is None and live.deployed==0
    live.close()
    rows=[json.loads(s) for s in (tmp_path/'training/training_timeline.jsonl').read_text().splitlines()]
    assert [r['kind'] for r in rows]==['initial_deployment','update_skipped','service_end']
