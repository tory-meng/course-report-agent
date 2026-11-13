"""按资料用途记录同课版本选择；新教案不能追认旧成绩年度的实际实施。"""
import re
from pathlib import Path

def inspect(case,sources):
    wanted=case['course_name'];aliases={wanted,*case.get('course_aliases',[])};rows=[]
    for source in sources:
        filename=Path(source['path']).name;blocks=source.get('blocks',[])
        head='\n'.join(b['text'] for b in blocks[:30])
        declared=[]
        for m in re.finditer(r'课程名称\s*[:：|]\s*([^|\n]+)',head):
            name=re.sub(r'^[（(]中文[）)]\s*[:：]?','',m[1]).strip().split('/')[0].strip(' 《》')
            name=re.split(r'\s+(?:学分|课程代码|开课单位|课程性质|学时|任课教师)',name)[0].strip()
            if re.search(r'[\u4e00-\u9fff]{2}',name):declared.append(name)
        same=any(name in head or name in filename for name in aliases)
        if declared and any(name not in aliases for name in declared):same=False
        kind=next((kind for regex,kind in [(r'教案','teaching_plan'),(r'教学日历','calendar'),(r'考核方案','assessment_plan'),
                    (r'大纲','outline'),(r'评学|教学小结|分析报告','reflection'),(r'记分|成绩单|成绩核算','grades')] if re.search(regex,filename)),'supporting')
        academic=[]
        for b in blocks[:30]:
            for m in re.finditer(r'(20\d{2})\s*[—－-]\s*(20\d{2})',b['text']):
                if int(m[2])==int(m[1])+1:academic.append({'year':int(m[1]),'academic_year':m[1]+'-'+m[2],'source_ref':b['id']})
        file_year=re.search(r'(?<!\d)(20\d{2})(?!\d)',filename)
        version=max((r['year'] for r in academic),default=int(file_year[1]) if file_year else None)
        cohort=sorted(set(re.findall(r'(20\d{2})\s*级',head)))
        rows.append({'source_id':source['id'],'source_sha256':source.get('sha256'),'path':source['path'],'kind':kind,'declared_courses':declared,
            'identity_status':'same_course' if same else 'unverified_or_other_course','version_year':version,
            'version_basis':'explicit_academic_year' if academic else 'filename_version_label_not_proven_applicability' if file_year else 'unknown',
            'academic_year_evidence':academic,'written_cohorts':cohort,'role':source.get('role'),
            'decision':'excluded_from_latest_selection' if not same or source.get('role')!='current' else 'retain_source'})
    groups={}
    enabled=case.get('autonomous_policy',{}).get('latest_applicable_materials')
    for kind in ('teaching_plan','calendar','assessment_plan'):
        candidates=[r for r in rows if r['kind']==kind and r['identity_status']=='same_course' and r['role']=='current']
        known=[r['version_year'] for r in candidates if r['version_year'] is not None]
        latest=max(known) if known else None
        selected=[r['source_id'] for r in candidates if r['version_year']==latest] if latest is not None else []
        for r in candidates:
            if enabled and r['source_id'] in selected:r['decision']='latest_proposal_source_not_historical_execution_proof'
            elif enabled and r['version_year'] is not None:r['decision']='older_version_retained_for_audit'
            else:r['decision']='version_or_authorization_unconfirmed'
        groups[kind]={'latest_version_year':latest,'selected_source_ids':selected if enabled else [],
                      'unversioned_source_ids':[r['source_id'] for r in candidates if r['version_year'] is None]}
    return {'version':'material_scope_v1','sources':rows,'selections_by_purpose':groups,'authorization_applied':bool(enabled),
        'scope':'仅按用途选择同课最新方案来源；不跨课程选最新文件，不更改登记成绩学期，不把新版安排当作旧轮次已实施证明。文件名年份只作版本标识，适用班级仍单独保留。'}
