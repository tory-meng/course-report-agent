"""从完整课程包生成绑定输入：解析旧版大纲、成绩层级和来源勾选表，不手写事实JSON。"""
import copy
from collections import Counter
from datetime import datetime
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from .common import ROOT,load,save,digest
from .readers import ingest,evidence_index
from .facts import LISTS,validate_facts
from .extraction_quality import formal_goal_records,cells
from .grade_selection import select,identities,item_label
from .grade_layout import layouts,is_student_id
from .grades import parse_grades
from .course_design import infer_grade_scheme
from .checkbox_allocations import find,derive,verify
from .score_formulas import inventory,enforce
from .reference_plans import rank_references
from .reference_db import reference_identity,connect
from .pipeline import load_template


def clean_activity(text):
    return re.sub(r'^\s*\d+[.．、]\s*','',text).strip()


def methods_from_outline(outline,course_name='',goal_text='',scope_audit=None):
    """抽取教学活动的代表性原句；不把所有活动自动关联全部目标。"""
    active=False;groups={}
    categories=[('自主学习',r'自主学习|课外学习|线上资源|资料检索'),('小组协作',r'小组|合作学习|团队'),
                ('课堂讨论',r'讨论|研讨'),('案例分析',r'案例分析'),('课堂讲授',r'讲授|讲解'),
                ('汇报与答辩',r'汇报|展示|答辩'),('实验与实践',r'实验|实践|操作|配置')]
    for b in outline['blocks']:
        t=b['text'].strip()
        if '【教学方法与学习活动】' in t:active=True;continue
        if t.startswith('【') or re.match(r'第.+单元|五、课程考核',t):active=False
        if not active or not t:continue
        body=clean_activity(t)
        # 仅排除明确跨学科的模板残留；同类词已出现在本课目标时保留。
        graphics=('坐标系变换','透视投影','计算机图形学','光照模型','曲面造型')
        foreign=[term for term in graphics if term in body and term not in goal_text]
        if '网络' in course_name and not any(w in course_name for w in ('图形','图像','视觉')) and foreign:
            if scope_audit is not None:scope_audit.append({'source_ref':b['id'],'original_text':b['text'],'excluded_terms':foreign,'reason':'网络课程目标中没有相应图形学主题，属于明显跨课程活动示例；原件保留。'})
            continue
        for name,pattern in categories:
            if re.search(pattern,body):
                group=groups.setdefault(name,[])
                if all(clean_activity(prior['text'])!=body for prior in group):group.append(b)
    result=[]
    for n,(name,rows) in enumerate(groups.items(),1):
        chosen=[rows[0]]
        if len(rows)>1 and len(clean_activity(rows[0]['text']))+len(clean_activity(rows[-1]['text']))<260:chosen.append(rows[-1])
        result.append({'id':'M'+str(n),'name':name,'description':'\n'.join(clean_activity(b['text']) for b in chosen),
                       'source_refs':[b['id'] for b in chosen],'quotes':{b['id']:b['text'] for b in chosen}})
    if not any(m['name']=='实验与实践' for m in result):
        lab=[b for b in outline['blocks'] if not b.get('table_id') and re.search(r'实验过程满分|实验结果的现场展示|学生完成.+实验并撰写实验报告',b['text'])]
        if lab:
            topic='图像算法' if any('灰度' in b['text'] for b in outline['blocks']) else '网络应用' if any('网络' in b['text'] for b in outline['blocks'][:10]) else '课程项目'
            result.append({'id':'M'+str(len(result)+1),'name':'实验与实践','status':'proposed_teaching_design',
                'description':'将结合本课实验考核要求组织'+topic+'练习、结果展示与报告讲评，指导学生说明实验方法、分析结果并记录任务分工。',
                'source_refs':[b['id'] for b in lab[:2]],'quotes':{b['id']:b['text'] for b in lab[:2]},
                'design_basis':'current_course_experiment_assessment_requirements_not_historical_execution_claim'})
    if not result:raise ValueError('没有足够的本课教学活动')
    return result


def primary_identity(case,sources):
    grade=next(s for s in sources if s['kind']=='grades')
    text='\n'.join(b['text'] for b in grade['blocks'][:5])
    m=re.search(r'(20\d{2}-20\d{2}-[12])',text)
    if not m:raise ValueError('正式成绩学期不明确')
    seed={'metadata':{'term':m[1]},'items':[]}
    mappings,selection=select(case,sources,seed)
    source=next(s for s in sources if s['id']==mappings[0]['source_id'])
    return source,m[1],mappings,selection


def point_reference(db,ranking):
    """仅取已筛选报告中的数值参数示例，绝不把H/M/L转换成数值。"""
    with connect(db,True) as con:
        for doc in ranking:
            rows=con.execute('SELECT c.id,c.locator,c.text FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.relpath=? ORDER BY c.id',(doc['file'],)).fetchall()
            candidates=[]
            for b in rows:
                lines=[cells({'text':line}) for line in b['text'].splitlines() if '|' in line]
                header=next((row for row in lines if any('课程支撑权重' in c for c in row) and any('贡献' in c for c in row)),None)
                if not header:continue
                col=next((i for i,c in enumerate(header) if c.strip()=='课程支撑权重'),None)
                if col is None:continue
                for row in lines:
                    if len(row)<=col:continue
                    value=row[col].replace('％','%').strip()
                    if re.fullmatch(r'0?\.\d+|\d+(?:\.\d+)?%',value):
                        v=float(value.rstrip('%'))/(100 if value.endswith('%') else 1)
                        if 0<v<=1:candidates.append({'value':v,'file':doc['file'],'sha256':doc['sha256'],'reference_id':b['id'],'locator':b['locator'],'quote':' | '.join(row)})
            if not candidates:
                for b in rows:
                    if '课程支撑权重' not in b['text'] or '贡献' not in b['text']:continue
                    for line in b['text'].splitlines():
                        m=re.fullmatch(r'\s*(\d+[._-]\d+)\s+课程目标\s*\d+\s+\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?\s+(\d+(?:\.\d+)?)%\s+\d+(?:\.\d+)?\s*',line)
                        if m:candidates.append({'value':float(m[2])/100,'file':doc['file'],'sha256':doc['sha256'],'reference_id':b['id'],'locator':b['locator'],'quote':line.strip()})
            if candidates:
                values=sorted({c['value'] for c in candidates});selected=values[len(values)//2]
                return {**next(c for c in candidates if c['value']==selected),'selection':'selected_observed_middle_parameter_example_not_official_current_course_weight'}
    raise ValueError('参考库中没有找到可定位的数值课程支撑权重，不从H/M/L猜数值')


def source_columns(sources,selection,formula_report):
    allowed={c['source_id'] for c in selection['candidate_tables']};columns=[]
    bad={(r['source_id'],r['sheet']):{re.match(r'([A-Z]+)',cell)[1] for cell in r['unsafe_score_cells']} for r in formula_report['sheets']}
    import openpyxl
    for source in sources:
        if source['id'] not in allowed:continue
        for sheet in source.get('sheets',[]):
            for layout in layouts(sheet):
                for col in layout['columns']:
                    if openpyxl.utils.get_column_letter(col['column']) in bad.get((source['id'],sheet['name']),set()):continue
                    if col['role'] in ('student_id','name','total','status'):continue
                    rows=[r for r in sheet['rows'][layout['header_row']:] if len(r)>=layout['student_id'] and is_student_id(r[layout['student_id']-1])]
                    if not rows or any(len(r)<col['column'] or not str(r[col['column']-1]).strip() for r in rows):continue
                    try:
                        if any(not 0<=float(r[col['column']-1])<=100 for r in rows):continue
                    except (TypeError,ValueError):continue
                    columns.append({'name':col['label'],'source_id':source['id'],'sheet':sheet['name'],'column':col['column'],
                                    'header_ref':next(b['id'] for b in source['blocks'] if b.get('row')==layout['header_start'] and b.get('table_id')==source['id']+':'+sheet['name'])})
    return columns


def normalize_item(name):
    aliases={'课堂回答问题及讨论':'课堂讨论','出勤及课堂纪律':'出勤及课堂纪律','出席':'出勤及课堂纪律'}
    return item_label(aliases.get(name,name))


def prepare(case_path,runtime_path,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    case=copy.deepcopy(load(Path(case_path)));runtime=load(Path(runtime_path))
    from .grade_preflight import case_preflight
    preflight=case_preflight(case);save(out/'grade_preflight.json',preflight)
    if preflight['status']!='passed':raise ValueError(preflight['status'])
    original_sources=case['sources'];case['sources']=[s for s in original_sources if s['kind']=='grades' or Path(s['path']).suffix.lower() in ('.doc','.docx') and re.search(r'大纲|教案|教学日历|考核方案|评定标准',Path(s['path']).name)]
    sources=ingest(case);save(out/'source_inventory.json',sources);index=evidence_index(sources)
    candidates=[]
    for s in sources:
        records,_=formal_goal_records(s['blocks'])
        if records:candidates.append((s,records))
    if len(candidates)!=1:raise ValueError('正式目标矩阵候选不唯一或缺失')
    outline,records=candidates[0]
    def record(value,refs):
        refs=list(dict.fromkeys(refs));return {**value,'source_refs':refs,'quotes':{r:index[r]['text'] for r in refs}}
    facts={k:[] for k in LISTS};facts.update(review={'approved':False,'reviewer':''},conflicts=[],preparation={'origin':'automatic_legacy_material_adapter','human_facts_file_supplied':False,'original_case':str(Path(case_path).resolve())})
    for r in records:facts[r['kind']].append(record(r['value'],r['evidence_ids']))
    gids=[g['id'] for g in facts['goals']];header,checkbox_rows=find(outline['blocks'],gids)
    primary,term,_,initial_selection=primary_identity(case,sources)
    head=primary['blocks'][:5];headtext='\n'.join(b['text'] for b in head)
    def actual(regex,text,label):
        m=re.search(regex,text)
        if not m:raise ValueError('缺少可定位的'+label)
        return m[1].strip()
    code=actual(r'课程代码[：:]\s*(CPU[A-Za-z0-9]+)',headtext,'课程代码')
    teacher=actual(r'任课教师[：:]\s*([^|\n]+)',headtext,'教师');college=actual(r'开课学院[：:]\s*([^|\n]+)',headtext,'学院')
    outline_text='\n'.join(b['text'] for b in outline['blocks'])
    hours=float(actual(r'(\d+(?:\.\d+)?)\s*学时',outline_text,'学时'))
    credits=float(actual(r'学分[：:]\s*(\d+(?:\.\d+)?)',headtext,'学分'))
    category=actual(r'课程类别[：:]\s*([^\n]+)',outline_text,'课程类别')
    major=actual(r'适用专业[：:]\s*([^\n]+)',outline_text,'适用专业')
    classes=sorted(set(re.findall(r'行政班[：:]\s*([^|\n]+)', '\n'.join(b['text'] for b in primary['blocks']))))
    if not classes:raise ValueError('缺少正式登记的行政班信息')
    grades=sorted(set('20'+m[1] for c in classes if (m:=re.search(r'(\d{2})\d{2}[A-Za-z]?$',c))))
    if not grades:raise ValueError('正式班级年级无法定位')
    meta_refs=[b['id'] for b in head]+[b['id'] for b in outline['blocks'][:10]]+[header['id']]+[b['id'] for b in checkbox_rows]
    facts['metadata']=record({'course_name':case['course_name'],'course_code':code,'teacher':teacher,'college':college,'major':major,'class_name':'、'.join(classes),'grade':'、'.join(grades),
        'term':term,'academic_year':term[:9],'semester':term[-1],'hours':hours,'credits':credits,'course_category':category,
        'course_nature':'必修' if '必修' in category else '选修' if '选修' in category else category,
        'total_max':100,'report_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()},meta_refs)
    activity_scope=[]
    facts['methods']=methods_from_outline(outline,case['course_name'],' '.join(g['text'] for g in facts['goals']),activity_scope)
    save(out/'teaching_activity_scope.json',{'excluded_examples':activity_scope,'scope':'只筛选本次教学示例，不修改原大纲；其余活动仍由后续模型语义核查。'})
    case['score_formula_policy']={'enabled':True,'random_generated_components':'exclude','dependent_components':'exclude','preserve_original_cached_values':True}
    formula_report=inventory(case,sources,out)
    rows_by_parent={}
    for b in checkbox_rows:rows_by_parent.setdefault(cells({'text':b['text']})[0],[]).append(b)
    if len(rows_by_parent)!=2:raise ValueError('本适配器要求完整的过程与期末两层总评；不硬套其他结构')
    definitions=[('A_usual','平时成绩','I_usual',next((k for k in rows_by_parent if '过程' in k),None)),
                 ('A_final','期末成绩','I_final',next((k for k in rows_by_parent if '终结' in k),None))]
    if any(parent is None for _,_,_,parent in definitions):raise ValueError('考核层级无法对应')
    # 正式成绩单父项必须可定位。明细只有覆盖整组且无随机依赖时才启用，不混合汇总与部分明细。
    grade_layout=next(l for sh in primary['sheets'] for l in layouts(sh))
    grade_header=next(b['id'] for b in primary['blocks'] if b.get('row')==grade_layout['header_start'])
    known_columns=source_columns(sources,initial_selection,formula_report)
    binding={'header_ref':header['id'],'row_refs':[b['id'] for b in checkbox_rows],'item_rows':{}};item_counter=0
    for aid,name,iid,parent in definitions:
        facts['assessments'].append(record({'id':aid,'name':name,'raw_item_id':iid},[grade_header]))
        parts=rows_by_parent[parent];matches=[]
        for row in parts:
            source_name=cells({'text':row['text']})[1]
            found=[c for c in known_columns if normalize_item(c['name'])==normalize_item(source_name) and normalize_item(c['name'])!=normalize_item(name)]
            matches.append(found[0] if len(found)==1 else None)
        detailed=len(parts)>1 and all(matches)
        facts['items'].append(record({'id':iid,'name':name,'assessment_id':aid,'max_score':100,'score_basis':'raw',**({'summary_only':True} if detailed else {})},[grade_header]))
        if detailed:
            for row,col in zip(parts,matches):
                item_counter+=1;child='I_component'+str(item_counter)
                facts['items'].append(record({'id':child,'name':col['name'],'assessment_id':aid,'max_score':100,'score_basis':'raw'},[col['header_ref'],row['id']]))
                binding['item_rows'][child]=[row['id']]
        else:binding['item_rows'][iid]=[r['id'] for r in parts]
    weights,allocations,allocation_evidence=derive(binding,facts['metadata']['quotes'],gids)
    for a in facts['assessments']:a['total_weight']=sum(weights[i['id']] for i in facts['items'] if not i.get('summary_only') and i['assessment_id']==a['id'])
    mappings,selection=select(case,sources,facts);case['grade_tables']=mappings;enforce(mappings,formula_report)
    case['evaluation_policy']['source_refs']=[grade_header]
    parsed=parse_grades(case,sources,facts)
    required={i['id'] for i in facts['items']}
    if any(s['total'] is not None and s['status']=='normal' and any(s['scores'].get(i) is None for i in required) for s in parsed['students']):raise ValueError('正式评分学生缺少本轮必要分项')
    scheme=infer_grade_scheme(case,facts,parsed,sources,out)
    for a,w in zip(facts['assessments'],scheme['selected']['weights']):
        if abs(a['total_weight']-w)>1e-10:raise ValueError('正式总评无法按本课考核方案解释，不强行生成')
    active={i['id']:i for i in facts['items'] if not i.get('summary_only')}
    for proof in scheme['component_formula_evidence']:
        aid=proof['assessment_id'];children={i:v for i,v in active.items() if v['assessment_id']==aid};selected=proof['within_parent_weights']
        if set(children)==set(selected):
            total=sum(weights[i] for i in children)
            if any(abs(weights[i]/total-selected[i])>1e-10 for i in children):raise ValueError('真实分项公式与大纲占比冲突，暂缓该包')
    facts['data_quality_issues']=['只使用正式成绩与完整、无随机依赖的明细。目标内配分依据本课勾选关系设计；未登记的目标子成绩不补造。']
    if any(r['unsafe_score_cells'] for r in formula_report['sheets']):facts['data_quality_issues'].append('部分辅助分项含随机生成或其依赖公式，已排除；本次采用正式登记的平时、期末与总评。')
    source_facts=out/'facts.source_draft.json';save(source_facts,facts)
    refs=reference_identity(runtime)
    reference_assessments=[a['name'] for a in facts['assessments']]
    if '项目' in case['course_name']:reference_assessments=['平时考核','设计报告','验收答辩']
    elif any('闭卷考试' in b['text'] for b in checkbox_rows):reference_assessments=['平时成绩','期末考试','测验','作业']
    else:reference_assessments+=['实验报告','期末报告']
    ranked=rank_references(Path(refs['db']),reference_assessments,[g['text'] for g in facts['goals']])
    chosen=next(r for r in ranked if r['threshold_candidates']);threshold=chosen['threshold_candidates'][0];point_ref=point_reference(Path(refs['db']),ranked)
    profile={'version':'course_evaluation_profile_v2','model':'source_item_allocation_proxy_v1','status':'proposed','id':case['id']+'_checkbox_v1',
        'case_id':case['id'],'group_id':case['group_id'],'course_name':case['course_name'],'term':term,
        'source_hashes':{s['id']:digest(Path(s['path'])) for s in case['sources']},'source_facts_sha256':digest(source_facts),
        'template_id':load_template(runtime,'course')['contract']['template_id'],'reference_library_sha256':refs['sha256'],
        'reference':{'source_path':str(ROOT/'inputs/references'/chosen['file']),'file':chosen['file'],'sha256':chosen['sha256'],'threshold_evidence':threshold},
        'threshold':threshold['value'],'goal_ids':gids,'item_maxima':{i['id']:100 for i in facts['items']},'item_total_weights':weights,'allocations':allocations,
        'source_checkbox':binding,'point_goal_weights':{p['id']:{g:1/len(p['goal_weights']) for g in p['goal_weights']} for p in facts['points']},
        'point_course_weights':{p['id']:p.get('course_weight',point_ref['value']) for p in facts['points']},'point_weight_reference':point_ref,
        'assumptions':['总评组成保留本课大纲与正式成绩全样本核算；不以未取整核算值替换正式总评。',
            '勾选仅表示支撑关系；同项在勾选目标间均分、同指标点在关联目标间等权均值是本次明确声明的设计。',
            '来源表的组内百分比按该组小计归一化；33.3%近似值及单一终结项的60%标注保留在审计，避免重复乘总评权重。',
            '达成阈值及缺少正式数值的课程支撑权重参考筛选后的库内数值示例，是候选参数，不将H/M/L直接映射为数字。'],
        'approval':{'approved':False,'reviewer':''}}
    verify(profile,facts);validate_facts(facts,sources,case['course_name'])
    case.update(facts_path=str(source_facts),grade_formula_inference=False,
        teaching_reference_policy={'enabled':True,'propose_goal_links':True,'repair_coverage':True},
        evaluation_design={'enabled':True,'auto_apply':True,'profile':str(out/'evaluation_profile.proposed.json'),'goal_links':'preserve_checkbox_support'})
    case['material_intake'].update(phase='automatically_prepared',preparation_origin='legacy_outline_and_register_adapter')
    save(out/'case.json',case);save(out/'evaluation_profile.proposed.json',profile);save(out/'grade_selection.preparation.json',selection);save(out/'reference_ranking.json',ranked);save(out/'checkbox_allocation_evidence.json',allocation_evidence)
    save(out/'source_selection.json',{'included':case['sources'],'excluded':[s for s in original_sources if s not in case['sources']],
        'unsafe_score_columns_excluded':[{k:r[k] for k in ('source_id','sheet','unsafe_score_cells')} for r in formula_report['sheets'] if r['unsafe_score_cells']],
        'scope':'确定性自动准备，不是人工批准或模型独立全包抽取的盲测。'})
    save(out/'preparation_validation.json',{'status':'passed','registered':len(parsed['students']),'valid_totals':len([s for s in parsed['students'] if s['total'] is not None]),'goals':len(gids),'points':len(facts['points']),
        'active_items':len(active),'source_facts_sha256':digest(source_facts),'source_checkbox_verified':True,'model_requested':False,'human_approval':False})
    return out/'case.json'
