from leasepath.continuous_online_v1 import EpisodeTracker


def features(**changes):
    f=dict(elapsed_idle_s=1.,compatibility_id='backend',prefix_tokens=128,tool_name='read_file',tool_elapsed_s=None,phase='queued',retry_count=0,is_queued=True,queue_length=2)
    f.update(changes);return f


def request(owner,rid,at,observed=None,tokens=None):
    return dict(owner=owner,request_id=rid,lookup_ns=int(at*1e9),release_ns=int((at+.2)*1e9),
                tokens=list(range(129)) if tokens is None else tokens,
                policy_decision={'distribution_audit':{'t0_s':at-.01,'observed_idle_features':observed or {}}})


def test_miss_is_demand_and_future_outcomes_not_known():
    tracker=EpisodeTracker();tracker.request(request('a','r1',1.))
    tracker.request(request('b','r2',2.,{'a':features()}))
    current=tracker.as_of(3.)
    assert len(current)==1 and current[0]['event']=='C' and current[0]['terminal_s']==3.
    row=request('a','r3',4.,{'b':features()});row['hit']=0
    tracker.request(row)
    assert tracker.observations[0]['event']=='U'
    assert tracker.episode_events['a:r1']=='U'
    tracker.retire('b',5.)
    assert tracker.observations[-1]['event']=='D'


def test_prefix_change_excluded_rather_than_inventing_retirement():
    tracker=EpisodeTracker();tracker.request(request('a','r1',1.))
    tracker.request(request('b','r2',2.,{'a':features()}))
    tracker.request(request('a','r3',4.,tokens=[999]+list(range(1,129))))
    assert not tracker.observations
    assert len(tracker.excluded)==1
    assert tracker.excluded[0]['reason']=='next_lookup_changes_observed_prefix'
