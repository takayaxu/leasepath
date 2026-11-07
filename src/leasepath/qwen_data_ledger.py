"""Durable generation reservations shared by probe, pilot and retry processes."""
import fcntl
import json
import os
from pathlib import Path
import time
import uuid

class GenerationBudget:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)

    def reserve(self,lane,**metadata):
        if lane not in ('probe','pilot'):
            raise ValueError('unknown budget lane')
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_APPEND|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            with os.fdopen(os.dup(fd),'r') as stream:
                stream.seek(0)
                rows=[json.loads(line) for line in stream]
            used=[r for r in rows if r['kind']=='generation_reserved']
            if len(used)>=144 or sum(r['lane']==lane for r in used)>=72:
                raise RuntimeError('generation budget exhausted')
            record={'kind':'generation_reserved','id':uuid.uuid4().hex,'lane':lane,
                    'ts_ns':time.monotonic_ns(),'prior_usage_status':'historical_unreconciled',**metadata}
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
                'pilot':sum(r['lane']=='pilot' for r in rows),'total_cap':144,'lane_cap':72,
                'prior_usage_status':'historical_unreconciled'}
