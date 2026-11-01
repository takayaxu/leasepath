from leasepath.real_workflow import ProgressGuard


def test_cycling_commands_with_same_failure_are_bounded():
    guard=ProgressGuard()
    outputs=[guard.observe('grep '+str(i),2,'same missing directory',b'unchanged',b'unchanged') for i in range(6)]
    assert not outputs[1]['notes']
    assert outputs[2]['notes'] and outputs[-1]['stop']


def test_real_patch_progress_clears_failure_history_and_noop_edits_are_visible():
    guard=ProgressGuard()
    for _ in range(5):guard.observe('wrong',1,'failure',b'a',b'a')
    assert not guard.observe('edit',0,'',b'a',b'b')['stop']
    assert guard.observe('wrong',1,'failure',b'b',b'b')['equivalent_failure_count']==1
    assert 'EDIT CHECK' in guard.observe("sed -i 's/x/y/' src/a.py",0,'',b'b',b'b')['notes'][0]
