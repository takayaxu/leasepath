"""Native request hook runs before the Agent decorates a request ID."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))


def test_tracking_uses_authoritative_native_id_before_agent_decoration(monkeypatch):
    from run_continuous_e2e_v2 import OnlineEngine,PressureEngine
    observations=[];boundaries=[]
    live=SimpleNamespace(deployed=2,kind='gbdt',boundary=lambda:boundaries.append(True),
                         request_completed=lambda row:observations.append(row))
    engine=object.__new__(OnlineEngine);engine.baseline=SimpleNamespace(live=live);engine.observing=True
    native={'owner':'project','lookup_ns':123456,'tokens':[1,2]}
    monkeypatch.setattr(PressureEngine,'generate',lambda self,*args,**kwargs:dict(native))
    result=engine.generate('actual prompt')
    assert 'request_id' not in result
    assert result['tracker_request_id']=='native-123456'
    assert observations[0]['request_id']=='native-123456'
    assert observations[0]['lookup_ns']==123456 and observations[0]['owner']=='project'
    assert result['predictor_version']==2 and boundaries==[True]
