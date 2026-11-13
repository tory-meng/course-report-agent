"""版本化候选评价配置。计算模型与本轮事实分离，代理估计必须显式启用。"""
import copy
from .common import load, save, path, digest, signature, number
from .report_scope import assessments
from .reference_db import reference_identity
from .reference_plans import rank_references

VERSION='course_evaluation_profile_v1'
MODEL='uniform_within_assessment_v1'
WARNING='评价方案试算：目标及观测点个体结果含代理估计；名称对应、空项解释、阈值及均匀分配规则待核验，不等于实际目标细评分。'


def create_profile(case_path,runtime_path,source_run,out):
    case=load(path(case_path));runtime=load(path(runtime_path));run=path(source_run);out=path(out)
    if out.exists():raise ValueError('评价配置已存在，不覆盖；请选择新的配置文件')
    manifest=load(run/'run_manifest.json');facts=load(run/'facts.json')
    hashes={s['id']:digest(path(s['path'])) for s in case['sources']}
    if manifest.get('case_id')!=case['id'] or manifest.get('group_id')!=case.get('group_id',case['id']) or manifest['inputs']!=hashes:
        raise ValueError('用于拟定配置的课程/输入与当前案例不一致')
    from .pipeline import load_template
    template=load_template(runtime,case.get('report_type','course'))
    if template['hashes']!=manifest['template_hashes']:raise ValueError('旧任务母版与当前绑定不同')
    configs=case.get('evaluation_workbooks',[])
    if len(configs)!=1:raise ValueError('需显式指定一份目标分配表，不能从参考报告猜目标矩阵')
    names=[a['name'] for a in assessments(facts)]
    refs=reference_identity(runtime)
    if not refs:raise ValueError('未配置可核验的参考库')
    ranking=rank_references(path(refs['db']),names,[g['text'] for g in facts['goals']])
    if not ranking or not ranking[0]['threshold_candidates']:raise ValueError('主参考没有可定位的目标阈值')
    # 名称对应只能生成候选，既有不同命名的实体继续留在facts.json。
    known={'平时成绩':'平时考核','验收报告':'设计报告','期末答辩':'验收答辩'}
    matrix_names=list(dict.fromkeys(a['name'] for a in facts['assessments'] if any(r['assessment_id']==a['id'] for r in facts['assessment_goal_weights'])))
    aliases={name: name if name in names else known.get(name) for name in matrix_names}
    if any(v not in names for v in aliases.values()) or set(aliases.values())!=set(names):
        raise ValueError('无法唯一提出名称对应；需在候选配置中显式填写，不拼接相近名字')
    primary=ranking[0];threshold=primary['threshold_candidates'][0]
    profile={'version':VERSION,'id':case['id']+'_'+signature({'inputs':hashes,'reference':primary['sha256']})[:12],
        'status':'proposed','case_id':case['id'],'group_id':case.get('group_id',case['id']),
        'course_name':case['course_name'],'term':facts['metadata'].get('term'),
        'source_hashes':hashes,'template_id':template['contract']['template_id'],'reference_library_sha256':refs['sha256'],
        'goal_ids':[g['id'] for g in facts['goals']],
        'reference':{'file':primary['file'],'source_path':str(path(runtime.get('reference_library',{}).get('source','inputs/references'))/primary['file']),
                     'sha256':primary['sha256'],'reference_id':primary['reference_id'],
                     'threshold_evidence':threshold,'score':primary['score'],'other_editions':primary['other_editions']},
        'model':MODEL,'threshold':threshold['value'],'aliases':aliases,'workbook_config_id':configs[0]['id'],
        'blank_policy':'not_participating_in_this_proposal','point_policy':'existing_single_goal_identity',
        'assumptions':['所列考核名称为候选一一对应；不改变原事实实体',
            '目标分配表未填三元组仅在本方案中解释为不参与',
            '分项内各目标得分率与该分项总得分率相同，属于代理估计',
            '借用主参考阈值，原课程总评公式保持不变',
            '现有单目标对应观测点按1:1汇总并采用同一候选阈值'],
        'source_run':str(run),'source_facts_sha256':digest(run/'facts.json'),
        'approval':{'approved':False,'reviewer':''},'scope':WARNING}
    validate_profile(profile,case,template,refs,hashes)
    save(out,profile);save(out.with_suffix('.ranking.json'),{'ranking':ranking,'reference_library':refs})
    return {'path':str(out),'id':profile['id'],'status':'proposed','reference':primary['file']}


def validate_profile(profile,case,template,refs,inputs):
    if profile.get('version')=='course_evaluation_profile_v2':
        from .standalone_profile import validate
        return validate(profile,case,template,refs,inputs)
    if profile.get('version')!=VERSION or profile.get('model')!=MODEL:raise ValueError('未知评价配置版本或计算模型')
    if profile.get('status')!='proposed':raise ValueError('当前入口仅支持明确标识的候选试算配置，不自动接受审批标记')
    if profile.get('approval',{}).get('approved'):raise ValueError('候选试算不能带事实或训练批准标记')
    for key,wanted in [('case_id',case['id']),('group_id',case.get('group_id',case['id'])),('course_name',case['course_name']),
                       ('source_hashes',inputs),('template_id',template['contract']['template_id']),
                       ('reference_library_sha256',(refs or {}).get('sha256'))]:
        if profile.get(key)!=wanted:raise ValueError('评价配置绑定不符：'+key)
    threshold=number(profile.get('threshold'))
    if not 0<=threshold<=1:raise ValueError('评价阈值须在[0,1]')
    if threshold!=number(profile['reference']['threshold_evidence']['value']):raise ValueError('候选阈值与标注的参考原文不一致')
    reference=path(profile['reference']['source_path'])
    # reference_identity已校验库内全部来源；这里再核对选择的具体文件。
    if not reference.is_file() or digest(reference)!=profile['reference']['sha256']:raise ValueError('候选主参考文件与绑定哈希不符')
    if profile.get('blank_policy')!='not_participating_in_this_proposal' or profile.get('point_policy')!='existing_single_goal_identity':
        raise ValueError('未支持的空项解释或观测点汇总规则')
    if not profile.get('assumptions'):raise ValueError('候选配置必须保留假设说明')


def compile_plan(profile,case,facts,aggregate):
    if profile.get('version')=='course_evaluation_profile_v2':
        from .standalone_profile import compile_plan as standalone
        return standalone(profile,case,facts,aggregate)
    if any(r.get('terms') and all(t.get('coefficient') is not None for t in r['terms']) for r in facts.get('goal_rules',[])):
        raise ValueError('已有明确的目标评分规则，不能用参考代理模型覆盖')
    for goal in facts['goals']:
        if goal.get('threshold') is not None and number(goal['threshold'])!=number(profile['threshold']):
            raise ValueError('本课已有不同目标阈值，不能由参考方案覆盖')
    if set(profile['goal_ids'])!={g['id'] for g in facts['goals']}:raise ValueError('评价配置目标集合不符')
    if profile.get('term')!=facts['metadata'].get('term'):raise ValueError('评价配置执行学期不符')
    workbook=next((a for a in aggregate['audits'] if a['config_id']==profile['workbook_config_id']),None)
    if workbook is None:raise ValueError('评价配置指向未核验的目标分配表')
    f=copy.deepcopy(facts);primary=assessments(f);names={a['name']:a for a in primary};aliases=profile['aliases']
    if len(set(aliases.values()))!=len(aliases) or set(aliases.values())!=set(names):raise ValueError('候选名称对应必须一一覆盖正式分项')
    entered={c['assessment'] for g in workbook['goals'].values() for c in g['components']}
    if entered!=set(aliases):raise ValueError('名称对应没有覆盖评价表已填项目')
    scales={name:sum(c['maximum'] for g in workbook['goals'].values() for c in g['components'] if c['assessment']==name) for name in aliases}
    if any(number(v)<=0 for v in scales.values()):raise ValueError('候选分项满分无效')
    f['assessments']=primary;ids={a['id'] for a in primary};f['items']=[i for i in f['items'] if i['assessment_id'] in ids]
    items={}
    for a in primary:
        matches=[i for i in f['items'] if i['assessment_id']==a['id']]
        if len(matches)!=1 or a.get('raw_item_id')!=matches[0]['id']:raise ValueError('代理模型要求每个考核方式唯一对应一列实际原始分')
        items[a['name']]=matches[0]
    for source_name,name in aliases.items():
        item=items[name];a=names[name]
        if item.get('max_score') is not None and number(item['max_score'])!=number(scales[source_name]):
            raise ValueError('目标分配满分与已有分项满分不一致，不自动换算口径')
        item.update(max_score=scales[source_name],converted_max=scales[source_name]*number(a['total_weight']),goal_weights={})
    f['goal_rules']=[];f['assessment_goal_weights']=[];f['assessment_point_rules']=[]
    for point in f['points']:
        if len(point.get('goal_weights',{}))!=1:raise ValueError('多目标观测点不能猜测汇总关系')
        if point.get('threshold') is not None and number(point['threshold'])!=number(profile['threshold']):
            raise ValueError('本课已有不同观测点阈值，不接受参考覆盖')
        if point.get('aggregation') not in (None,'weighted_mean') or any(v is not None and number(v)!=1 for v in point['goal_weights'].values()):
            raise ValueError('本课已有不同观测点汇总规则，不替换为候选1:1关系')
        point.update(goal_weights={g:1 for g in point['goal_weights']},aggregation='weighted_mean',threshold=profile['threshold'])
    for goal in f['goals']:
        gid=goal['id'];goal['threshold']=profile['threshold'];terms=[]
        rows={c['assessment']:c for c in workbook['goals'][gid]['components']}
        for source_name,name in aliases.items():
            a=names[name];item=items[name];row=rows.get(source_name)
            allocation,weight=(number(row['maximum']),number(row['weight'])) if row else (0,0)
            item['goal_weights'][gid]=allocation/scales[source_name]
            f['assessment_goal_weights'].append({'id':a['id']+':'+gid,'assessment_id':a['id'],'goal_id':gid,'weight':weight,'origin':'proposed_profile'})
            if allocation:
                terms.append({'item_id':item['id'],'coefficient':weight*allocation/scales[source_name]})
                points=[p for p in f['points'] if gid in p['goal_weights']]
                for point in points:f['assessment_point_rules'].append({'id':a['id']+':'+point['id'],
                    'assessment_id':a['id'],'point_id':point['id'],'terms':[{'item_id':item['id'],'coefficient':1}],
                    'source_refs':[f"profile:{profile['id']}"],'origin':'proxy_estimate'})
        f['goal_rules'].append({'id':gid,'terms':terms,'origin':'proxy_estimate'})
    f['evaluation_context']={'mode':'scenario_estimate','profile_id':profile['id'],'warning':WARNING,
                              'model':MODEL,'status':'proposed','assumptions':profile['assumptions']}
    f['review']={'approved':False,'reviewer':''}
    return f
