"""Command separation for the explicitly guided repair collection profile."""
import re

from .d2_repair import SUITE


def category(command):
    if command.strip() == SUITE:
        return 'test'
    if re.search(r'>\s*solution\.py|write_text|write_bytes|\bsed\s+-i\b', command):
        return 'edit'
    if re.match(r'\s*(cat|ls|find|grep|rg|head|tail|sed)\b', command):
        return 'inspect'
    return 'shell'


def edit_rejection(command):
    if 'pytest' in command and command.strip() != SUITE:
        return 'Run testing as a separate action containing only: ' + SUITE
    if re.search(r'(^|[\s;&|])sed\s+-i\b', command):
        return ("For this guided profile, replace the complete solution.py module with a noninteractive heredoc: "
                "cat <<'PY' > solution.py\n# your full implementation\nPY\n"
                "Then run the full suite in a separate action. Do not use sed -i.")
    return None
