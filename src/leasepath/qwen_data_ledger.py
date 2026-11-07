"""Durable generation reservations shared by probe, pilot and retry processes."""
import fcntl
import json
import os
from pathlib import Path
import time
import uuid

class GenerationBudget:
    def __init__(self,path,*,limits=None):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.limits=limits or {'probe':72,'pilot':72,'total':144}
        if (set(self.limits)!={'probe','pilot','total'} or
                any(type(v) is not int or v<=0 for v in self.limits.values()) or
                self.limits['total']>252 or self.limits['pilot']>228 or self.limits['probe']>72):
            raise ValueError('invalid bounded collection budget')

    def reserve(self,lane,**metadata):
        if lane not in ('probe','pilot'):
            raise ValueError('unknown budget lane')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_APPEND|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            if self.path.with_suffix('.sealed.json').exists():
                raise RuntimeError('generation ledger is sealed')
            with os.fdopen(os.dup(fd),'r') as stream:
                stream.seek(0)
                rows=[json.loads(line) for line in stream]
            used=[r for r in rows if r['kind']=='generation_reserved']
            if any(r.get('budget_limits',{'probe':72,'pilot':72,'total':144})!=self.limits for r in used):
                raise ValueError('ledger belongs to different fixed budget')
            if len(used)>=self.limits['total'] or sum(r['lane']==lane for r in used)>=self.limits[lane]:
                raise RuntimeError('generation budget exhausted')
            record={'kind':'generation_reserved','id':uuid.uuid4().hex,'lane':lane,
                    'ts_ns':time.monotonic_ns(),'prior_usage_status':'historical_unreconciled',
                    'budget_limits':self.limits,**metadata}
            data=(json.dumps(record,allow_nan=False)+'\n').encode()
            offset=0
            while offset<len(data):
                count=os.write(fd,data[offset:])
                if count<=0:raise OSError('ledger write made no progress')
                offset+=count
            os.fsync(fd)
            return record
        finally:
            os.close(fd)

    def summary(self):
        rows=[json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []
        rows=[r for r in rows if r['kind']=='generation_reserved']
        return {'generations':len(rows),'probe':sum(r['lane']=='probe' for r in rows),
                'pilot':sum(r['lane']=='pilot' for r in rows),'total_cap':self.limits['total'],
                'lane_caps':{k:self.limits[k] for k in ('probe','pilot')},
                'prior_usage_status':'historical_unreconciled'}
