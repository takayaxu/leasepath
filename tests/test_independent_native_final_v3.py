import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_independent_agent_loop_v3 import parse_native_final


def test_final_continuation_requires_actual_prompt_prefix():
    assert parse_native_final(': {"candidate":[1,2]}', 'feedback\n✿RETURN✿\n') == {'candidate': [1, 2]}
    assert parse_native_final('{"candidate":[1,2]}', 'ordinary prompt') == {'candidate': [1, 2]}
    with pytest.raises(ValueError, match='without actual'):
        parse_native_final(': {"candidate":[1,2]}', 'ordinary prompt')


@pytest.mark.parametrize('text', [': : {"candidate":[]}', ': thought {"candidate":[]}', ': {"candidate":[]} extra'])
def test_no_arbitrary_text_stripping_or_json_salvage(text):
    with pytest.raises(json.JSONDecodeError):
        parse_native_final(text, '✿RETURN✿')
