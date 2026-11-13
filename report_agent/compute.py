"""式1、式2及同源统计；只使用确认的映射和政策，不生成伪成绩。"""
import statistics
from .common import number, signature

def summary(values,threshold=None):
    if not values:return {'valid_count':0,'min':None,'mean':None,'median':None,'max':None,'threshold':threshold,'passed':None,'rate':None}
    nums=list(values.values()); threshold=number(threshold) if threshold is not None else None
    return {'valid_count':len(nums),'min':min(nums),'mean':statistics.mean(nums),'median':statistics.median(nums),'max':max(nums),
            'threshold':threshold,'passed':sum(v>=threshold for v in nums) if threshold is not None else None,
            'rate':sum(v>=threshold for v in nums)/len(nums) if threshold is not None else None}

def status_score(student,key,policy):
    state=student['status'] if student['status']!='normal' else student['score_status'].get(key,'missing')
    value=student['total'] if key=='total' else student['scores'].get(key)
    if value is None and state=='normal':state='missing'
    action=policy.get(state)
    if action=='zero':return 0.0,None
    if action=='exclude':return None,state
    if action!='include':return None,'未确认状态处理：'+state
    if value is None:return None,'缺失评分'
    return number(value),None

def evaluate_terms(terms,students,items,policy):
    result={'values':{},'excluded':{},'issues':[],'source_refs':[]}
    try:
        if not terms:raise ValueError('缺少计算项与系数')
        seen=set(); valid=[]
        for term in terms:
            key=term['item_id']
            if key in seen:raise ValueError('重复计算项')
            if term.get('coefficient') is None:raise ValueError('缺少计算系数：'+key)
            if items.get(key,{}).get('max_score') is None:raise ValueError('缺少考核项满分：'+key)
            seen.add(key); c=number(term['coefficient']); m=number(items[key]['max_score'])
            if c<0 or m<=0:raise ValueError('系数须非负，满分须大于0')
            if c:valid.append((key,c,m))
        denominator=sum(c*m for _,c,m in valid)
        if denominator<=0:raise ValueError('加权满分分母为0')
    except (ValueError,TypeError,KeyError) as e:
        result['issues'].append(str(e));return result
    for s in students:
        numerator=0;reason=None
        for key,c,m in valid:
            x,reason=status_score(s,key,policy)
            if reason:break
            if not 0<=x<=m:raise ValueError(f'{s["anonymous_id"]}/{key}得分超出对应满分；检查原始分/折合分口径')
            numerator+=x*c
            if key in s['refs']:result['source_refs'].append(s['refs'][key])
        if reason:result['excluded'][s['anonymous_id']]=reason
        else:result['values'][s['anonymous_id']]=numerator/denominator
    result['denominator']=denominator
    result['source_refs']=sorted(set(result['source_refs']))
    return result

def calculate(case,facts,grades,aggregate=None):
    policy=case.get('evaluation_policy',{})
    from .decision_policy import enabled,used_states
    minimal=enabled(case)
    states=used_states(grades) if minimal else {'normal','missing','absent','deferred','exempt'}
    if not policy.get('source_refs') and (not minimal or states-{'normal'}):raise ValueError('evaluation_policy必须注明评价制度来源')
    for state in states:
        if policy.get(state) not in ('include','exclude','zero'):raise ValueError('未确认学生状态处理：'+state)
    if policy['normal']!='include':raise ValueError('正常有效评分应include')
    items={x['id']:x for x in facts.get('items',[])}; students=grades['students']; meta=facts['metadata']
    if meta.get('total_max') is None:raise ValueError('缺少总评满分total_max，请确认分数口径后补充facts')
    total_max=number(meta['total_max'])
    if total_max<=0:raise ValueError('总评满分必须>0')
    totals={};excluded={}
    for s in students:
        value,reason=status_score(s,'total',policy)
        if reason:excluded[s['anonymous_id']]=reason;continue
        if not 0<=value<=total_max:raise ValueError('总评超出满分')
        totals[s['anonymous_id']]=value
    pct={k:v/total_max*100 for k,v in totals.items()}
    counts=[sum(lo<=v<hi for v in pct.values()) for lo,hi in [(90,101),(80,90),(70,80),(60,70),(0,60)]]
    out={'registered_count':len(students),'total':{**summary(totals,meta.get('pass_score')),'values':totals,'percent_values':pct,'counts':counts,
          'excluded':excluded,'max_score':total_max,'source_refs':[s['refs']['total'] for s in students if 'total' in s['refs']]},
         'goals':{},'points':{},'assessments':{},'assessment_points':{},'issues':list(grades['warnings'])+list(facts.get('data_quality_issues',[]))}
    out['sample_scope']=grades.get('sample_scope',{})
    if not totals:out['issues'].append('总评没有有效样本，不能进行成绩统计')
    rules={x['id']:x for x in facts.get('goal_rules',[])}
    for goal in facts.get('goals',[]):
        threshold=goal.get('threshold')
        if threshold is not None and not 0<=number(threshold)<=1:raise ValueError('目标标准须在[0,1]')
        r=evaluate_terms(rules.get(goal['id'],{}).get('terms'),students,items,policy)
        out['goals'][goal['id']]={**r,**summary(r['values'],threshold)}
        if threshold is None:out['issues'].append(goal['id']+'缺少达成标准，不能判断达成率')
    from .report_scope import assessments,audit
    out['report_scope']=audit(facts)
    if out['report_scope']['matrix_only_assessments']:
        out['issues'].append(out['report_scope']['note']+' 目标系数表中待关联方式：'+'、'.join(a['name'] for a in out['report_scope']['matrix_only_assessments']))
    for a in assessments(facts,True):
        if a.get('raw_item_id'):
            key=a['raw_item_id'];values={};excluded={};refs=[]
            for student in students:
                value,reason=status_score(student,key,policy)
                if reason:excluded[student['anonymous_id']]=reason
                else:values[student['anonymous_id']]=value;refs.append(student['refs'][key])
            r={'values':values,'excluded':excluded,'issues':[],'source_refs':refs,'score_basis':'original_register_points'}
            out['assessments'][a['id']]={**r,**summary(values)}
        else:
            r=evaluate_terms(a.get('terms'),students,items,policy)
            out['assessments'][a['id']]={**r,**summary({k:v*100 for k,v in r['values'].items()})}
    for point in facts.get('points',[]):
        r={'values':{},'excluded':{},'issues':[],'source_refs':point.get('source_refs',[])}
        weights=point.get('goal_weights',{}); agg=point.get('aggregation')
        try:
            weights={k:number(v) for k,v in weights.items()}
            if not weights or any(w<0 for w in weights.values()) or sum(weights.values())<=0:raise ValueError('缺少有效目标到观测点系数')
            if agg not in ('weighted_mean','weighted_sum'):raise ValueError('未确认观测点汇总方式')
            for s in students:
                sid=s['anonymous_id']; numerator=0;missing=False
                for goal,w in weights.items():
                    if not w:continue
                    value=out['goals'].get(goal,{}).get('values',{}).get(sid)
                    if value is None:missing=True;break
                    numerator+=value*w
                if missing:r['excluded'][sid]='缺少组成目标的有效达成值'
                else:
                    v=numerator/sum(weights.values()) if agg=='weighted_mean' else numerator
                    if not 0<=v<=1:raise ValueError('观测点结果超出[0,1]，检查汇总口径')
                    r['values'][sid]=v
        except (ValueError,TypeError) as e:
            r['issues'].append('缺少已确认的目标到观测点系数或汇总方式' if any(v is None for v in point.get('goal_weights',{}).values()) else str(e));r['values']={}
        th=point.get('threshold')
        if th is None:out['issues'].append(point['id']+'缺少观测点标准，不能判断个体是否达标')
        if th is not None and not 0<=number(th)<=1:raise ValueError('观测点标准须在[0,1]')
        r.update(summary(r['values'],th));r['contribution']=None
        if point.get('course_weight') is not None and r['mean'] is not None:
            w=number(point['course_weight'])
            if not 0<=w<=1:raise ValueError('课程支撑权重须在[0,1]')
            r['contribution']=r['mean']*w
        out['points'][point['id']]=r
    for rule in facts.get('assessment_point_rules',[]):
        r=evaluate_terms(rule['terms'],students,items,policy)
        point=next(x for x in facts['points'] if x['id']==rule['point_id'])
        out['assessment_points'][rule['id']]={**r,**summary(r['values'],point.get('threshold')),'assessment_id':rule['assessment_id'],'point_id':rule['point_id']}
    for typ in ('goals','points','assessments','assessment_points'):
        for key,row in out[typ].items():
            out['issues'] += [f'{typ}/{key}: {e}' for e in row['issues']]
            if not row['valid_count']:out['issues'].append(f'{typ}/{key}: 没有可计算的有效样本')
    for key in ['goals','points','methods','assessments','items']:
        if not facts.get(key):out['issues'].append('缺少'+key)
    if aggregate:
        out['aggregate_goals']=aggregate['goals'];out['aggregate_audits']=aggregate['audits']
        for audit in aggregate['audits']:
            if audit['text_discrepancies']:
                out['issues'].append('目标评价表文字与公式重算不一致：'+audit['source_id']+'；班级汇总采用公式重算值，差异详见aggregate_evidence.json')
    if facts.get('evaluation_context'):
        out['evaluation_context']=facts['evaluation_context'];out['issues'].append(facts['evaluation_context']['warning'])
        for typ in ('goals','points','assessment_points'):
            for row in out[typ].values():row['evidence_status']='proxy_estimate'
    snapshot={'facts':facts,'grades':grades,'policy':policy}
    if aggregate:snapshot['aggregate_evidence']=aggregate
    out['snapshot_sha256']=signature(snapshot)
    return out
