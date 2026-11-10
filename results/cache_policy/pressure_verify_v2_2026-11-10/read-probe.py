import os,json,time,resource,hashlib,sys
p=sys.argv[1];offset=int(sys.argv[2]);buffer=bytearray(1024**2);start=time.monotonic();h=hashlib.sha256()
with open(p,'rb',buffering=0) as f:
 f.seek(offset)
 for n in range(64):
  count=f.readinto(buffer);h.update(memoryview(buffer)[:count])
  if n%16==0:print(json.dumps({'bytes':(n+1)*1024**2,'elapsed':time.monotonic()-start}),flush=True)
print(json.dumps({'status':'complete','elapsed':time.monotonic()-start,'offset':offset,'sha256':h.hexdigest(),'major_faults':resource.getrusage(resource.RUSAGE_SELF).ru_majflt,'io':open('/proc/self/io').read()}),flush=True)
