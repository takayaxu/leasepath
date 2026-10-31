from leasepath.d2_repair import SUITE
from leasepath.d2_repair_guidance import category, edit_rejection


def test_edit_containing_pytest_text_is_not_an_executed_test():
    command = "sed -i 's/python -m pytest/foo/' solution.py"
    assert category(command) == 'edit'
    assert edit_rejection(command)
    assert category(SUITE) == 'test' and edit_rejection(SUITE) is None
    assert edit_rejection("cat > solution.py <<'PY'\ndef f(): pass\nPY\n" + SUITE)
    rewrite = "cat <<'PY' > solution.py\ndef f(): pass\nPY"
    assert category(rewrite) == 'edit' and edit_rejection(rewrite) is None
