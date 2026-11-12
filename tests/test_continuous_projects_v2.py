from leasepath.continuous_projects_v1 import projects as old_projects
from leasepath.continuous_projects_v2 import projects


def test_explicit_destination_and_collection_types_preserve_requirements():
    old=old_projects();new=projects()
    assert len(new)==6
    for before,after in zip(old,new):
        assert before['id']==after['id']
        assert before['tools']==after['tools']
        for a,b in zip(before['turns'],after['turns']):
            assert b['request'].startswith('Target deliverable: '+a['artifact']+'. ')
            assert a['request'] in b['request']
            assert (a['artifact'],a['key'],a['requirement'])==(b['artifact'],b['key'],b['requirement'])
    assert 'list of dictionaries' in new[0]['turns'][5]['request']
    assert 'order is a dictionary' in new[0]['turns'][6]['request']
