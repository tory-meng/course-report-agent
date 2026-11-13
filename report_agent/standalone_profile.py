"""无目标汇总工作簿时的显式代理配置；源事实、候选分配与登记成绩分离。"""
import copy
import math
from .common import number,path,digest

VERSION='course_evaluation_profile_v2'
MODEL='source_item_allocation_proxy_v1'

def validate(p,case,template,refs,inputs):
    if p.get('version')!=VERSION or p.get('model')!=MODEL or p.get('status')!='proposed':raise ValueError('未知独立候选配置')
    if p.get('approval',{}).get('approved'):raise ValueError('代理配置不得携带人工审批')
    for k,v in [('case_id',case['id']),('group_id',case.get('group_id',case['id'])),('course_name',case['course_name']),
                ('source_hashes',inputs),('template_id',template['contract']['template_id']),('reference_library_sha256',(refs or {}).get('sha256'))]:
        if p.get(k)!=v:raise ValueError('独立评价配置绑定不符：'+k)
    if not case.get('facts_path') or p.get('source_facts_sha256')!=digest(path(case['facts_path'])):raise ValueError('候选配置与源事实草稿不符')
    ref=p['reference'];f=path(ref['source_path'])
    if not f.is_file() or digest(f)!=ref['sha256']:raise ValueError('参考文件绑定不符')
    threshold=number(p['threshold'])
    if not 0<=threshold<=1 or threshold!=number(ref['threshold_evidence']['value']):raise ValueError('阈值与参考证据不符')
    if not p.get('assumptions'):raise ValueError('代理配置须声明假设')

def compile_plan(p,case,facts,aggregate):
    if facts.get('goal_rules') or facts.get('assessment_point_rules') or any(r.get('weight') is not None for r in facts.get('assessment_goal_weights',[])):
        raise ValueError('已有目标细分规则，不能用独立代理配置覆盖')
    f=copy.deepcopy(facts);goals={g['id']:g for g in f['goals']};items={i['id']:i for i in f['items']};parents={a['id']:a for a in f['assessments']}
    active={k:i for k,i in items.items() if not i.get('summary_only')}
    if set(p['allocations'])!=set(active) or set(p['goal_ids'])!=set(goals):raise ValueError('候选目标或评分项集合不符')
    if p.get('term')!=f['metadata'].get('term'):raise ValueError('候选执行学期不符')
    if not math.isclose(sum(number(p['item_total_weights'][k]) for k in active),1,abs_tol=1e-10):raise ValueError('原始分项总评占比和不为1')
    coefficients={g:{} for g in goals}
    for key,item in items.items():
        maximum=number(p['item_maxima'][key])
        if maximum<=0 or item.get('max_score') is not None and not math.isclose(number(item['max_score']),maximum):raise ValueError('候选分项满分与已知值冲突')
        item['max_score']=maximum
        if item.get('summary_only'):continue
        row=p['allocations'][key]
        if set(row)!=set(goals) or any(not 0<=number(v)<=1 for v in row.values()) or not math.isclose(sum(map(number,row.values())),1,abs_tol=1e-10):raise ValueError('目标配分行须完整且和为1')
        w=number(p['item_total_weights'][key])
        if not 0<=w<=1:raise ValueError('分项总评占比越界')
        # 总评占比作用于得分率。50分题项与100分题项不能再直接按原始分相加。
        item.update(goal_weights=copy.deepcopy(row),converted_max=100*w)
        for gid,v in row.items():coefficients[gid][key]=100*w*number(v)/maximum
    for aid,a in parents.items():
        weight=sum(number(p['item_total_weights'][k]) for k,i in active.items() if i['assessment_id']==aid)
        if a.get('total_weight') is not None and not math.isclose(number(a['total_weight']),weight,abs_tol=1e-10):raise ValueError('已知总评权重不能被候选覆盖')
        a['total_weight']=weight
    f['goal_rules']=[];f['assessment_goal_weights']=[];f['assessment_point_rules']=[]
    for gid,g in goals.items():
        if g.get('threshold') is not None and number(g['threshold'])!=number(p['threshold']):raise ValueError('已知目标阈值不能覆盖')
        g['threshold']=p['threshold'];cs=coefficients[gid];den=sum(items[k]['max_score']*v for k,v in cs.items())
        if den<=0:raise ValueError('目标没有可用评分项：'+gid)
        f['goal_rules'].append({'id':gid,'terms':[{'item_id':k,'coefficient':v} for k,v in cs.items() if v],'origin':'proxy_estimate'})
        for aid in parents:
            contribution=sum(items[k]['max_score']*v for k,v in cs.items() if items[k]['assessment_id']==aid)
            f['assessment_goal_weights'].append({'id':aid+':'+gid,'assessment_id':aid,'goal_id':gid,'weight':contribution/den,'origin':'proxy_estimate'})
    denominators={gid:sum(items[k]['max_score']*v for k,v in cs.items()) for gid,cs in coefficients.items()}
    for point in f['points']:
        mapping=point.get('goal_weights',{})
        if not mapping or point.get('aggregation') not in (None,'weighted_mean'):raise ValueError('不能覆盖已知观测点关系')
        proposed=p.get('point_goal_weights',{}).get(point['id'])
        if proposed is None:
            if len(mapping)!=1:raise ValueError('多目标观测点需明确的独立汇总制度')
            proposed={next(iter(mapping)):1}
        if set(proposed)!=set(mapping) or any(g not in goals for g in proposed):raise ValueError('候选观测点关联与来源不符')
        point_weights={g:number(v) for g,v in proposed.items()}
        if any(v<0 or v>1 for v in point_weights.values()) or not math.isclose(sum(point_weights.values()),1,abs_tol=1e-10):raise ValueError('观测点内目标权重和须为1')
        if any(v is not None and not math.isclose(number(v),point_weights[g],abs_tol=1e-10) for g,v in mapping.items()):raise ValueError('不能覆盖已知观测点关系')
        weight=number(p['point_course_weights'][point['id']])
        if not 0<=weight<=1 or point.get('course_weight') is not None and number(point['course_weight'])!=weight:raise ValueError('观测点贡献权重冲突')
        if point.get('threshold') is not None and number(point['threshold'])!=number(p['threshold']):raise ValueError('已知观测点阈值不能覆盖')
        point.update(goal_weights=point_weights,aggregation='weighted_mean',threshold=p['threshold'],course_weight=weight)
        for aid in parents:
            if len(point_weights)==1:
                gid=next(iter(point_weights));cs=coefficients[gid]
            else:
                # 先按每个目标自己的满分归一化，再合并观测点；不能偏向满分更大的目标。
                cs={k:sum(point_weights[g]*coefficients[g].get(k,0)/denominators[g] for g in point_weights) for k in active}
            terms=[{'item_id':k,'coefficient':v} for k,v in cs.items() if v and items[k]['assessment_id']==aid]
            if terms:f['assessment_point_rules'].append({'id':aid+':'+point['id'],'assessment_id':aid,'point_id':point['id'],'terms':terms,'origin':'proxy_estimate'})
    f['evaluation_context']={'mode':'scenario_estimate','profile_id':p['id'],'model':MODEL,'status':'proposed',
         'warning':'自动拟定评价方案：目标配分、阈值及观测点贡献含代理估计，不等于学生真实目标细评分。','assumptions':p['assumptions']}
    f['review']={'approved':False,'reviewer':''}
    return f
