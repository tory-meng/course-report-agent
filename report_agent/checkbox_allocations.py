"""旧版考核勾选矩阵：保留来源关系，明确区分勾选与新增的数值分配。"""
import math
import re
from .extraction_quality import cells,table_key

def find(blocks,goal_ids):
    expected=[g.removeprefix('G') for g in goal_ids]
    for b in blocks:
        cs=cells({'text':b['text']})
        if cs[-len(expected):]!=expected:continue
        if '考核方式' not in b['text']:continue
        rows=[]
        for row in blocks:
            if table_key(row)!=table_key(b) or row['id']==b['id']:continue
            rs=cells({'text':row['text']})
            if len(rs)>len(expected) and any(v in ('√','✓','✔') for v in rs[-len(expected):]):rows.append(row)
        if rows:return b,rows
    raise ValueError('未找到本课目标列完整的考核勾选矩阵')

def derive(binding,quotes,goal_ids):
    expected=[g.removeprefix('G') for g in goal_ids];header=cells({'text':quotes[binding['header_ref']]})
    if header[-len(expected):]!=expected:raise ValueError('考核勾选表的目标编号不符')
    rows={};groups={}
    for ref in binding['row_refs']:
        cs=cells({'text':quotes[ref]});flags=cs[-len(goal_ids):]
        if any(v not in ('','√','✓','✔') for v in flags):raise ValueError('勾选矩阵目标列含未知值')
        supported=[g for g,v in zip(goal_ids,flags) if v]
        if not supported:raise ValueError('评分项没有来源目标支撑')
        parent=cs[0].strip()
        percents=[float(m[1])/100 for c in cs[:-len(goal_ids)] if (m:=re.fullmatch(r'(\d+(?:\.\d+)?)\s*[%％]',c.strip()))]
        if not percents:raise ValueError('考核矩阵缺少可定位的总评权重')
        rows[ref]={'parent':parent,'name':cs[1],'parent_weight':percents[-1],
                   'literal_within_weight':percents[-2] if len(percents)>=2 else None,'goal_ids':supported}
        groups.setdefault(parent,[]).append(ref)
    global_rows={};normalizations=[]
    for parent,refs in groups.items():
        weights={rows[r]['parent_weight'] for r in refs}
        if len(weights)!=1:raise ValueError('同一考核方式总评权重不一致')
        within=[rows[r]['literal_within_weight'] for r in refs]
        if len(refs)==1:within=[1.]
        if any(v is None or v<=0 for v in within):raise ValueError('组内多项占比缺失')
        total=sum(within)
        if len(refs)>1 and abs(total-1)>.002:raise ValueError('组内多项百分比不闭合，不能强行归一化')
        normalizations.append({'parent':parent,'literal_sum':total,'normalized_sum':1.,'row_count':len(refs)})
        for ref,w in zip(refs,within):global_rows[ref]=next(iter(weights))*w/total
    used=[r for rs in binding['item_rows'].values() for r in rs]
    if len(used)!=len(set(used)) or set(used)!=set(rows):raise ValueError('考核勾选矩阵行有遗漏或重复使用')
    weights={};allocations={}
    for item,refs in binding['item_rows'].items():
        w=sum(global_rows[r] for r in refs);weights[item]=w
        allocations[item]={g:sum(global_rows[r]/len(rows[r]['goal_ids']) for r in refs if g in rows[r]['goal_ids'])/w for g in goal_ids}
    if not math.isclose(sum(weights.values()),1,abs_tol=1e-10):raise ValueError('考核总评权重之和不为1')
    return weights,allocations,{'source_rows':rows,'normalizations':normalizations,
        'allocation_scope':'勾选关系来自本课原表；同一评分项在勾选目标间均分属本次评价设计，非原文给出的目标子评分。'}

def verify(profile,facts):
    binding=profile['source_checkbox'];quotes={r:q for record in [facts['metadata']]+facts['items'] for r,q in record.get('quotes',{}).items()}
    weights,allocations,evidence=derive(binding,quotes,profile['goal_ids'])
    if set(weights)!=set(profile['item_total_weights']) or set(weights)!={i['id'] for i in facts['items'] if not i.get('summary_only')}:raise ValueError('勾选表与实际评分项集合不符')
    for item,w in weights.items():
        if not math.isclose(w,profile['item_total_weights'][item],abs_tol=1e-10):raise ValueError('来源考核权重被改写')
        if set(allocations[item])!=set(profile['allocations'][item]):raise ValueError('目标列不完整')
        if any(not math.isclose(v,profile['allocations'][item][g],abs_tol=1e-10) for g,v in allocations[item].items()):raise ValueError('勾选目标分配与声明的均分规则不符')
    return evidence
