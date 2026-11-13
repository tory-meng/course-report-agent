"""成绩方案推断、目标支撑设计和课程改进安排，全部保留独立运行记录。"""
import copy
import json
import re
import sqlite3
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from .common import save
from .client import task
from .structured import obj,array,choice,text

def fit_two_components(rows):
    """百分比网格+Excel四舍五入，必须解释全部有效行；保留所有可行解。"""
    candidates=[]
    if not rows:return candidates
    for percent in range(1,100):
        w=Decimal(percent)/100
        for digits in (None,0,1,2):
            ok=True
            for a,b,total in rows:
                value=Decimal(str(a))*w+Decimal(str(b))*(1-w)
                if digits is not None:value=value.quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
                if abs(value-Decimal(str(total)))>Decimal('0.0000001'):ok=False;break
            if ok:candidates.append({'weights':[percent/100,(100-percent)/100],'round_digits':digits})
    return candidates

def infer_grade_scheme(case,facts,grades,sources,run):
    """真实公式优先，数值拟合只作补充；不覆盖正式登记分数。"""
    parents=[a for a in facts.get('assessments',[]) if a.get('raw_item_id')]
    if len(parents)!=2:raise ValueError('本轮自动推断要求两项总评组成，复杂方案应保留完整层级')
    ids=[a['raw_item_id'] for a in parents]
    students=[s for s in grades['students'] if s['status']=='normal' and s.get('total') is not None and all(s['scores'].get(i) is not None for i in ids)]
    rows=[(*[s['scores'][i] for i in ids],s['total']) for s in students]
    candidates=fit_two_components(rows)
    # 现有真实公式审计仅接受直接线性式；同时读它生成的公式副本以识别层级公式。
    from .grade_formulas import audit,linear_terms,evaluate,expression_tree
    audit(case,sources,run)
    if case.get('score_formula_policy',{}).get('enabled'):
        from .score_formulas import inventory,enforce
        formula_inventory=inventory(case,sources,run)
        enforce(grades['mapping'],formula_inventory)
    import openpyxl
    formula_evidence=[];component_evidence=[]
    from .grade_layout import layouts,is_student_id
    for source in sources:
        p=Path(source['path']);fp=p if p.suffix.lower()=='.xlsx' else run/'formula_source'/(p.stem+'.xlsx')
        if source.get('kind')!='grades' or not fp.exists():continue
        wb=openpyxl.load_workbook(fp,data_only=False)
        try:
            for sh in source.get('sheets',[]):
                ws=wb[sh['name']]
                for layout in layouts(sh):
                    columns={c['column']:c for c in layout['columns']}
                    for n,row in enumerate(sh['rows'],1):
                        if n<=layout['header_row'] or len(row)<layout['student_id'] or not is_student_id(row[layout['student_id']-1]):continue
                        for col,c in columns.items():
                            if col>len(row):continue
                            formula=ws.cell(n,col).value
                            if not isinstance(formula,str) or not formula.startswith('='):continue
                            try:
                                weights,digits=linear_terms(formula,n)
                                def original(cell):
                                    cell=ws[cell];return row[cell.column-1]
                                if abs(evaluate(expression_tree(formula),original)-Decimal(str(row[col-1])))>Decimal('0.0000001'):continue
                                active=[i for i in facts['items'] if not i.get('summary_only')]
                                child_weights={}
                                for letter,w in weights.items():
                                    header=columns[openpyxl.utils.column_index_from_string(letter)]['label']
                                    matches=[i['id'] for i in active if i['name'].strip()==header.strip()]
                                    if len(matches)!=1:break
                                    child_weights[matches[0]]=w
                                if len(child_weights)==len(weights):
                                    parent_match=[a for a in parents if '平时' in c['label'] and '平时' in a['name']]
                                    if len(parent_match)==1:component_evidence.append({'source_id':source['id'],'cell':ws.cell(n,col).coordinate,'formula':formula,'assessment_id':parent_match[0]['id'],'within_parent_weights':child_weights,'cache_verified':True})
                                if c['role'] not in ('total','generic_score'):continue
                                resolved=[]
                                for letter,w in weights.items():
                                    label=columns[openpyxl.utils.column_index_from_string(letter)]['label']
                                    match=[i for i,a in enumerate(parents) if ('平时' in label and '平时' in a['name']) or ('期末' in label and '期末' in a['name'])]
                                    if len(match)!=1:raise ValueError('公式列与总评组成无法对应')
                                    resolved.append((match[0],w))
                                if len(resolved)!=2 or len({i for i,w in resolved})!=2:continue
                                formula_evidence.append({'source_id':source['id'],'sheet':sh['name'],'cell':ws.cell(n,col).coordinate,'formula':formula,'weights':[dict(resolved)[i] for i in range(2)],'cache_verified':True})
                            except (ValueError,TypeError,KeyError,IndexError,ArithmeticError):continue
        finally:wb.close()
    if not candidates:raise ValueError('没有能解释全部正式总评的两项权重方案，不能强行拟合')
    patterns={tuple(e['weights']) for e in formula_evidence}
    priority=[c for c in candidates if tuple(c['weights']) in patterns]
    if len(patterns)>1:raise ValueError('成绩册真实公式权重存在冲突')
    if patterns and not priority:raise ValueError('真实公式权重不能解释正式总评，须保留层级另行处理')
    pool=priority or candidates
    current=[a.get('total_weight',.5) for a in parents]
    selected=min(pool,key=lambda c:(sum(abs(x-y) for x,y in zip(c['weights'],current)),c['round_digits'] is None,abs((c['round_digits'] or 0))))
    report={'status':'all_valid_rows_matched','origin':'source_formulas_and_registered_rounding' if priority else 'all_row_numeric_fit',
            'parents':[{'id':a['id'],'item_id':a['raw_item_id'],'name':a['name']} for a in parents],
            'valid_rows':len(rows),'registered_rows':len(grades['students']),'selected':selected,'feasible_candidates':candidates,
            'unique_in_percent_grid':len(candidates)==1,'formula_evidence':formula_evidence,
            'component_formula_evidence':component_evidence,
            'preserved_official_totals':True,'weights_inferred_are_not_institutional_approval':True}
    save(run/'grade_scheme_inference.json',report)
    terms='＋'.join(a['name']+'×'+format(w*100,'g')+'%' for a,w in zip(parents,selected['weights']))
    formula='总评成绩＝'+('ROUND（'+terms+'，'+str(selected['round_digits'])+'）' if selected['round_digits'] is not None else terms)+'。'
    facts['metadata']['grade_formula_text']=formula+'以成绩册登记分数为准，先采用登记的分项成绩，再计算总评并按上述规则取位。'
    if facts['metadata'].get('component_formula_text'):
        facts['metadata']['grade_formula_text']+=facts['metadata']['component_formula_text']
    if component_evidence:
        first=component_evidence[0];weights=first['within_parent_weights']
        if any(e['within_parent_weights']!=weights for e in component_evidence):raise ValueError('分项构成公式不一致')
        names={i['id']:i['name'] for i in facts['items']}
        facts['metadata']['grade_formula_text']+='平时成绩＝'+'＋'.join(names[i]+'×'+format(w*100,'g')+'%' for i,w in weights.items())+'，按成绩单登记值取位后计入总评。'
    facts['metadata']['total_rule_text']=facts['metadata']['grade_formula_text']
    facts['grade_scheme_inference']=report
    return report

def design_goals(case,facts,profile,scheme,client,run):
    """模型选择课程语义支撑，宿主分配系数并做覆盖校验，不生成学生分数。"""
    p=copy.deepcopy(profile);items=[i for i in facts['items'] if not i.get('summary_only')];gids=[g['id'] for g in facts['goals']];iids=[i['id'] for i in items]
    if case.get('evaluation_design',{}).get('goal_links') in ('preserve_source_matrix','preserve_checkbox_support'):
        # 已有同课矩阵时保留其系数。不能再让语义选择把明确的数值关系改成均分。
        import math
        if case['evaluation_design']['goal_links']=='preserve_checkbox_support':
            from .checkbox_allocations import verify
        else:
            from .source_allocations import verify
        evidence=verify(p,facts)
        for parent,weight in zip(scheme['parents'],scheme['selected']['weights']):
            actual=sum(p['item_total_weights'][i['id']] for i in items if i['assessment_id']==parent['id'])
            if not math.isclose(actual,weight,abs_tol=1e-10):
                raise ValueError('成绩册核算权重与来源矩阵汇总权重不符，不能静默重分配')
        p['design_record']={'source':'current_course_checkbox_with_declared_equal_allocation' if p.get('source_checkbox') else 'verified_current_course_matrix','evidence':evidence,
                            'grade_scheme_file':'grade_scheme_inference.json','approved':False}
        save(run/'source_allocation_verification.json',p['design_record'])
        return p
    schema=obj({'links':array(obj({'item_id':choice(iids),'goal_ids':array(choice(gids),1,len(gids)),'reason':text(160)},['item_id','goal_ids','reason']),len(items),len(items))},['links'])
    data={'course':case['course_name'],'goals':facts['goals'],'items':items,'methods':facts.get('methods',[]),
          'task':'为本课程设计考核项到课程目标的支撑关系。每项出现一次，所有课程目标均须覆盖。综合性平时/期末成绩可同时支撑多个目标，具体小组展示优先协作交流，课堂测试优先知识应用。只能使用提供的项和目标，不创造分数或宣称已实施。只返回JSON。'}
    def check(a):
        links=a['links']
        if sorted(x['item_id'] for x in links)!=sorted(iids):raise ValueError('考核项缺失或重复')
        if set(g for x in links for g in x['goal_ids'])!=set(gids):raise ValueError('课程目标未全部覆盖')
        if any(len(x['goal_ids'])!=len(set(x['goal_ids'])) for x in links):raise ValueError('同一支撑关系目标重复')
    answer=task(client,[{'role':'system','content':'你负责课程评价方案设计。严格按给定课程目标和评分项选择合理支撑，返回JSON。'},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],run/'records'/'evaluation_design.json',check,{'task_kind':'evaluation_design','case_id':case['id'],'group_id':case.get('group_id')},retries=1,output_schema=schema)
    p['allocations']={x['item_id']:{g:(1/len(x['goal_ids']) if g in x['goal_ids'] else 0) for g in gids} for x in answer['links']}
    # 既有分项组内权重保持；总评层使用成绩册核算权重。
    for parent,weight in zip(scheme['parents'],scheme['selected']['weights']):
        children=[i['id'] for i in items if i['assessment_id']==parent['id']]
        previous=sum(p['item_total_weights'][i] for i in children)
        for iid in children:p['item_total_weights'][iid]=p['item_total_weights'][iid]/previous*weight
        verified=[r['within_parent_weights'] for r in scheme.get('component_formula_evidence',[]) if r['assessment_id']==parent['id'] and set(r['within_parent_weights'])==set(children)]
        if verified:
            if any(v!=verified[0] for v in verified):raise ValueError('同一考核组的分项公式不一致')
            for iid in children:p['item_total_weights'][iid]=verified[0][iid]*weight
    p['design_record']={'source':'local_model_semantic_links','links':answer['links'],'coefficients':'equal_allocation_within_each_selected_item','grade_scheme_file':'grade_scheme_inference.json','approved':False}
    return p

def improvement_candidates(facts):
    """把本课目标转成可执行安排，供模型挑选；所有内容均为未来教学方案。"""
    topics=' '.join(g['text'] for g in facts['goals'])
    ids={g['id'] for g in facts['goals']}
    if '提示词' in topics and ids=={'G1','G2','G3'}:
        return {
          '6.1':[
            '围绕课程目标1，将文献检索与提示词应用组织为连贯任务。安排学生从问题提炼、关键词设计、数据库检索到结果筛选形成检索记录，再比较不同提示词产生的结果，说明信息来源和选择理由。',
            '围绕课程目标2和课程目标3，安排提示词安全与学术规范案例讨论，要求检查模型回答的来源、适用条件与潜在风险。将前沿工具阅读和自主练习记录纳入日常反馈，关注学生持续学习过程。'],
          '6.2':[
            '教学实施将采用讲授示范、独立练习、交流反馈相结合的方式。课堂安排文献检索与提示词修改练习，课后保存检索式、筛选依据、提示词版本和结果比较，教师按共性问题进行讲评。',
            '后续评价将对照课程目标分别记录工具使用、问题解决与安全意识、自主学习表现。结合原始成绩及文档汇报观察学生差异，对薄弱环节增加练习，并在同一评价口径下比较后续任务的变化。'],
          '7':[
            '计划在后续开课时完善课程目标、教学任务和考核项的对应表，细化检索质量、提示词设计、安全与规范、反思改进等评价要点，使每项任务能够留下可追溯的学习记录。',
            '将按成绩分布和课堂反馈开展分层辅导，指导学生纠正检索策略和提示词表达中的常见问题。课程结束后汇总作业、文档汇报和学习反馈，形成后续教学内容与考核安排的调整记录。']}
    if '专业词汇' in topics and ids=={'G1','G2','G3','G4','G5','G6'}:
        return {
          '6.1':[
            '围绕课程目标1和课程目标2，计划把专业词汇学习与技术文档阅读、摘要表达结合起来。按计算机软硬件、语言、网络和软件设计内容组织词汇归纳与语境练习，指导学生说明术语含义并用于专业表达。',
            '结合课程目标3至课程目标6，安排设备与技术方案比较、小组讨论及自主阅读任务。要求学生说明选择依据、记录小组分工，并交流阅读中的疑问和解决过程，使专业应用、团队沟通与自主学习相互衔接。'],
          '6.2':[
            '课堂将采用专业材料导读、案例分析、小组展示与交流反馈相结合的方式。教师先示范术语辨析和文档结构分析，再安排学生开展阅读、写作和口头表达练习，并围绕共性语言问题进行讲评。',
            '后续评价将结合随堂测试、大作业、小组展示和课堂表现，分别观察词汇理解、技术文档表达、协作沟通与自主学习情况。对分项表现较弱的学生安排词汇巩固和文档修改练习，并保存修改前后的学习记录。'],
          '7':[
            '计划补充与课程大纲相匹配的专业英语阅读材料，按主题更新术语和技术文档案例。持续收集学生对材料难度、表达任务与课堂节奏的反馈，据此调整导读内容和课后练习。',
            '将细化小组展示、随堂测试、大作业及课堂表现的评价要点，分别记录个人参与和任务成果。后续核算继续保留分项原始成绩、平时汇总及总评取位过程，结合学习记录逐项调整教学安排。']}
    if '逻辑电路' in topics:
        return {
          '6.1':['围绕课程目标1，将安排逻辑代数化简、卡诺图表达和逻辑门功能验证的衔接练习。要求学生同时提交真值表、化简过程与电路验证结果，针对符号转换和边界条件中的问题进行讲评。',
                 '围绕课程目标2和课程目标3，计划设置组合逻辑、时序逻辑与存储逻辑的设计任务。引导学生从需求描述出发比较电路方案，使用仿真工具检查状态变化，并结合文献和器件资料解释设计选择。'],
          '6.2':['课堂将按问题分析、电路设计、仿真验证与成果讲评组织活动。实验中要求保留输入输出、时序变化和故障排查记录，报告中说明设计过程与验证依据，教师据此调整重点内容的讲解与练习。',
                 '后续评价将分别查看基础知识题、电路分析题、实验记录和设计报告。对知识题与应用题表现不同的学生安排相应训练；保存分项评分和修改记录，按同一评价口径观察后续任务表现。'],
          '7':['计划完善数字逻辑实验与设计报告的评价要求，分别记录逻辑表达、方案分析、仿真验证及问题解释情况，减少仅凭最终电路结果评分的做法，使过程表现能够进入后续教学反馈。',
               '将结合成绩分布和课堂反馈开展分层辅导，对逻辑化简、时序分析与综合设计中的常见问题补充练习，并持续更新器件资料和应用案例。后续保留独立的目标题项评分以便逐项分析变化。']}
    if '写作' in topics and '沟通' in topics:
        return {
          '6.1':['围绕课程目标1，将安排自我介绍、邮件沟通和科技文档汇报等情境练习。要求学生根据交流对象选择表达方式，在同伴反馈后修改语言和展示内容，关注表达礼仪、信息完整性与回应问题的能力。',
                 '围绕课程目标2和课程目标3，计划把文献检索、资料筛选、文档组织和工具应用连成写作任务。引导学生从科学问题和解决方案出发搭建结构，规范引用来源，并使用写作与文献管理工具完成修订。'],
          '6.2':['课堂将采用示范、独立写作、同伴互评和修改讲评相结合的方式。安排学生保存检索记录、文档版本和修改说明，展示时说明论证逻辑与工具选择，教师据此归纳沟通及写作中的共性问题。',
                 '后续评价将结合平时作业、期末文档和文档答辩，分别观察沟通表达、文献组织与工具使用。保留文档评分、答辩评分及期末汇总的取位过程，对写作和口头表达的薄弱环节分别安排指导。'],
          '7':['计划完善科技文档与汇报任务的评价要求，明确结构逻辑、信息来源、引用规范、版式和表达效果等评价要点。通过任务版本比较和反馈记录，为后续教学内容与课堂练习调整提供依据。',
               '将持续收集学生对检索、写作工具和答辩训练的反馈，更新与本专业学习任务相符的文档案例。后续核算继续分别保留各项原始成绩和汇总记录，避免将取整后的总评反向当作独立能力评分。']}
    if '图像' in topics:
        return {
          '6.1':['围绕课程目标1和课程目标2，将把灰度变换、空间滤波和图像增强组织为连续实验。要求学生说明算法的适用场景与参数选择，比较处理前后图像并分析差异，避免只展示结果而缺少原理解释。',
                 '结合课程目标3至课程目标6，计划安排文献阅读、算法实现和成果交流任务。指导学生记录资料来源、实验分工与修改过程，在合作中说明方法选择，结合自主学习反馈完善解决方案。'],
          '6.2':['课堂将采用原理讲解、算法演示、实验练习与结果讲评相结合的方式。针对图像平滑、锐化、频域处理、彩色处理和分割等任务，安排学生保存代码、参数和实验输出，说明不同方法的适用条件。',
                 '后续评价将分别查看基础知识理解、算法实现、实验报告和成果交流表现。依据正式成绩与学习记录安排针对性练习；保留各环节的实际评分，不把汇总成绩反推为独立目标子成绩。'],
          '7':['计划完善图像处理实验与课题报告的评价要求，明确算法说明、实现过程、结果分析和规范引用等评价要点，并收集常见错误案例用于后续讲评。',
               '将持续整理自主学习与团队交流记录，比较学生在不同实验任务中的表现。后续记录独立的实验观察与评分，区分原始测量、汇总结果和评价换算，形成可追溯的教学反馈。']}
    if '网络' in topics and '项目' in facts.get('metadata',{}).get('course_name',''):
        return {
          '6.1':['围绕课程目标1至课程目标3，将把网络需求分析、方案设计、配置实施和验收组织为衔接任务。要求学生说明建设目标、设计依据和约束条件，用配置记录与测试结果验证方案。',
                 '结合课程目标4和课程目标5，计划明确小组分工与阶段沟通要求。安排学生交叉检查需求、设计与配置文档之间的一致性，记录个人工作和协作反馈，培养项目管理与团队合作意识。'],
          '6.2':['课堂将采用项目案例分析、分组实践、阶段检查与成果汇报相结合的方式。教师围绕网络规划和配置中的共性问题进行讲评，指导学生保留文档版本、配置说明、测试结果和修订依据。',
                 '后续评价将结合过程性检查和项目验收，分别观察知识应用、方案论证、工程实现与团队协作。对薄弱环节安排补充练习，并在相同评分口径下比较后续阶段表现。'],
          '7':['计划完善项目需求说明、设计说明、配置说明与验收报告的评价要点，明确文档衔接、方案合理性、配置可复现性和团队贡献的记录要求。',
               '将依据成绩分布和项目反馈更新案例与实践任务，针对需求遗漏、配置错误和验证不足开展辅导。后续分别保留过程检查与最终验收的评分依据，避免仅凭综合分判断各项能力。']}
    if '计算机网络' in topics:
        return {
          '6.1':['围绕课程目标1和课程目标2，将把网络基础概念、协议机制和局域网应用组织为衔接练习。要求学生从实际需求出发分析网络方案，说明技术选择，并通过配置与实验结果验证判断。',
                 '结合其他课程目标，计划安排技术资料检索、方案讨论与小组成果交流。引导学生比较不同设计思路，说明网络应用中的约束与影响，记录分工、自主学习过程和问题解决依据。'],
          '6.2':['课堂将采用协议原理讲解、案例讨论、实验操作和结果讲评相结合的方式。围绕地址规划、网络连接、协议分析与配置验证安排练习，要求学生保存操作记录并解释异常现象。',
                 '后续评价将结合课堂讨论、测验作业、实验过程、实验验收和期末考核，分别观察基础理解与综合应用。对分项表现较弱的学生安排对应训练，并保留过程反馈和修订记录。'],
          '7':['计划完善网络实验与验收的评价要求，明确分析思路、配置过程、测试结果和成果表达等评价要点；继续分别保留分项、平时汇总与正式总评的取位过程。',
               '将结合课堂反馈和成绩分布更新网络应用案例，开展协议理解与故障分析训练。后续增加独立的目标表现记录，并持续收集自主学习和协作交流反馈，逐项调整教学安排。']}
    # 目标原文已经在2.2完整展示。这里按完整名称组织未来任务，不能切半个词再补句号。
    def full_labels(values,budget):
        chosen=[]
        for value in values:
            if len('、'.join(chosen+[value]))>budget:break
            chosen.append(value)
        return '、'.join(chosen)+('等' if chosen and len(chosen)<len(values) else '')
    goal_names=full_labels(['课程目标'+g['id'].removeprefix('G') for g in facts['goals']],55)
    activity_names=full_labels([m.get('name','') for m in facts.get('methods',[]) if m.get('name')],55) or '本课已有教学活动'
    course=facts.get('metadata',{}).get('course_name','本课程')
    # 超长课名只在完整标题/元信息展示，不截断名称。
    subject='《'+course+'》' if len(course)<=24 else '本课程'
    division='任务分工、' if re.search(r'团队|协作|角色|分工',topics) else ''
    return {'6.1':[f'将围绕{subject}的课程目标组织后续教学，结合{activity_names}安排学习任务。每项任务将明确成果要求，保留过程与反馈记录。',
                   f'计划对照{goal_names}细化练习与讲评要求，分别观察学习成果和方法选择。学生将记录{division}学习过程与修改依据，教师结合任务表现调整后续指导。'],
            '6.2':['课堂将按任务示范、学生练习和成果讲评组织实施，要求学生保存学习过程与修订记录。','后续将结合分项成绩和学习记录观察各课程目标表现，对薄弱环节增加练习与个别指导。'],
            '7':['计划完善课程目标与考核项的对应关系，细化任务评价要求并保留原始评分记录。','将汇总课堂反馈和学习成果，在相同评价口径下比较后续表现，并据此调整教学内容。']}

def improvement_sections(case,facts,calc,client,run):
    reference=[]
    db=run/'reference_snapshot.sqlite3'
    if not db.exists():db=Path(__file__).resolve().parents[1]/'knowledge'/'references.sqlite3'
    if db.exists():
        con=sqlite3.connect(db)
        try:
            files=list(dict.fromkeys(h['file'] for h in facts.get('teaching_reference_context',{}).get('hits',[])))[:2]
            for file in files:
                rows=con.execute("SELECT c.id,c.text,c.locator FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.relpath=? AND (c.text LIKE '%6.1%' OR c.text LIKE '%6.2%' OR c.text LIKE '%7 持续%') LIMIT 1",(file,)).fetchall()
                reference += [{'reference_id':row[0],'file':file,'locator':row[2],'text':row[1][:650]} for row in rows]
        finally:con.close()
    candidates=improvement_candidates(facts)
    schema=obj({s:array(choice(candidates[s]),2,2) for s in ('6.1','6.2','7')},['6.1','6.2','7'])
    from .writing import semantic_view
    data={'course':case['course_name'],'goals':semantic_view(facts['goals']),'teaching_methods':semantic_view(facts.get('methods',[])),
          'original_course_notes':facts.get('narratives',[]),'grade_summary':{'valid':calc['total']['valid_count'],'mean':calc['total']['mean']},
          'reference_style_only':reference,
          'section_candidates':candidates,
          'task':'结合本课内容与参考章节结构，为6.1改进重点、6.2实施与效果评价、7持续改进分别选择两个不同候选段落并合理排序。只选择相应小节的候选原文，不能重复段落或用其他小节替代。它们均为后续方案，不是历史成效。JSON键为6.1、6.2、7。'}
    def check(a):
        for sid,paras in a.items():
            joined=''.join(paras)
            if len(set(paras))!=len(paras):raise ValueError('同一章节不能重复段落')
            if re.search(r'已实施|已完成|显著|明显提升|上一轮.{0,12}(提高|取得)|待核验|待审核|代理估计|拟',joined):raise ValueError('教学方案混入过去成效或审核标签')
            for paragraph in paras:
                if not re.search(r'将|计划|安排|后续|应当|应该',paragraph):raise ValueError('改进安排须表达后续行动')
                body=re.sub(r'课程目标\d+','',paragraph)
                if re.search(r'\d',body):raise ValueError('改进方案不新增数值')
    result=task(client,[{'role':'system','content':'你撰写本课程教学改进方案，参考资料只用于文体组织。写具体可执行活动，未来安排不能写成历史事实。严格返回JSON。'},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],run/'records'/'improvement_design.json',check,{'task_kind':'improvement_design','case_id':case['id'],'group_id':case.get('group_id')},retries=1,output_schema=schema)
    save(run/'improvement_design_sources.json',{'course_source_refs':sorted({r for g in facts['goals']+facts.get('methods',[]) for r in g.get('source_refs',[])}),'reference_style':reference,'candidate_origin':'host_course_goal_activity_design_model_selection','scope':'future_teaching_plan','approved':False})
    return result
