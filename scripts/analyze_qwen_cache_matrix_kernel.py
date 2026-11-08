from pathlib import Path
import json
root=Path(__file__).resolve().parents[1];base=root/'.artifacts/qwen-cache-stage3/run-b';out=root/'results/cache_policy/qwen_stage3b_2026-11-08'
samples=[json.loads(l) for l in (base/'resources.jsonl').read_text().splitlines()];rows=[]
for cap in [24,32,64]:
 for p in (base/f'capacity-{cap}').glob('concurrency-*/ridge_validation-quality.json'):
  q=json.loads(p.read_text())
  for call in q['tools']:
   if call['name']!='compute' or call['status']!='ok':continue
   start=call['started_ns'];end=call['ended_ns']
   first=min(samples,key=lambda r:abs(r['monotonic_ns']-start));last=min(samples,key=lambda r:abs(r['monotonic_ns']-end))
   delta={k:last['vmstat'][k]-v for k,v in first['vmstat'].items()}
   ps=call['worker_timing']['phases'];r=dict(capacity=cap,batch=p.parent.name,tool_wall_s=(end-start)/1e9,input_hash_s=call['input_check_ns']/1e9,post_hash_s=call['post_input_check_ns']/1e9,worker_system_cpu_s=call['worker_timing']['overall']['system_ns']/1e9,compute_wall_s=ps['numerical_compute']['wall_ns']/1e9,compute_system_cpu_s=ps['numerical_compute']['system_ns']/1e9,npz_wall_s=ps['npz_materialize']['wall_ns']/1e9,main_thread_runqueue_wait_s=call['worker_timing']['overall']['main_thread_runqueue_wait_ns']/1e9,global_vmstat_delta=delta,first_memory_pressure=first['memory_pressure'],last_memory_pressure=last['memory_pressure'],sample_start_offset_s=(first['monotonic_ns']-start)/1e9,sample_end_offset_s=(last['monotonic_ns']-end)/1e9,scope='global shared host counters, nearest10s samples, correlation not worker-attributed kernel cause')
   rows.append(r)
out.mkdir(exist_ok=True,parents=True);(out/'ridge-kernel-pressure.json').write_text(json.dumps(rows,indent=2)+'\n')
for r in sorted(rows,key=lambda r:r['worker_system_cpu_s'],reverse=True)[:8]:
 print(r['capacity'],r['batch'], 'tool',round(r['tool_wall_s'],2),'system',round(r['worker_system_cpu_s'],2),'compaction_stall',r['global_vmstat_delta'].get('compact_stall'),'thp_fault_alloc',r['global_vmstat_delta'].get('thp_fault_alloc'),'memory_PSI',r['last_memory_pressure'].splitlines()[0])
