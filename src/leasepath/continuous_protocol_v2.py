"""A normal Agent delivery is assistant text, independently artifact-checked.

Do not require an extra JSON envelope: Qwen may add legitimate explanatory
prose. A no-tool response is the model's stop decision. Artifact correctness,
not prose claiming success, determines acceptance.
"""


def delivery(replies):
    if any(message.get('function_call') for message in replies):
        raise ValueError('tool invocation is not a completed delivery')
    content=''.join(message.get('content','') for message in replies).strip()
    if not content:raise ValueError('empty assistant delivery')
    return {'assistant_text':content}


def native_delivery(text,prompt):
    if text.startswith(':'):
        if not prompt.rstrip().endswith('✿RETURN✿'):
            raise ValueError('continuation colon without actual unfinished FN_EXIT')
        text=text[1:]
    content=text.strip()
    if not content:raise ValueError('empty native delivery')
    return {'assistant_text':content}
