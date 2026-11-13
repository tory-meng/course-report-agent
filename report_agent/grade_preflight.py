"""生成前检查关键成绩单；空白名册、考勤和班级汇总不能代替学生成绩。"""
import re
from pathlib import Path
from .common import number
from .grade_layout import layouts,label,ALIASES
from .grade_selection import identities

SCORE_LABELS={'总评','总评成绩','最终成绩','总成绩','综合成绩','课程总评成绩','总评得分','课程成绩','成绩','考核成绩'}
GRADES={'优','优秀','良','良好','中','中等','及格','不及格','合格','不合格','通过','未通过','A','B','C','D','F','A+','A-','B+','B-','C+','C-'}

def score_value(value):
    if value is None or isinstance(value,bool) or not str(value).strip():return None
    try:return number(value)
    except (ValueError,TypeError):
        return str(value).strip() if str(value).strip() in GRADES else None

def valid_id(value):
    s=str(value).strip()
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',s))

def inspect_sheet(sheet):
    """按表头定位，不用数字密度猜总评列；兼容只有姓名的成绩表。"""
    detected=layouts(sheet)
    if not detected:
        aliases={'paperid':'学号','studentid':'学号','studentnumber':'学号','presenter':'姓名','name':'姓名','studentname':'姓名','score':'成绩','totalscore':'总评','finalgrade':'总评'}
        normalized={**sheet,'rows':[[aliases.get(re.sub(r'\s+','',str(v)).lower(),v) for v in row] if n<5 else row for n,row in enumerate(sheet['rows'])]}
        detected=layouts(normalized)
        for layout in detected:layout['normalized_english_headers']=True
    if not detected:
        for index,row in enumerate(sheet['rows'][:35]):
            names=[i+1 for i,v in enumerate(row) if label(v) in ALIASES['name']]
            totals=[i+1 for i,v in enumerate(row) if label(v) in SCORE_LABELS]
            if len(names)==1 and totals:
                detected.append({'header_row':index+1,'student_id':None,'columns':[{'column':i+1,'label':str(v),'role':'name' if i+1 in names else 'total' if i+1 in totals else 'unknown'} for i,v in enumerate(row)]})
                break
    results=[]
    for layout in detected:
        names=[c['column'] for c in layout['columns'] if c['role']=='name']
        totals=[c for c in layout['columns'] if c['role']=='total' or label(c['label']) in SCORE_LABELS]
        if len(names)!=1:continue
        other=[c for c in layout['columns'] if c not in totals and c['role'] not in ('student_id','name') and label(c['label']) not in ('序号','专业','班级','年级','身份证','手机号','联系电话')]
        registered=0;counts={c['column']:0 for c in totals};numeric={c['column']:0 for c in totals};other_counts={c['column']:0 for c in other};ids=[]
        for row in sheet['rows'][layout['header_row']:]:
            if len(row)<names[0]:continue
            name=str(row[names[0]-1]).strip()
            if not name or name in ('姓名','合计','总计','平均','平均分','人数'):continue
            sidcol=layout['student_id']
            if sidcol and (len(row)<sidcol or not valid_id(row[sidcol-1])):continue
            if not sidcol and (len(name)>30 or re.search(r'平均|合计|总计|签字|签名|教师|课程名称',name)):continue
            registered+=1
            ids.append(str(row[sidcol-1]).strip() if sidcol else name)
            for c in totals:
                value=score_value(row[c['column']-1]) if len(row)>=c['column'] else None
                if value is not None:counts[c['column']]+=1
                if isinstance(value,(int,float)):numeric[c['column']]+=1
            for c in other:
                value=score_value(row[c['column']-1]) if len(row)>=c['column'] else None
                if isinstance(value,(int,float)) and 0<=value<=100:other_counts[c['column']]+=1
        if registered:
            results.append({'sheet':sheet['name'],'header_row':layout['header_row'],'student_id_column':layout['student_id'],
                'name_column':names[0],'registered_count':registered,
                'total_columns':[{'column':c['column'],'label':c['label'],'scored_count':counts[c['column']],'numeric_count':numeric[c['column']]} for c in totals],
                'score_columns':[{'column':c['column'],'label':c['label']} for c in layout['columns'] if re.search(r'平时|期末|期中|作业|实验|答辩|项目|成绩|得分',c['label']) and c['role'] not in ('student_id','name')],
                'duplicate_student_keys':len(ids)-len(set(ids)),
                'requires_name_only_adapter':not bool(layout['student_id']),
                'normalized_english_headers':layout.get('normalized_english_headers',False),
                'unresolved_numeric_columns':[{'column':c['column'],'label':c['label'],'numeric_count':other_counts[c['column']]} for c in other if other_counts[c['column']]]})
    if not results:
        # 无表头只记录候选，不把某个数值列擅自命名为正式总评。
        rows=sheet['rows'];width=max((len(r) for r in rows),default=0)
        for sidcol in range(width-1):
            found=[r for r in rows if len(r)>sidcol+1 and re.fullmatch(r'\d{8,18}',str(r[sidcol]).strip()) and re.fullmatch(r'[\u3400-\u9fff·]{2,10}',str(r[sidcol+1]).strip())]
            if len(found)<3:continue
            numbers=[]
            for col in range(sidcol+2,width):
                n=sum(isinstance((v:=score_value(r[col] if len(r)>col else None)),(int,float)) and 0<=v<=100 for r in found)
                if n:numbers.append({'column':col+1,'label':'无表头','numeric_count':n})
            if numbers:
                results.append({'sheet':sheet['name'],'header_row':None,'student_id_column':sidcol+1,'name_column':sidcol+2,'registered_count':len(found),'total_columns':[],'score_columns':[],'duplicate_student_keys':0,'requires_name_only_adapter':False,'requires_headerless_adapter':True,'unresolved_numeric_columns':numbers})
                break
    return results

def inspect_source(source):
    tables=[r for sh in source.get('sheets',[]) for r in inspect_sheet(sh)]
    for t in tables:
        if t.get('requires_headerless_adapter') and len(t['unresolved_numeric_columns'])==1 and re.search(r'成绩单|成绩表',Path(source['path']).stem):
            c=t['unresolved_numeric_columns'][0]
            t['total_columns']=[{'column':c['column'],'label':'成绩（无表头单列识别）','scored_count':c['numeric_count'],'numeric_count':c['numeric_count']}]
            t['inferred_total_column']={'origin':'explicit_grade_filename_and_single_numeric_column_after_student_id_name','column':c['column']}
    return {'source_id':source['id'],'path':source['path'],'sha256':source.get('sha256'),
            'written_courses':identities(source),'tables':tables,
            'attendance':bool(re.search(r'考勤|签到',Path(source['path']).name))}

def check(case,sources):
    """只作有无成绩的前置检查；身份、版本和分数冲突仍由后续严格裁决。"""
    reports=[inspect_source(s) for s in sources if s.get('role')=='current' and s.get('sheets')]
    accepted=[r for r in reports if not r['attendance'] and any(c['scored_count'] for t in r['tables'] for c in t['total_columns'])]
    if accepted:return {'status':'passed','case_id':case['id'],'message':'发现包含学生姓名及实际成绩的成绩表；继续核对课程身份、总评口径与计算规则。','sources':reports,'accepted_source_ids':[r['source_id'] for r in accepted]}
    rosters=[r for r in reports if not r['attendance'] and any(t['registered_count'] for t in r['tables'])]
    if any(t.get('unresolved_numeric_columns') for r in rosters for t in r['tables']):
        return {'status':'blocked_grade_semantics','case_id':case['id'],'message':'发现学生记录及数值，但缺少明确总评列（或只有分项/备注列）；已停止生成，需先确定成绩列含义，不能将备注或最后一列直接当总评。','sources':reports,'accepted_source_ids':[]}
    status='blocked_empty_grades' if rosters and any(t['total_columns'] for r in rosters for t in r['tables']) else 'blocked_missing_grades'
    text='成绩单只有空白成绩或缺少可用总评，已停止生成；请补充有实际成绩的成绩单。' if status=='blocked_empty_grades' else '缺少关键成绩单：需要学生姓名及实际总评成绩；考勤、评分标准和班级统计不能代替，已停止生成。'
    return {'status':status,'case_id':case['id'],'message':text,'sources':reports,'accepted_source_ids':[]}

def case_preflight(case):
    from .readers import read_source
    sources=[];errors=[]
    for s in case['sources']:
        p=Path(s['path'])
        if s.get('role','current')!='current':continue
        if p.name.startswith('~$'):continue
        spreadsheet=p.suffix.lower() in ('.xls','.xlsx','.csv','.tsv')
        named_grade=bool(re.search(r'成绩单|记分册|计分册|成绩登记|成绩表|成绩册',p.stem))
        if not spreadsheet and not named_grade:continue
        try:
            source=read_source(s,case.get('ocr',False),case.get('ocr_lang','chi_sim+eng'))
            if not source['sheets'] and p.suffix.lower() in ('.doc','.docx'):
                source['sheets']=word_sheets(source)
            if not source['sheets'] and named_grade:
                errors.append({'source_id':s['id'],'path':s['path'],'error':'候选成绩单尚未提取出可核验的学生成绩表，需要表格识别或格式适配'})
            sources.append(source)
        except Exception as exc:errors.append({'source_id':s['id'],'path':s['path'],'error':str(exc)})
    result=check(case,sources);result['read_errors']=errors
    if result['status']!='passed' and errors:
        result.update(status='blocked_unreadable_grades',message='候选成绩单读取失败，已停止生成；请先修复文件或转换为可读取的成绩表，不能当作没有成绩而跳过。')
    return result

def word_sheets(source):
    groups={}
    for b in source.get('blocks',[]):
        if not b.get('table_id') or not b.get('cells'):continue
        groups.setdefault(b['table_id'],[]).append([c['text'] for c in b['cells']])
    return [{'name':key,'rows':rows,'formula_cells':[]} for key,rows in groups.items()]

def stop_without_model(case,out,result,resume=False,runtime_path=None):
    from .common import load,save,require_empty
    from .progress import Progress
    out=Path(out)
    if resume:
        prior=load(out/'run_manifest.json')
        if prior.get('case_id')!=case['id'] or not prior.get('status','').startswith('blocked_') or prior.get('fingerprint'):
            raise ValueError('不能将原生成任务改写为缺成绩记录；请使用新的输出目录')
    else:require_empty(out)
    manifest={'case_id':case['id'],'group_id':case.get('group_id',case['id']),'status':result['status'],
              'stop_reason':result['message'],'model_requested':False,'report_generated':False,'runtime_path':str(runtime_path)}
    save(out/'run_manifest.json',manifest);save(out/'grade_preflight.json',result)
    (out/'STOP_REASON.txt').write_text(result['message']+'\n',encoding='utf-8')
    progress=Progress(out);progress.stage(1,'检查关键成绩单');progress.finish(result['status'],result['message'])
    return manifest
