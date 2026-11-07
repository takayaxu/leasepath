import pytest

np=pytest.importorskip('numpy')
from leasepath.qwen_data_tasks import equal

def test_checker_rejects_materially_wrong_content():
    assert equal({'rows':[['A',20.0],['B',30.0]]},{'rows':[['A',20.0],['B',30.0]]})
    assert not equal({'rows':[['A',20.0]]},{'rows':[['A',20.0],['B',30.0]]})
    assert not equal({'value':100000001},{'value':100000000})
    assert not equal({'value':float('nan')},{'value':0.0})
    assert not equal({'value':True},{'value':1})
    assert not equal({'value':1.0,'leak':'x'},{'value':1.0})
    assert not equal({'value':0.95,'fold_scores':[0.8,0.9]},
                     {'value':0.95,'fold_scores':[0.9,1.0]})
    assert equal({'value':1.0+1e-12},{'value':1.0})
