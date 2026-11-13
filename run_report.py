#!/usr/bin/env python3
"""v2文档生成入口；用--help查看inspect/run/pdf/doctor。"""
import argparse
import importlib.metadata
import json
import shutil
from report_agent.common import path,load,save,model_files
from report_agent.pipeline import run,inspect,load_template

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('inspect');q.add_argument('--case',required=True);q.add_argument('--out',default='outputs/source_inspection.json')
    q=sub.add_parser('run');q.add_argument('--case',required=True);q.add_argument('--runtime',default='configs/runtime.json');q.add_argument('--out',required=True);q.add_argument('--resume',action='store_true');q.add_argument('--offline',action='store_true');q.add_argument('--probe-extraction',action='store_true',help='验证覆盖各字段类别的连续前缀批次；旧策略仅前两批，成功后同目录--resume继续')
    q.add_argument('--reuse-extraction-from',help='指定旧任务目录；抽取及辅助生成仅在身份、消息/schema完全匹配且通过当前校验时复用')
    q.add_argument('--evaluation-profile',help='显式候选评价配置；绑定课程、输入、模板和参考来源')
    q.add_argument('--allow-estimates',action='store_true',help='允许生成有醒目标识的代理估计试算草稿；不批准事实或训练')
    q.add_argument('--interactive',action='store_true',help='仅对自动核对后仍未解决的重大问题提问；需启用grade_first_minimal_v1策略')
    q=sub.add_parser('doctor');q.add_argument('--runtime',default='configs/runtime.json');q.add_argument('--check-service',action='store_true')
    q=sub.add_parser('pdf');q.add_argument('--run',required=True);q.add_argument('--runtime',default='configs/runtime.json')
    q=sub.add_parser('refresh',help='从已验证计算和生成记录重排报告，不请求在线模型');q.add_argument('--from-run',required=True);q.add_argument('--out',required=True)
    q.add_argument('--repair-host-candidates',action='store_true',help='仅对已核对的两候选全选记录修正宿主候选文本，保留原模型记录与顺序')
    q=sub.add_parser('material-run',help='从材料清单自动准备事实/评价方案并生成，不要求人工编写事实JSON');q.add_argument('--case',required=True);q.add_argument('--runtime',default='configs/runtime.json');q.add_argument('--out',required=True);q.add_argument('--prepare-only',action='store_true')
    q=sub.add_parser('catalog',help='扫描课程资料目录，检查关键成绩并更新安排清单');q.add_argument('--root');q.add_argument('--out',required=True);q.add_argument('--previous')
    q=sub.add_parser('plan');q.add_argument('--case',required=True);q.add_argument('--runtime',default='configs/runtime.json');q.add_argument('--from-run',required=True);q.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='inspect':inspect(a.case,a.out)
    elif a.command=='run':
        result=run(a.case,a.runtime,a.out,a.resume,a.offline,a.probe_extraction,a.interactive,a.reuse_extraction_from,a.evaluation_profile,a.allow_estimates)
        if result.get('status') in ('awaiting_user','generation_blocked','extraction_probe_review_required','extraction_quality_blocked') or result.get('status','').startswith('blocked_'):return 2
    elif a.command=='plan':
        from report_agent.evaluation_profiles import create_profile
        print(json.dumps(create_profile(a.case,a.runtime,a.from_run,a.out),ensure_ascii=False,indent=2))
    elif a.command=='pdf':
        from report_agent.pipeline import retry_pdf
        result=retry_pdf(a.run,a.runtime)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    elif a.command=='refresh':
        from report_agent.refresh import refresh
        refresh(a.from_run,a.out,a.repair_host_candidates)
    elif a.command=='material-run':
        from report_agent.material_workflow import run_material
        result=run_material(a.case,a.runtime,a.out,a.prepare_only)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if result.get('status','').startswith('blocked_') or result.get('status') in ('awaiting_user','generation_blocked','extraction_quality_blocked'):return 2
    elif a.command=='catalog':
        from report_agent.material_catalog import scan
        cfg=load(path('configs/material_intake.json'));registry=path(cfg['latest_registry'])
        previous=a.previous
        if not previous and not a.root and registry.is_file():previous=load(registry)['catalog_path']
        result=scan(a.root or cfg['root'],path(a.out),previous)
        save(registry,{'root':result['root'],'catalog_path':str(path(a.out)/'catalog.json'),'queue_path':str(path(a.out)/'batch_queue.json'),'updated_at':result['scanned_at']})
        print(json.dumps({'packages':result['package_count'],'files':result['file_count'],'status_counts':result['status_counts']},ensure_ascii=False))
    else:
        cfg=load(path(a.runtime));load_template(cfg,'course');report={}
        for pkg in ['python-docx','openpyxl','xlrd','PyMuPDF','matplotlib']:
            try:report[pkg]=importlib.metadata.version(pkg)
            except importlib.metadata.PackageNotFoundError:report[pkg]='MISSING'
        report['libreoffice']=cfg.get('libreoffice') or shutil.which('libreoffice') or shutil.which('soffice')
        if a.check_service:
            from report_agent.client import Client
            report['service']=Client(cfg['model']).identity
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if 'MISSING' in report.values() or not report['libreoffice']:return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
