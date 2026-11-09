"""wait4 child-lifetime faults, including interpreter startup and exit."""
import os
import subprocess
import sys


class UsagePopen(subprocess.Popen):
    lifetime_usage = None

    def _try_wait(self, wait_flags):
        try:
            pid, status, usage = os.wait4(self.pid, wait_flags)
        except ChildProcessError:
            return self.pid, 0
        if pid:
            self.lifetime_usage = {'major_faults':usage.ru_majflt,'minor_faults':usage.ru_minflt,
                'block_input_ops':usage.ru_inblock,'block_output_ops':usage.ru_oublock,
                'user_s':usage.ru_utime,'system_s':usage.ru_stime,
                'scope':'wait4 worker lifetime including startup, exit, and reaped descendants'}
        return pid, status


def worker_command(output, call_id):
    command = [sys.executable, '-m', 'leasepath.restricted_data_resident_tree_worker']
    if os.environ.get('LEASEPATH_TRACE_FILES') == '1':
        # Only preparation diagnostics use ptrace. Never enabled during JCT.
        return ['/usr/bin/strace','-ff','-qq','-ttt','-T','-yy','-s','4096',
                '-e','trace=%file,read,pread64,mmap,munmap',
                '-o',str(output/(call_id+'-files.strace')), *command]
    return command
