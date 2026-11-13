"""把当前大纲的考核配分矩阵汇总到实际已登记的评分项；保留来源而非重新均分。"""
import math
from .extraction_quality import cells

def derive(matrix,quotes,goal_ids):
    header=cells({'text':quotes[matrix['goal_header_ref']]})
    if header[1:1+len(goal_ids)]!=[g.removeprefix('G') for g in goal_ids]:
        raise ValueError('来源矩阵的目标列顺序与本课目标不符')
    rows={}
    for ref in matrix['row_refs']:
        cs=cells({'text':quotes[ref]})
        if len(cs)<2+len(goal_ids):raise ValueError('来源矩阵列数不全')
        w=float(cs[1]);allocation={g:float(cs[n+2]) if cs[n+2].strip() else 0. for n,g in enumerate(goal_ids)}
        if not 0<w<=1 or any(not 0<=v<=1 for v in allocation.values()) or not math.isclose(sum(allocation.values()),1,abs_tol=1e-9):
            raise ValueError('来源矩阵配分不合法，需先裁决冲突')
        if ref in rows:raise ValueError('来源矩阵重复行')
        rows[ref]=(w,allocation)
    slices=matrix.get('item_slices')
    if slices is None:slices={item:[{'source_ref':r,'goal_ids':goal_ids} for r in refs] for item,refs in matrix['item_rows'].items()}
    used=[(s['source_ref'],g) for ss in slices.values() for s in ss for g in s['goal_ids'] if rows[s['source_ref']][1][g]>0]
    expected={(r,g) for r,(_,a) in rows.items() for g,v in a.items() if v>0}
    if len(used)!=len(set(used)) or set(used)!=expected:raise ValueError('来源矩阵行或目标分片遗漏、重复计分')
    weights={};allocations={}
    for item,ss in slices.items():
        contributions={g:sum(rows[s['source_ref']][0]*rows[s['source_ref']][1][g] for s in ss if g in s['goal_ids']) for g in goal_ids}
        w=sum(contributions.values())
        if w<=0:raise ValueError('评分项没有来源权重')
        weights[item]=w
        allocations[item]={g:v/w for g,v in contributions.items()}
    if not math.isclose(sum(weights.values()),1,abs_tol=1e-9):raise ValueError('来源总评权重之和不为1')
    return weights,allocations

def verify(profile,facts):
    matrix=profile.get('source_matrix')
    if not matrix:raise ValueError('缺少同课矩阵的来源绑定')
    quotes={r:q for record in [facts['metadata']]+facts['goals']+facts['items'] for r,q in record.get('quotes',{}).items()}
    weights,allocations=derive(matrix,quotes,profile['goal_ids'])
    active={i['id'] for i in facts['items'] if not i.get('summary_only')}
    if set(weights)!=active or set(profile['item_total_weights'])!=active or set(profile['allocations'])!=active:
        raise ValueError('来源矩阵与实际评分项集合不符')
    for item in active:
        if not math.isclose(profile['item_total_weights'][item],weights[item],abs_tol=1e-10):raise ValueError('来源总评权重被改写')
        if set(profile['allocations'][item])!=set(profile['goal_ids']):raise ValueError('来源目标配分列缺失')
        for goal in profile['goal_ids']:
            if not math.isclose(profile['allocations'][item][goal],allocations[item][goal],abs_tol=1e-10):raise ValueError('来源目标配分被改写')
    return {'row_refs':matrix['row_refs'],'item_rows':matrix.get('item_rows'),'item_slices':matrix.get('item_slices'),'verified_weights':weights,
            'verified_allocations':allocations,'scope':'按本课大纲矩阵聚合已登记的考核项；不代表存在未登记的学生细项评分'}
