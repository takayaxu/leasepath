"""Paired actual-model development comparisons, with failure denominators."""
import argparse
import json
from pathlib import Path
import statistics


def read(path):return json.loads(Path(path).read_text())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pipeline',type=Path,required=True);args=parser.parse_args()
    root=args.pipeline;plan=read(root/'plan.json');deliveries=[]
    for arm in plan['e2e_arms']:
        ev=root/'e2e'/arm['name'];path=ev/'plan.json'
        if not path.exists() or not (ev/'audit/summary.json').exists():continue
        config=read(path);work=Path(config['work']);audit=read(ev/'audit/summary.json')
        assert audit['status']=='passed'
        summary=read(work/'summary.json');rows={(r['owner'],r['round']):r for r in [read(p) for p in (work/'rounds').glob('*.json')]}
        deliveries.append(dict(name=arm['name'],summary=summary,rounds=rows,work=str(work)))
    baseline=next((d for d in deliveries if d['name']=='lru'),None)
    if baseline is None:raise ValueError('no audited LRU arm')
    comparisons=[]
    for arm in deliveries:
        common=[k for k in baseline['rounds'].keys() & arm['rounds'].keys() if baseline['rounds'][k]['success'] and arm['rounds'][k]['success']]
        lru=[baseline['rounds'][k]['response_s'] for k in common];actual=[arm['rounds'][k]['response_s'] for k in common]
        summary=arm['summary'];ref=baseline['summary']
        comparison=dict(arm=arm['name'],observed_rounds=len(arm['rounds']),planned_rounds=48,
            successful_rounds=sum(r['success'] for r in arm['rounds'].values()),common_success_rounds=len(common),
            paired_lru_mean_response_s=statistics.mean(lru) if lru else None,
            paired_mean_response_s=statistics.mean(actual) if actual else None,
            paired_response_change_pct=(sum(actual)/sum(lru)-1)*100 if lru and sum(lru)>0 else None,
            cached_tokens=summary['cached_tokens'],prefill_tokens=summary['prefill_tokens'],prefill_s=summary['prefill_s'],
            prefill_s_change_pct=(summary['prefill_s']/ref['prefill_s']-1)*100 if ref['prefill_s'] else None,
            evictions=summary['evictions'],prediction_calls=summary['prediction_calls'],deployed_updates=summary['deployed_updates'],
            limitations='one ordered development cycle; no repeat confidence or strict causal claim; request-trajectory equivalence still needs checking')
        comparisons.append(comparison)
    result=dict(status='development_comparison',comparisons=comparisons,holdout_model_tasks=0,
        scope='actual natural-tool continuing Agent runs; one cycle is diagnostic, not final multi-repeat E2E acceptance')
    (root/'e2e_comparison.json').write_text(json.dumps(result,indent=2)+'\n')
    text='# 持续项目开发端到端对照\n\n单轮开发对照；完整原生／反馈／KV 审计通过。无独立留出或多轮置信区间结论。\n\n'
    text+='| 方法 | 成功轮次／48 | 共同成功轮次 | 配对平均响应秒 | 响应变化 | 复用 tokens | prefill 秒 | 更新数 |\n|---|---:|---:|---:|---:|---:|---:|---:|\n'
    for r in comparisons:
        mean=f'{r["paired_mean_response_s"]:.3f}' if r['paired_mean_response_s'] is not None else '不可估计'
        change=f'{r["paired_response_change_pct"]:+.2f}%' if r['paired_response_change_pct'] is not None else '不可估计'
        text+=f'| {r["arm"]} | {r["successful_rounds"]} | {r["common_success_rounds"]} | {mean} | {change} | {r["cached_tokens"]} | {r["prefill_s"]:.3f} | {r["deployed_updates"]} |\n'
    text+='\n成功子集响应时间须结合全 48 轮失败分母阅读；失败提前结束不算加速。单次顺序执行受主机和工具波动影响，后续按冻结计划补平衡重复。\n'
    (root/'e2e_comparison.md').write_text(text)


if __name__=='__main__':main()
