"""Extension contracts must preserve parameter boundaries and the frozen budget across restarts."""
import json
import pytest
from jsonschema import Draft202012Validator,ValidationError
from leasepath.restricted_data_tools import schemas_for
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.restricted_data_tools import SessionTools


def test_shared_parameter_bounds_are_selected_by_operation():
    schema=next(x['parameters'] for x in schemas_for(['nmf','ica']) if x['name']=='compute')
    v=Draft202012Validator(schema)
    v.validate({'dataset_id':'dtest','operation':'ica','components':5,'iterations':400})
    with pytest.raises(ValidationError):v.validate({'dataset_id':'dtest','operation':'nmf','components':5,'iterations':400})
    for args in [
        {'dataset_id':'dtest','operation':'nmf','components':5,'iterations':200,'code':'print(1)'},
        {'dataset_id':'dtest','operation':'ica','components':True,'iterations':300},
        {'dataset_id':'dtest','operation':'ica','components':10**8,'iterations':300},
    ]:
        with pytest.raises(ValidationError):v.validate(args)


def test_small_stage_budget_is_durable_and_cannot_change_on_resume(tmp_path):
    path=tmp_path/'ledger.jsonl';limits={'probe':2,'pilot':3,'total':4}
    b=GenerationBudget(path,limits=limits)
    b.reserve('probe');b.reserve('probe')
    with pytest.raises(RuntimeError):b.reserve('probe')
    resumed=GenerationBudget(path,limits=limits);resumed.reserve('pilot');resumed.reserve('pilot')
    with pytest.raises(RuntimeError):resumed.reserve('pilot')
    with pytest.raises(ValueError):GenerationBudget(path,limits={'probe':3,'pilot':3,'total':6}).reserve('pilot')
    assert len(path.read_text().splitlines())==4


def test_complete_nested_array_result_is_one_object_not_an_empty_page(tmp_path):
    tools=SessionTools(tmp_path/'session',{},['quantile'])
    result={'mean_kth_distance':3.2,'first_indices':[1,2,3,4,5]}
    tools.artifacts['atest']=result
    viewed=tools.call('view_artifact',{'artifact_id':'atest','offset':0,'limit':10})
    assert viewed['result']==result and viewed['page']==[result]
    assert viewed['row_count']==1 and viewed['complete_result_returned']
    assert viewed['pagination_unit']=='result_object'
    tools.artifacts['atable']={'rows':[[1,2],[3,4]]}
    viewed=tools.call('view_artifact',{'artifact_id':'atable','offset':1,'limit':1})
    assert viewed['page']==[[3,4]] and viewed['row_count']==2


def test_sealed_budget_refuses_further_reservations(tmp_path):
    path=tmp_path/'ledger.jsonl';b=GenerationBudget(path);b.reserve('probe')
    path.with_suffix('.sealed.json').write_text('{}\n')
    with pytest.raises(RuntimeError):b.reserve('probe')
    assert len(path.read_text().splitlines())==1
