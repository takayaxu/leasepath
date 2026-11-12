import pytest
from leasepath.continuous_predictor_v1 import ContinuousGBDT,ContinuousEmpirical,training_data
from leasepath.prediction_training import check_distribution


def features(**changes):
    result=dict(elapsed_idle_s=1.,compatibility_id='dev',prefix_tokens=512,tool_name='read_file',
                tool_elapsed_s=None,phase='queued',retry_count=0,is_queued=True,queue_length=1)
    result.update(changes);return result


def test_future_outcomes_are_censored_and_project_weights_balanced():
    rows=[dict(episode_id='e'+str(i),owner='p'+str(i%2),snapshot_s=float(i+1),terminal_s=50.,remaining_s=49.-i,event='U',features=features()) for i in range(6)]
    samples,targets=training_data(rows,domain='training',cutoff_s=5.)
    assert len(samples)==4
    assert all(t['outcome']=='right_censored' for t in targets)
    assert [t['remaining_s'] for t in targets]==[4.,3.,2.,1.]
    assert sum(s['sample_weight'] for s in samples)==4
    assert all(s['features']['compatibility_id']=='training' for s in samples)


def test_different_tools_remain_in_empirical_groups():
    model=ContinuousEmpirical()
    assert model._key(features(tool_name='read_file'))!=model._key(features(tool_name='run_experiment'))


def test_unknown_tool_uses_state_and_model_roundtrip(tmp_path):
    samples=[];targets=[]
    for i in range(180):
        queued=i%2==0
        f=features(tool_name='read_file' if queued else 'run_experiment',queue_length=1 if queued else 6,
                   is_queued=queued,phase='queued' if queued else 'waiting_tool',tool_elapsed_s=None if queued else 4.)
        samples.append(dict(sample_id=str(i),run_id='project'+str(i%6),idle_episode_id='e'+str(i),split='development',features=f,sample_weight=1.))
        targets.append(dict(sample_id=str(i),outcome='legal_demand',remaining_s=.1 if queued else 40.))
    model=ContinuousGBDT().fit(samples,targets)
    path=tmp_path/'candidate.json';model.save(path);restored=ContinuousGBDT.load(path)
    f=features(tool_name='brand_new_tool');prediction=model.predict(f)
    check_distribution(prediction)
    assert prediction==restored.predict(f)
    assert all(row['estimate_source']=='continuous_tool_state_gbdt' for row in prediction if row['supported'])
    assert all(row['unknown_tool'] for row in prediction if row['supported'])
    early=model.predict(features(tool_name='brand_new_tool',queue_length=1,is_queued=True,phase='queued'))
    late=model.predict(features(tool_name='brand_new_tool',queue_length=6,is_queued=False,phase='waiting_tool',tool_elapsed_s=4.))
    assert next(row['F_U'] for row in early if row['right_s']==1)>next(row['F_U'] for row in late if row['right_s']==1)
    assert not model.predict(features(compatibility_id='other'))[0]['supported']
    empirical=ContinuousEmpirical().fit(samples,targets);path=tmp_path/'empirical.json';empirical.save(path)
    assert ContinuousEmpirical.load(path).predict(features())==empirical.predict(features())
