"""评价证据边界与换算同源性审计；数值正确不自动证明测量效度。"""
import math

def review(facts,calc,profile=None):
    result={'mode':'proxy' if calc.get('evaluation_context') else 'evidence','equivalent_goal_groups':[],
            'notes':[],'threshold_sensitivity':{},'formal_measurement_verified':False}
    if not calc.get('evaluation_context'):return result
    items={i['id']:i for i in facts.get('items',[])};vectors={}
    for rule in facts.get('goal_rules',[]):
        terms=rule.get('terms',[])
        denominator=sum(items[t['item_id']]['max_score']*t['coefficient'] for t in terms)
        if denominator:vectors[rule['id']]={t['item_id']:t['coefficient']/denominator for t in terms if t['coefficient']}
    ungrouped=list(vectors)
    while ungrouped:
        gid=ungrouped.pop(0);group=[gid]
        for other in list(ungrouped):
            if set(vectors[gid])==set(vectors[other]) and all(math.isclose(v,vectors[other][key],rel_tol=1e-10,abs_tol=1e-12) for key,v in vectors[gid].items()):
                group.append(other);ungrouped.remove(other)
        if len(group)>1:result['equivalent_goal_groups'].append(group)
    result['notes'].append('目标达成度、达成率及观测点贡献均为候选方案试算；达到候选阈值不等于对应能力、课程或毕业要求已获正式认定。')
    for group in result['equivalent_goal_groups']:
        result['notes'].append('、'.join(group)+'采用相同评分项与归一化比例，曲线相同由换算规则决定，不能解释为这些能力已被独立测量且水平相同。')
    result['notes'].append('总评构成权重与各目标内支撑权重含义不同；目标内权重按该目标的加权满分贡献归一化，不再次乘总评权重。')
    if profile:
        result['threshold_reference']={k:profile.get('reference',{}).get(k) for k in ('file','sha256','threshold_evidence')}
        if profile.get('source_matrix'):
            result['notes'].append('候选阈值'+str(profile['threshold'])+'借鉴现有参考报告，尚无本课正式阈值依据；考核配分与课程支撑权重保留本课大纲矩阵，汇总项按来源权重聚合。')
        else:
            result['notes'].append('候选阈值'+str(profile['threshold'])+'借鉴现有参考报告，尚无本课正式阈值依据；课程支撑权重亦为拟定参数，不将H/M/L直接当作数值系数。')
    for gid,row in calc.get('goals',{}).items():
        values=list(row.get('values',{}).values());threshold=row.get('threshold')
        if not values or threshold is None:continue
        probes=sorted(set([threshold,min(1,round(threshold+.1,10)),min(1,round(threshold+.2,10))]))
        result['threshold_sensitivity'][gid]=[{'hypothetical_threshold':t,'valid_count':len(values),'passed':sum(v>=t for v in values),'rate':sum(v>=t for v in values)/len(values),'status':'sensitivity_scenario_not_official_standard'} for t in probes]
    result['notes'].append('观测点值仅反映本课程在当前映射下的支撑情况；同一目标映射多个观测点不构成多次独立测量，也不能替代专业毕业要求整体评价。')
    return result
