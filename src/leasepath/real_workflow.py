"""Bounded generic feedback; never edits or supplies an issue-specific fix."""
from collections import deque
import hashlib
import re


class ProgressGuard:
    def __init__(self, *, window=12, feedback_at=3, stop_at=6, phase_interval=6):
        self.history=deque(maxlen=window)
        self.feedback_at,self.stop_at,self.phase_interval=feedback_at,stop_at,phase_interval
        self.unchanged_steps=0

    def observe(self, command, returncode, output, before, after):
        changed=before!=after
        if changed:
            self.history.clear()
            self.unchanged_steps=0
        else:
            self.unchanged_steps+=1
        # Same failed output with the same patch remains a loop even if the
        # model cycles guessed symbols/commands. Never count successes here.
        failure=(returncode,hashlib.sha256(output.encode()).hexdigest(),hashlib.sha256(after).hexdigest())
        if returncode!=0 and not changed:self.history.append(failure)
        else:self.history.append(None)
        repeats=sum(x==failure for x in self.history) if returncode!=0 else 0
        notes=[]
        if repeats==self.feedback_at:
            notes.append('The same failure output has recurred three times without a source change. Stop guessing names. '
                         'Use rg -n on literal identifiers from the issue, read the existing implementation and revise your hypothesis.')
        if self.unchanged_steps and self.unchanged_steps%self.phase_interval==0:
            notes.append('No patch progress in six actions. State what you learned, reproduce the actual issue, '
                         'and make a minimal implementation change. Do not repeat broad searches or invent paths.')
        if not changed and re.search(r'sed\s+-i|\.write_text\(|\.write_bytes\(',command):
            notes.append('EDIT CHECK: this command produced no patch change. Verify the exact existing source text '
                         'and assert a unique old snippet before replacement; a successful sed exit code does not prove an edit.')
        return {'notes':notes,'stop':repeats>=self.stop_at,'equivalent_failure_count':repeats,
                'unchanged_steps':self.unchanged_steps}
