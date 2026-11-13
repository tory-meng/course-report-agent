"""课程包一条命令：关键成绩门禁→自动准备→现有生成流水线。"""
from pathlib import Path
from .common import load,save,digest,path,require_empty
from .grade_preflight import case_preflight,stop_without_model

def run_material(case_file,runtime_file,out,prepare_only=False):
    case_file=path(case_file);runtime_file=path(runtime_file);out=path(out)
    case=load(case_file);preflight=case_preflight(case)
    if preflight['status']!='passed':return stop_without_model(case,out,preflight,False,str(runtime_file))
    require_empty(out)
    manifest={'status':'preparing','original_case':str(case_file),'original_case_sha256':digest(case_file),
              'runtime':str(runtime_file),'runtime_sha256':digest(runtime_file),'model_requested':False,
              'human_facts_file_required':False,'report_generated':False}
    save(out/'workflow_manifest.json',manifest)
    from .material_preparation import prepare
    try:
        prepared=prepare(case_file,runtime_file,out/'prepared')
    except ValueError as exc:
        manifest.update(status='blocked_material_preparation',reason=str(exc))
        save(out/'workflow_manifest.json',manifest)
        (out/'STOP_REASON.txt').write_text(str(exc)+'\n未进入模型生成；需检查资料或适配原因，不等于已判定全部材料不完整。\n',encoding='utf-8')
        return manifest
    manifest.update(status='prepared',prepared_case=str(prepared),prepared_case_sha256=digest(prepared))
    save(out/'workflow_manifest.json',manifest)
    if prepare_only:return manifest
    from .pipeline import run
    try:result=run(str(prepared),str(runtime_file),str(out/'report'))
    except Exception as exc:
        manifest.update(status='generation_failed',reason=str(exc),model_requested=(out/'report/records').exists())
        save(out/'workflow_manifest.json',manifest);raise
    manifest.update(status=result['status'],report_run=str(out/'report'),
                    model_requested=result.get('model',{}).get('mode')!='offline_fixture',
                    report_generated=(out/'report/report.docx').is_file() and (out/'report/report.pdf').is_file())
    save(out/'workflow_manifest.json',manifest)
    return manifest
