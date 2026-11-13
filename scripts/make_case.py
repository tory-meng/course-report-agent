#!/usr/bin/env python3
"""登记一门课程；从正式成绩表/记分册读取课程名称，自动启用成绩优先策略。"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import path,save,signature
from report_agent.readers import read_source
from report_agent.direct_extract import labelled_row
from report_agent.decision_policy import VERSION,source_rank,grade_candidates

SUPPORTED={'.xls','.xlsx','.csv','.tsv','.doc','.docx','.pdf','.txt','.md'}

def create_case(folder,target,case_id,course_name=None,group_id=None):
    folder=path(folder);target=path(target)
    if target.exists():raise ValueError('case已存在，拒绝覆盖：'+str(target))
    if not folder.is_dir():raise ValueError('输入目录不存在：'+str(folder))
    sources=[];read=[];unsupported=[]
    for f in sorted(folder.rglob('*')):
        if not f.is_file() or f.name.startswith(('~$','.~')):continue
        if f.suffix.lower() not in SUPPORTED:
            unsupported.append(str(f));continue
        spreadsheet=f.suffix.lower() in ('.xls','.xlsx','.csv','.tsv')
        source={'id':'src_'+str(len(sources)+1),'path':str(f.resolve()),'role':'current',
                'kind':'grades' if spreadsheet and '考勤' not in f.name else 'document'}
        sources.append(source)
        if spreadsheet:read.append(read_source(source))
    if not sources:raise ValueError('输入目录没有支持的资料')
    names=[]
    for s in read:
        rank=source_rank(s)
        if not rank:continue
        for block in s['blocks']:
            labels,_=labelled_row(block)
            if labels.get('course_name'):names.append({'name':labels['course_name'],'rank':rank,'ref':block['id'],'quote':block['text']})
    if not course_name:
        highest=max((v['rank'] for v in names),default=0)
        values={v['name'] for v in names if v['rank']==highest}
        if len(values)!=1:
            report=target.with_suffix('.inspection.json')
            save(report,{'course_name_candidates':names,'grade_candidates':grade_candidates(read),'unsupported_files':unsupported})
            raise ValueError('成绩资料未给出唯一课程名称；查看 '+str(report)+'，核对后用 --course-name 指定完整名称。')
        course_name=values.pop()
    case={'id':case_id,'group_id':group_id or 'course_'+signature(''.join(course_name.split()))[:16],
          'report_type':'course','course_name':course_name,'sources':sources,'ocr':False,'grade_tables':[],
          'evaluation_policy':{},'extract_chunk_chars':2000,
          'decision_policy':{'mode':VERSION,'model_review':True},
          'data_partition':'holdout_until_reviewed'}
    save(target,case)
    save(target.with_suffix('.inspection.json'),{'course_name_candidates':names,'grade_candidates':grade_candidates(read),
         'unsupported_files':unsupported,'legacy_doc_count':sum(Path(s['path']).suffix.lower()=='.doc' for s in sources),
         'note':'所有材料默认current；历史或参考报告请在case中明确标为history/style_reference。'})
    print('已生成：',target,'；课程：',course_name,'；来源数：',len(sources),'；已启用成绩优先与重大问题交互。')
    if unsupported:print('以下文件未登记，请核对是否含必要材料：',unsupported)
    return case

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir',required=True);p.add_argument('--case-id',required=True)
    p.add_argument('--course-name');p.add_argument('--group-id');p.add_argument('--out',required=True)
    a=p.parse_args();create_case(a.input_dir,a.out,a.case_id,a.course_name,a.group_id)

if __name__=='__main__':main()
