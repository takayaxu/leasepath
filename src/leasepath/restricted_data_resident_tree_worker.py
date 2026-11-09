"""Same numerical work; retain resource use of library-discovery descendants."""
from contextlib import redirect_stdout
import io
import json
import subprocess
import time
from .resident_worker_process import UsagePopen


def main():
    children=[]
    class TrackedPopen(UsagePopen):
        def __init__(self,*args,**kwargs):
            self.record={'argv':args[0] if args else kwargs.get('args'),'began_ns':time.monotonic_ns()}
            super().__init__(*args,**kwargs)
            self.record['pid']=self.pid
            children.append(self.record)
        def _try_wait(self,flags):
            pid,status=super()._try_wait(flags)
            if pid:self.record.update(ended_ns=time.monotonic_ns(),usage=self.lifetime_usage)
            return pid,status
    subprocess.Popen=TrackedPopen
    from . import restricted_data_resident_worker as worker
    stream=io.StringIO()
    with redirect_stdout(stream):worker.main()
    envelope=json.loads(stream.getvalue())
    envelope['file_diagnostics']['descendant_processes']=children
    envelope['file_diagnostics']['lifetime_scope']='wait4 includes worker and reaped descendants; worker self counters separate'
    print(json.dumps(envelope,allow_nan=False))

if __name__=='__main__':main()
