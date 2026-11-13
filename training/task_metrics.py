"""候选任务专项诊断；语义答案尚待审核，不把参考答案一致率当准确率。"""
import json
import re
from report_agent.structured import validate_schema

METRICS_REVISION='input_constraints_v3'

def _source_terms(source):
    content=' '.join(str(v) for t in source.get('tables',[]) for r in t.get('header_rows',[]) for v in r['cells'])
    terms=set(re.findall(r'20\d{2}-20\d{2}-[12](?!\d)',content))
    for m in re.finditer(r'(20\d{2})\s*[—－-]\s*(20\d{2})\s*学年\s*第\s*([12一二])\s*学期',content):
        terms.add(m[1]+'-'+m[2]+'-'+{'一':'1','二':'2'}.get(m[3],m[3]))
    return terms

def _identity_issue(data,source):
    from report_agent.grade_selection import identities
    from report_agent.grade_layout import label
    if source.get('role')!='current' or source.get('kind')!='grades':return '非本轮成绩来源'
    shim={'sheets':[{'rows':[r['cells'] for r in t.get('header_rows',[])]} for t in source.get('tables',[])]}
    names=identities(shim);wanted=label(data.get('course_name',''))
    override=data.get('confirmed_identity_overrides',{}).get(source['source_id'],{})
    source_hash=source.get('sha256')
    confirmed=bool(override.get('confirmation') and isinstance(source_hash,str)
                   and re.fullmatch(r'[0-9a-fA-F]{64}',source_hash) and override.get('sha256')==source_hash)
    if (not names or any(label(n)!=wanted for n in names)) and not confirmed:return '课程身份不符或缺少文件哈希绑定确认'
    terms=_source_terms(source)
    if data.get('term') and terms and terms!={data['term']}:return '成绩学期与当前任务不符'
    return None

def _column_labels(table,column):
    from report_agent.grade_layout import ALIASES,label
    heads=table.get('header_rows',[]);start=next((i for i,r in enumerate(heads) if any(label(v) in ALIASES['student_id'] for v in r['cells'])),None)
    if start is None:return []
    return [str(r['cells'][column-1]).strip() for r in heads[start:] if column<=len(r['cells']) and str(r['cells'][column-1]).strip()]

def _unsafe_columns(data,source_id,sheet):
    cols=set()
    for risk in data.get('formula_risks',[]):
        if risk['source_id']!=source_id or risk['sheet']!=sheet:continue
        for cell in risk.get('unsafe_score_cells',[]):
            match=re.match(r'([A-Z]+)',cell)
            if not match:continue
            n=0
            for c in match[1]:n=n*26+ord(c)-ord('A')+1
            cols.add(n)
    return cols

def _usable_total(data,source,table):
    from report_agent.grade_layout import ALIASES,label
    if _identity_issue(data,source) or table.get('numeric_total_count',0)<=0:return False
    width=max((len(r['cells']) for r in table.get('header_rows',[])),default=0)
    for col in range(1,width+1):
        labels=_column_labels(table,col)
        total=any(label(v) in ALIASES['total'] for v in labels)
        generic=bool(labels and label(labels[-1])=='成绩' and re.search(r'记分册|成绩登记|成绩单',source.get('file_name','')))
        if (total or generic) and col not in _unsafe_columns(data,source['source_id'],table['sheet']):return True
    return False

def _grade_constraints(data,actual):
    from report_agent.grade_layout import ALIASES,label
    issues=[];sources={s['source_id']:s for s in data['sources']}
    def check_cell(cell,total=False):
        if not isinstance(cell,dict):issues.append('选中状态缺少有效总评坐标');return
        sid=cell['source_id'];source=sources.get(sid)
        if source is None:issues.append('使用未知成绩来源');return
        problem=_identity_issue(data,source)
        if problem:issues.append(problem+'：'+sid)
        table=next((t for t in source.get('tables',[]) if t['sheet']==cell['sheet']),None)
        if table is None:issues.append('使用不存在的成绩工作表');return
        col=cell['column'];width=max((len(r['cells']) for r in table.get('header_rows',[])),default=0)
        if not isinstance(col,int) or isinstance(col,bool) or col<1 or col>width:issues.append('成绩列号超出实际表头范围');return
        labels=_column_labels(table,col)
        if not labels:issues.append('评分列没有实际表头依据')
        if any(label(v) in ALIASES['student_id']+ALIASES['name']+ALIASES['status'] for v in labels):issues.append('把身份或状态列当作评分列')
        if total:
            generic=bool(labels and label(labels[-1])=='成绩' and re.search(r'记分册|成绩登记|成绩单',source.get('file_name','')))
            if labels and not generic and not any(label(v) in ALIASES['total'] for v in labels):issues.append('总评列的表头不是总评口径')
            if table.get('numeric_total_count',0)<=0:issues.append('从空白成绩表选取总评')
        if col in _unsafe_columns(data,sid,cell['sheet']):issues.append('使用随机公式或其依赖评分列')
        if sid in actual['excluded_sources']:issues.append('同一来源同时被排除和用于评分')
    if actual['status']=='selected':check_cell(actual['official_total'],True)
    elif actual['official_total'] is not None or actual['components']:issues.append('停止状态仍使用成绩')
    if actual['status'].startswith('blocked'):
        eligible=[s for s in sources.values() if any(_usable_total(data,s,t) for t in s.get('tables',[]))]
        if eligible:issues.append('误拒绝已有同课有效总评的材料')
    item_ids=[c['item_id'] for c in actual['components']]
    if len(item_ids)!=len(set(item_ids)):issues.append('同一评分项重复映射')
    for cell in actual['components']:check_cell(cell)
    for entry in actual['roster_only']:
        source=sources.get(entry['source_id']);table=next((t for t in (source or {}).get('tables',[]) if t['sheet']==entry['sheet']),None)
        if source is None or table is None:issues.append('名册指向不存在的来源或工作表')
        elif _identity_issue(data,source):issues.append('名册来源缺少本课身份依据')
    if len(actual['excluded_sources'])!=len(set(actual['excluded_sources'])):issues.append('排除来源重复')
    return list(dict.fromkeys(issues))

def _activity_constraints(data,actual,kind):
    from report_agent.teaching_reference import bind_proposal_links
    if kind=='teaching_design':
        methods=data['methods'];goals=data['goals'];links=actual['links']
    else:
        methods=data['available_methods'];goals=data['uncovered_goals']
        links=[{'method_id':m['id'],'goal_ids':[r['goal_id'] for r in actual['repairs'] if m['id'] in r['method_ids']],
                'reason':'核对所选已有活动与目标的支撑条件。'} for m in methods]
    # 完整来源存在时直接复用生产校验；缺少该上下文的最小诊断只做ID/重复检查。
    if not methods or not goals:return ['缺少活动或目标上下文']
    if all(r.get('source_refs') for r in methods+goals):
        try:bound=bind_proposal_links({'links':links},methods,goals)
        except ValueError as exc:return [str(exc)]
        rejected=[r['reason'] for link in bound for r in link.get('rejected_proposals',[])]
        return list(dict.fromkeys(rejected))
    mids=[r['method_id'] for r in links]
    if len(mids)!=len(set(mids)) or set(mids)!={m['id'] for m in methods}:return ['教学方式行缺失或重复']
    return []

def _past_effect_claim(text):
    # “检验是否已经掌握”提出待检验的问题，不是宣称已取得成效。
    assertion=re.sub(r'(?:是否|能否)\s*(?:已经|已)(?:掌握|达到|完成|提高|提升|取得|改善|解决)[^，。；！？]*','',text)
    return bool(re.search(r'已经(?:掌握|达到|完成|提高|提升|取得|改善|解决)|已取得|已提升|已提高|明显改善|显著提升|取得了',assertion))

def diagnose(row,actual):
    issues=[];metrics={'metrics_revision':METRICS_REVISION}
    try:validate_schema(actual,row['output_schema'])
    except ValueError as e:return [str(e)],{'semantic_quality':'not_established'}
    kind=row['task_kind']
    metrics['reference_approved']=bool(row.get('review',{}).get('approved'))
    metrics['reference_review_kind']=row.get('review',{}).get('kind','human_review' if metrics['reference_approved'] else 'unreviewed')
    metrics['semantic_quality']='not_established_by_structural_checks'
    if kind not in ('write_section','grade_source_selection','teaching_design','teaching_design_gap_review'):
        metrics['task_constraint_scope']='schema_only_for_legacy_task'
        return [],metrics
    expected=json.loads(row['messages'][-1]['content']);data=json.loads(row['messages'][-2]['content'])
    if not isinstance(data,dict):raise ValueError('专项任务输入须为JSON对象')
    if kind=='teaching_design_gap_review':
        allowed={m['id'] for m in data['available_methods']};targets={g['id'] for g in data['uncovered_goals']}
        rows=actual['repairs']
        if len(rows)!=len(targets) or {r['goal_id'] for r in rows}!=targets:issues.append('遗漏或重复目标')
        if any(len(r['method_ids'])>2 or len(r['method_ids'])!=len(set(r['method_ids'])) or not set(r['method_ids'])<=allowed for r in rows):issues.append('活动选择数量或ID不合格')
        if any(not r['reason'].strip() for r in rows):issues.append('关联理由为空')
        if any(len(r['reason'])>=95 and not r['reason'].endswith(('。','！','？','；')) for r in rows):issues.append('关联理由疑似截断，未满足完整短句要求')
        proposed={r['goal_id']:set(r['method_ids']) for r in rows};reference={r['goal_id']:set(r['method_ids']) for r in expected['repairs']}
        metrics['method_selection_matches_agent_reference']=proposed==reference
        metrics['reference_gap_filled_without_agent_support']=any(not reference[g] and proposed.get(g) for g in reference)
        metrics['reasons_require_semantic_review']=True
        issues+=_activity_constraints(data,actual,kind)
    elif kind=='teaching_design':
        issues+=_activity_constraints(data,actual,kind)
        metrics['activity_goal_pairs_match_agent_reference']={(r['method_id'],g) for r in actual['links'] for g in r['goal_ids']}=={(r['method_id'],g) for r in expected['links'] for g in r['goal_ids']}
        metrics['reasons_require_semantic_review']=True
    elif kind=='grade_source_selection':
        for k in ('status','official_total','components','roster_only','excluded_sources'):
            a=actual[k];b=expected[k]
            if isinstance(a,list):
                a={json.dumps(x,ensure_ascii=False,sort_keys=True) for x in a};b={json.dumps(x,ensure_ascii=False,sort_keys=True) for x in b}
            metrics[k+'_matches_source_checked_proposal']=a==b
        issues+=_grade_constraints(data,actual)
    elif kind=='write_section':
        paragraphs=actual['paragraphs'];sid=row['section_id']
        if len(paragraphs)!=len(expected['paragraphs']):issues.append('段落数量不符')
        if any(len(p)>180 for p in paragraphs):issues.append('段落过长')
        if re.search(r'显著|有效解决|保证掌握',''.join(paragraphs)) or (sid=='2.4' and any(_past_effect_claim(p) for p in paragraphs)):issues.append('出现无依据成效断言')
        if sid!='2.4' and any(not re.search(r'将|计划|安排|后续',p) for p in paragraphs):issues.append('改进安排缺少未来语气')
        if not set(actual['source_refs'])<=set(row['evidence_refs']):issues.append('引用不在本课任务依据内')
        rules=' '.join(str(data.get(k,'')) for k in ('task','instruction','reference_adoption_rule','reference_style','presentation_rule'))
        methods=data.get('methods',data.get('facts',[])) if sid=='2.4' else []
        titles=[str(m.get('presentation_name') or m.get('name','')) for m in methods]
        duplicate_titles=sum(any(re.match(r'^\s*(?:[（(]\d+[）)]|\d+[.、])?\s*'+re.escape(t)+r'\s*[:：]',p) for t in titles if t) for p in paragraphs)
        metrics['repeated_method_title_count']=duplicate_titles
        if duplicate_titles and re.search(r'不(?:加|写)[^。]*标题|名称由(?:宿主|程序)|方式名称由',rules):issues.append('违反当前任务要求，段落重复教学方式标题')
        metrics['duplicate_paragraph_count']=len(paragraphs)-len(set(paragraphs))
        if sid!='2.4' and any(_past_effect_claim(p) for p in paragraphs):issues.append('将未来安排写成已取得成效')
        if re.search(r'(?:平均分|达成率|及格率|提升了|提高了)\s*(?:为|达到|至)?\s*\d',''.join(paragraphs)):
            # 教学安排任务没有成绩统计依据；不是仅因出现目标编号就拒绝。
            if not any(k in data for k in ('statistics','calculation_summary','score_summary')):issues.append('教学安排中新增未提供的成绩统计或成效数字')
        metrics['text_grounding_and_fluency_require_review']=True
    metrics['reference_approved']=bool(row.get('review',{}).get('approved'))
    metrics['semantic_quality']='not_established_by_structural_checks'
    return list(dict.fromkeys(issues)),metrics
