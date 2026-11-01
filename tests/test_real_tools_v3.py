import os
from pathlib import Path
import sys

import pytest

from leasepath.real_tools_v3 import RepairTools, ToolRejected, decode_action


def setup_tools(tmp_path):
    work = tmp_path/'work'; work.mkdir()
    binpath = tmp_path/'bin'; binpath.mkdir()
    (binpath/'python').symlink_to(sys.executable)
    return work, RepairTools(work, tmp_path/'outputs', {'PATH':str(binpath)+':/usr/bin:/bin'},
                             sorted(os.sched_getaffinity(0))[:1], len, page_tokens=15)


def test_ambiguous_and_multi_edit_rejection_writes_nothing(tmp_path):
    work, tools = setup_tools(tmp_path)
    (work/'a.py').write_text('x = 1\nx = 1\n')
    (work/'b.py').write_text('y = 2\n')
    with pytest.raises(ToolRejected, match='occurs 2'):
        tools.edit([{'path':'b.py','old':'2','new':'3'}, {'path':'a.py','old':'x = 1','new':'x = 2'}])
    assert (work/'b.py').read_text() == 'y = 2\n'


def test_syntax_failure_is_observed_without_secret_repair(tmp_path):
    work, tools = setup_tools(tmp_path)
    (work/'a.py').write_text('x = 1\n')
    result = tools.edit([{'path':'a.py','old':'x = 1','new':'x = ('}])
    assert result['automatic_syntax'][0]['returncode'] != 0
    assert 'SyntaxError' in result['automatic_syntax'][0]['output']
    assert (work/'a.py').read_text() == 'x = (\n'


def test_pagination_retains_middle_and_announces_omissions(tmp_path):
    work, tools = setup_tools(tmp_path)
    text = 'start-middle-critical-error-end'
    result = tools.store_output(text)
    next_page = tools.execute({'op':'output','result_id':result['result_id'],'offset':result['next_offset']})
    last_page = tools.execute({'op':'output','result_id':result['result_id'],'offset':next_page['next_offset']})
    assert result['text'] + next_page['text'] + last_page['text'] == text
    assert result['omitted_char_intervals'] == [[15,len(text)]]
    assert next_page['visible_char_interval'] == [15,30]


def test_paths_and_unregistered_local_edits_rejected(tmp_path):
    work, tools = setup_tools(tmp_path)
    (work/'escape').symlink_to(tmp_path)
    for path in ['../outside','/etc/passwd','escape/outside','.git/config']:
        with pytest.raises(ToolRejected): tools.path(path)
    tools.edit_paths = {'allowed.py'}
    with pytest.raises(ToolRejected, match='preregistered'):
        tools.edit([{'path':'other.py','old':'a','new':'b'}])


def test_invalid_json_is_not_repaired():
    with pytest.raises(ValueError): decode_action('{"op":"edit",}')
    assert decode_action('```json\n{"op":"submit"}\n```')['op']=='submit'
