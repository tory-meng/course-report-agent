"""显式来源设计入口：事实、候选评价与原登记总评分离，不放宽旧自动适配门禁。

prepare(case_file, runtime_file, design_file, out)只做确定性准备，不请求模型。
design.version=course_source_design_v1；需绑定case_id及全部source_hashes，提供
metadata/goals/points/methods/assessments/items、mappings和evaluation。
记录source_refs必须来自本轮材料；quotes可省略，由实际证据块回填。
evaluation含item_maxima、item_total_weights、allocations、point_goal_weights、
point_course_weights和assumptions；reference可显式绑定，也可从现有库筛选阈值。
生成后用run --evaluation-profile OUT/evaluation_profile.proposed.json
--allow-estimates。故意禁用evaluation_design自动推断，避免用拟合值覆盖登记分。
"""
import copy
from decimal import Decimal,ROUND_HALF_UP
import math
from pathlib import Path

from .common import load,save,path,digest,number,signature
from .facts import LISTS,validate_facts
from .readers import ingest,evidence_index
from .grade_preflight import case_preflight
from .grades import parse_grades
from .grade_selection import identities
from .grade_layout import label
from .compute import status_score,calculate
from .standalone_profile import validate as validate_profile,compile_plan
from .reference_db import reference_identity
from .reference_plans import rank_references
from .pipeline import load_template

VERSION='course_source_design_v1'


def source_records(design,sources,course_name):
    """把来源设计转换为项目facts结构；不以设计文本充当原文引文。"""
    supplied=design.get('facts',design)
    if supplied.get('review',{}).get('approved') or design.get('approval',{}).get('approved'):
        raise ValueError('来源设计不能自动授予人工批准')
    index=evidence_index(sources)
    facts={key:copy.deepcopy(supplied.get(key,[])) for key in LISTS}
    facts['metadata']=copy.deepcopy(supplied.get('metadata',{}))
    facts.update(review={'approved':False,'reviewer':''},conflicts=[])
    for kind,record in [('metadata',facts['metadata'])]+[(key,r) for key in LISTS for r in facts[key]]:
        refs=record.get('source_refs',[])
        if not refs or any(ref not in index for ref in refs):raise ValueError('来源设计记录缺少本轮有效引用：'+kind)
        if any(index[ref]['role']!='current' for ref in refs) and not (kind=='narratives' and record.get('section')=='6.1'):
            raise ValueError('来源设计只能以本轮材料支撑课程记录')
        quotes=record.setdefault('quotes',{})
        for ref in refs:
            if ref not in quotes:quotes[ref]=index[ref]['text']
    if any(facts[k] for k in ('goal_rules','assessment_goal_weights','assessment_point_rules')):
        raise ValueError('紧凑来源设计不接收预先混入事实的目标细评分规则')
    if not facts['goals'] or not facts['points'] or not facts['items'] or not facts['assessments']:
        raise ValueError('来源设计需完整的目标、观测点、考核方式及评分项')
    facts['preparation']={'origin':'explicit_source_bound_course_design','human_facts_file_supplied':False,
        'scope':'课程目标与活动依据本轮材料；缺少的观测点与配分为本次明确设计，不冒充学校正式制度。'}
    facts['data_quality_issues']=copy.deepcopy(design.get('data_quality_issues',[]))
    facts['data_quality_issues'].append('正式总评按原登记保留；课程目标评价使用本次来源设计分项方案，二者不强迫相同。')
    validate_facts(facts,sources,course_name)
    return facts


def select_reference(runtime,facts,supplied=None):
    refs=reference_identity(runtime)
    if not refs:raise ValueError('来源设计需要已绑定的参考库阈值出处')
    ranking=rank_references(path(refs['db']),[a['name'] for a in facts['assessments']],[g['text'] for g in facts['goals']])
    if supplied is not None:return copy.deepcopy(supplied),refs,ranking
    chosen=next((row for row in ranking if row.get('threshold_candidates')),None)
    if chosen is None:raise ValueError('筛选后的课程库没有可定位评价阈值')
    source_root=path(runtime.get('reference_library',{}).get('source','inputs/references'))
    return {'file':chosen['file'],'source_path':str(source_root/chosen['file']),'sha256':chosen['sha256'],
        'reference_id':chosen['reference_id'],'threshold_evidence':chosen['threshold_candidates'][0],
        'score':chosen['score'],'other_editions':chosen.get('other_editions',[])},refs,ranking


def make_profile(case,facts,evaluation,reference,refs,template,source_facts,source_hashes):
    required=('item_maxima','item_total_weights','allocations','point_goal_weights','point_course_weights','assumptions')
    if any(key not in evaluation for key in required):raise ValueError('来源设计evaluation缺少配分或适用假设')
    if not evaluation['assumptions']:raise ValueError('来源设计必须说明评价设计的适用范围')
    active={i['id'] for i in facts['items'] if not i.get('summary_only')}
    if set(evaluation['item_total_weights'])!=active or set(evaluation['allocations'])!=active:
        raise ValueError('配分须恰好覆盖非汇总评分项，不得重复计分')
    if set(evaluation['item_maxima'])!={i['id'] for i in facts['items']}:
        raise ValueError('满分必须覆盖全部真实评分项')
    points={p['id']:p for p in facts['points']}
    if set(evaluation['point_goal_weights'])!=set(points) or set(evaluation['point_course_weights'])!=set(points):
        raise ValueError('观测点评价设计集合不完整')
    threshold=reference['threshold_evidence']['value']
    if evaluation.get('threshold') is not None and number(evaluation['threshold'])!=number(threshold):
        raise ValueError('阈值与所选参考出处不一致')
    profile={'version':'course_evaluation_profile_v2','model':'source_item_allocation_proxy_v1','status':'proposed',
        'id':case['id']+'_source_design_'+signature(evaluation)[:10],
        'case_id':case['id'],'group_id':case.get('group_id',case['id']),'course_name':case['course_name'],
        'term':facts['metadata'].get('term'),'source_hashes':source_hashes,'source_facts_sha256':digest(source_facts),
        'template_id':template['contract']['template_id'],'reference_library_sha256':refs['sha256'],
        'reference':reference,'threshold':threshold,'goal_ids':[g['id'] for g in facts['goals']],
        **{key:copy.deepcopy(evaluation[key]) for key in required},
        'approval':{'approved':False,'reviewer':''},
        'design_record':{'source':'explicit_current_material_design','evidence':sorted({r for p in facts['points']+facts['goals']+facts['items'] for r in p['source_refs']}),
            'approved':False,'scope':'来源不足的评价关系为本次设计；不宣称已有学生目标题项成绩。'}}
    for key in ('point_weight_reference','allocation_evidence','policy_evidence','design_scope'):
        if key in evaluation:profile[key]=copy.deepcopy(evaluation[key])
    validate_profile(profile,case,template,refs,source_hashes)
    return profile


def compare_official_totals(grades,facts,profile,policy,comparison=None):
    """只审计分项设计与正式总评差异，不改分、不要求二者同一口径。"""
    comparison=comparison or {}
    if comparison.get('official_total_policy','preserve_registered')!='preserve_registered':
        raise ValueError('必须保留正式登记总评，不允许回推替换')
    digits=comparison.get('round_digits')
    if digits is not None and (not isinstance(digits,int) or isinstance(digits,bool) or not 0<=digits<=6):
        raise ValueError('总评对比round_digits须为0至6的整数或null')
    total_max=number(facts['metadata']['total_max']);rows=[];skipped=[]
    for student in grades['students']:
        total,reason=status_score(student,'total',policy)
        if reason:skipped.append({'student_id':student['id'],'reason':reason});continue
        subtotal=Decimal('0');missing=[]
        for iid,weight in profile['item_total_weights'].items():
            score,why=status_score(student,iid,policy)
            if why:missing.append({'item_id':iid,'reason':why});continue
            maximum=number(profile['item_maxima'][iid])
            if not 0<=score<=maximum:raise ValueError('原始评分超出设计满分：'+student['id']+'/'+iid)
            subtotal+=Decimal(str(score))/Decimal(str(maximum))*Decimal(str(weight))*Decimal(str(total_max))
        if missing:skipped.append({'student_id':student['id'],'reason':'分项缺失或状态排除','items':missing});continue
        rounded=subtotal if digits is None else subtotal.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
        delta=float(rounded-Decimal(str(total)))
        rows.append({'student_id':student['id'],'registered_total':total,'designed_component_total':float(subtotal),
            'comparison_total':float(rounded),'delta':delta,'matches':math.isclose(delta,0,abs_tol=1e-8),
            'source_ref':student['refs'].get('total')})
    return {'status':'audited_preserving_registered_totals','policy':'preserve_registered',
        'comparison_round_digits':digits,'compared_count':len(rows),'matched_count':sum(r['matches'] for r in rows),
        'mismatch_count':sum(not r['matches'] for r in rows),'max_abs_difference':max((abs(r['delta']) for r in rows),default=None),
        'rows':rows,'skipped':skipped,'scope':'正式总评用于成绩统计；设计分项用于本次课程目标评价。两者差异保留，不拟合消除、不替换原值。'}


def prepare(case_file,runtime_file,design_file,out):
    case_file=path(case_file);runtime_file=path(runtime_file);design_file=path(design_file);out=path(out)
    if out.exists():raise ValueError('来源设计输出已存在，使用新目录')
    case=copy.deepcopy(load(case_file));runtime=load(runtime_file);design=load(design_file)
    if design.get('version')!=VERSION or design.get('case_id')!=case['id']:raise ValueError('来源设计版本或课程ID不匹配')
    hashes={s['id']:digest(path(s['path'])) for s in case['sources']}
    if design.get('source_hashes')!=hashes:raise ValueError('来源设计与当前文件SHA不匹配')
    preflight=case_preflight(case)
    if preflight['status']!='passed':raise ValueError(preflight['status'])
    sources=ingest(case);facts=source_records(design,sources,case['course_name'])
    mappings=copy.deepcopy(design.get('mappings',design.get('grade_tables',[])))
    if not mappings:raise ValueError('来源设计必须给出已核对的评分列映射')
    source_lookup={s['id']:s for s in sources}
    for mapping in mappings:
        source=source_lookup.get(mapping.get('source_id'))
        if not source or source.get('role')!='current' or source.get('kind')!='grades':raise ValueError('评分映射必须来自本轮成绩文件')
        names=identities(source);override=case.get('grade_mapping_policy',{}).get('identity_overrides',{}).get(source['id'],{})
        confirmed=bool(override.get('confirmation') and source.get('sha256') and override.get('sha256')==source['sha256'])
        if (not names or any(label(n)!=label(case['course_name']) for n in names)) and not confirmed:
            raise ValueError('评分来源课程身份不匹配且缺少文件绑定确认')
        sheet=next((s for s in source['sheets'] if s['name']==mapping.get('sheet')),None)
        if sheet is None:raise ValueError('评分映射工作表不存在')
        width=max(map(len,sheet['rows']),default=0)
        cols=[mapping.get(k) for k in ('student_id','name','total','status','class_name') if mapping.get(k) is not None]+list(mapping.get('items',{}).values())
        if any(not isinstance(c,int) or isinstance(c,bool) or c<1 or c>width for c in cols):raise ValueError('评分映射列号超出工作表')
    case['grade_tables']=mappings
    # 明确的来源设计使用已核对的映射，不让自动适配器覆盖。
    case['grade_mapping_policy']={'mode':'explicit_source_design_v1','origin':str(design_file)}
    case['decision_policy']={'mode':'source_design_bound_v1'}
    case['grade_formula_inference']=False
    case['score_formula_policy']={'enabled':True,'random_generated_components':'exclude','dependent_components':'exclude','preserve_original_cached_values':True}
    case['evaluation_design']={'enabled':False,'reason':'正式总评直接保留；来源设计分项评价不进行相等拟合'}
    if design.get('evaluation_policy') is not None:case['evaluation_policy']=copy.deepcopy(design['evaluation_policy'])
    policy=case.get('evaluation_policy',{})
    if not policy.get('source_refs'):raise ValueError('评价政策必须绑定本轮依据，不能猜测缺考或缓考处理')
    index=evidence_index(sources)
    if any(r not in index or index[r]['role']!='current' for r in policy['source_refs']):raise ValueError('评价政策引用不属于本轮来源')
    active={i['id'] for i in facts['items'] if not i.get('summary_only')}
    if not active<={k for m in mappings for k in m.get('items',{})}:raise ValueError('实际映射缺少设计所需评分项')
    grades=parse_grades(case,sources,facts)
    reference,refs,ranking=select_reference(runtime,facts,design.get('reference'))
    template=load_template(runtime,case.get('report_type','course'))
    evaluation=design.get('evaluation',{})
    # 生成校验所需暂存facts，最终目录始终为全新输出，不污染原材料。
    out.mkdir(parents=True);source_facts=out/'facts.source_draft.json'
    save(source_facts,facts)
    case['facts_path']=str(source_facts)
    try:
        from .score_formulas import inventory,enforce
        formula_report=inventory(case,sources,out);enforce(mappings,formula_report)
        profile=make_profile(case,facts,evaluation,reference,refs,template,source_facts,hashes)
        view=compile_plan(profile,case,facts,None)
        calc=calculate(case,view,grades)
        if not calc['total']['valid_count']:raise ValueError('没有政策允许的有效正式总评')
        audit=compare_official_totals(grades,facts,profile,policy,design.get('comparison'))
        profile_file=out/'evaluation_profile.proposed.json'
        save(profile_file,profile)
        case['evaluation_profile']=str(profile_file);case['allow_estimates']=True
        case['compact_material_preparation']={'version':VERSION,'design_file':str(design_file),
            'design_sha256':digest(design_file),'profile_file':str(profile_file),
            'required_run_flags':['--evaluation-profile',str(profile_file),'--allow-estimates'],
            'official_total_policy':'preserve_registered'}
        save(out/'case.json',case);save(out/'normalized_grades.preview.json',grades)
        save(out/'source_inventory.json',sources);save(out/'reference_ranking.json',ranking)
        save(out/'grade_preflight.json',preflight);save(out/'registered_component_comparison.json',audit)
        save(out/'preparation_validation.json',{'status':'passed','registered':grades['registered_count'],
            'valid_totals':calc['total']['valid_count'],'goals':len(facts['goals']),'points':len(facts['points']),
            'source_hashes':hashes,'source_facts_sha256':digest(source_facts),'design_sha256':digest(design_file),
            'profile_sha256':digest(profile_file),'official_totals_unchanged':True,
            'designed_component_mismatches':audit['mismatch_count'],'model_requested':False,'human_approval':False,
            'scope':'显式来源绑定设计的准备验证，不是正式毕业要求矩阵或模型全包抽取盲测。'})
    except Exception as exc:
        save(out/'preparation_validation.json',{'status':'failed','reason':str(exc),'model_requested':False})
        raise
    return out/'case.json'
