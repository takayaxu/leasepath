import pytest
from leasepath.prefill_cost_calibration import decreasing_fit, fit_table
from leasepath.distribution_cache import MarginalCostTable


def test_pooled_fit_preserves_sample_weight_and_rejects_independent_error():
    assert decreasing_fit([4.,1.,3.,0.]) == [4.,2.,2.,0.]
    rows=[{'prefix_tokens':256,'hit_tokens':h,'prefill_s':value+offset,'split':split}
          for h,value in [(0,3.),(128,2.),(256,1.)]
          for split,offset in [('fit',-.02),('fit',.02),('validation',.01)]]
    table=fit_table(rows,'fixture')
    assert table['validation']['status']=='passed'
    assert MarginalCostTable(table,backend_identity='fixture').marginal(256,128)==1.
    for row in rows:
        if row['split']=='validation':row['prefill_s']+=.6
    rejected=fit_table(rows,'fixture')
    assert rejected['validation']['status']=='rejected'
    with pytest.raises(ValueError,match='unvalidated'):
        MarginalCostTable(rejected,backend_identity='fixture')
    with pytest.raises(ValueError,match='incomplete'):
        fit_table(rows[:-1],'fixture')
