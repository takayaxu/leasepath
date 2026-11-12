import pytest
from leasepath.continuous_protocol_v2 import delivery,native_delivery


def test_real_prose_before_json_is_a_valid_delivery():
    text='The checks have been successfully passed.\n{"done":true,"summary":"Updated report.json"}'
    assert delivery([{'role':'assistant','content':text}])==native_delivery(': '+text,'unfinished ✿RETURN✿')
    assert delivery([{'role':'assistant','content':'Updated the report and checked it.'}])['assistant_text'].startswith('Updated')


def test_empty_or_function_response_cannot_claim_delivery():
    with pytest.raises(ValueError):delivery([{'content':' '}])
    with pytest.raises(ValueError):delivery([{'content':'done','function_call':{'name':'read_file'}}])
    with pytest.raises(ValueError):native_delivery(': done','not a continuation')
