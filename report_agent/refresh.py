"""从已完成的计算与生成记录重排报告；不依赖在线推理服务。"""
import copy
import re
import shutil
from pathlib import Path
from docx import Document
from .common import load,save,path,digest,require_empty
from .pipeline import fingerprint,load_template
from .reference_db import reference_identity
from .course_presentation import enabled
from .course_design import improvement_candidates
from .report_scope import method_label
from .writing import method_body
from . import tables,charts,render,pdf

def verified_total_rule_description(facts,comparison):
    """仅补充原登记表有百分比且全部有效行核算一致的已知规则；不猜未知公式。"""
    if facts['metadata'].get('total_rule_text') or comparison.get('mismatch_count') or not comparison.get('compared_count'):
        return None
    if comparison.get('comparison_round_digits')!=0:return None
    parents=facts.get('assessments',[]);items={i['id']:i for i in facts.get('items',[])}
    if len(parents)!=2:return None
    parts=[];refs=[]
    for parent in parents:
        iid=parent.get('raw_item_id');weight=parent.get('total_weight');name=parent.get('name','')
        if iid not in items or items[iid].get('max_score')!=100 or weight is None:return None
        percent=format(weight*100,'g');short=re.sub(r'成绩$','',name)
        pattern=re.compile(re.escape(short)+r'\s*[：:]\s*'+re.escape(percent)+r'\s*[%％]')
        matches=[ref for ref,quote in parent.get('quotes',{}).items() if pattern.search(quote)]
        if not matches:return None
        refs+=matches;parts.append(name+'×'+percent+'%')
    if abs(sum(p['total_weight'] for p in parents)-1)>1e-10:return None
    return {'text':'正式总评按'+'＋'.join(parts)+'计算，汇总后四舍五入取整数。本轮'+str(comparison['compared_count'])+'条有效评分均与登记总评一致。',
        'source_refs':sorted(set(refs)),'compared_count':comparison['compared_count'],
        'origin':'registered_percentage_statement_and_all_valid_rows_verified'}

def verified_sections(source,facts,repair_host_candidates=False,repair_audit=None):
    """保留已验证改进段落；教学方式直接整理本课活动，不新增事实。"""
    record=load(source/'records'/'improvement_design.json')
    if not record.get('ok'):raise ValueError('改进方案生成记录尚未通过校验')
    from .structured import validate_schema
    validate_schema(record['answer'],record['output_schema'])
    candidates=improvement_candidates(facts)
    result=copy.deepcopy(record['answer'])
    for sid in ('6.1','6.2','7'):
        if len(result[sid])!=2 or set(result[sid])!=set(candidates[sid]):
            if not repair_host_candidates or facts.get('preparation',{}).get('origin')!='explicit_source_bound_course_design':
                raise ValueError('改进安排与当前课程活动不一致，需重新生成')
            # 旧任务两候选均须选择，模型仅决定顺序；只在此可证明的封闭选择任务迁移宿主文本。
            from training.data import actual_messages
            import json
            prompt=json.loads(actual_messages(record)[-1]['content'])
            old=record['output_schema']['properties'][sid]['items'].get('enum',[])
            if len(old)!=2 or len(set(old))!=2 or set(result[sid])!=set(old) or prompt.get('section_candidates',{}).get(sid)!=old:
                raise ValueError('旧记录不是来源绑定的两候选全选任务，不能迁移宿主文本')
            prior=list(result[sid]);result[sid]=[candidates[sid][old.index(p)] for p in prior]
            if repair_audit is not None:repair_audit[sid]={'before':prior,'after':result[sid],
                'proof':'旧schema两个枚举候选均被选择，保留模型顺序，仅修正宿主候选文本；新文字不冒称本次模型生成。'}
    methods=[]
    for m in facts.get('methods',[]):
        if not m.get('source_refs') or not m.get('description'):raise ValueError('缺少本课教学活动依据')
        body=re.sub(r'^\s*拟(?:定)?','',m['description']).strip()
        methods.append(method_label(facts,m)+'：'+method_body(body,method_label(facts,m)))
    if not methods:raise ValueError('缺少教学方式')
    result['2.4']=methods
    return result

def refresh(source_run,out,repair_host_candidates=False):
    source=path(source_run);out=path(out);original=load(source/'run_manifest.json')
    if original.get('offline') is not False:raise ValueError('只刷新真实课程任务')
    case=load(path(original['case_path']));runtime=load(path(original['runtime_path']))
    case.update(evaluation_profile=original['evaluation_profile']['path'],allow_estimates=True)
    template=load_template(runtime,case.get('report_type','course'))
    refs=reference_identity(runtime);fp,inputs=fingerprint(case,runtime,template,refs)
    if inputs!=original['inputs'] or template['hashes']!=original['template_hashes'] or refs!=original['reference_library'] or runtime!=original['runtime_config']:
        raise ValueError('原始输入、模板、参考库或运行配置变化，不能只重排旧结果')
    if case['id']!=original['case_id'] or digest(path(case['evaluation_profile']))!=original['evaluation_profile']['sha256']:
        raise ValueError('课程身份或基础评价配置变化')
    from .common import model_files
    actual={p.name:digest(p) for p in model_files(path(runtime['model']['expected_adapter_path']),True)}
    if actual!=runtime['model']['expected_adapter_sha256']:raise ValueError('Adapter身份变化')
    facts=load(source/'facts.json');calc=load(source/'calculations.json');compiled=load(source/'calculation_inputs.json')
    if not enabled(facts) or compiled['inputs'].get('report_presentation')!=facts.get('report_presentation'):raise ValueError('重排只支持显式课程报告展示模式')
    from .compute import calculate
    aggregate=load(source/'aggregate_evidence.json')
    from .evaluation_profiles import compile_plan
    if compile_plan(load(source/'evaluation_profile.json'),case,facts,aggregate)!=compiled['inputs']:raise ValueError('评价配置与计算输入不一致')
    recomputed=calculate(case,compiled['inputs'],load(source/'normalized_grades.json'),aggregate if aggregate['audits'] else None)
    for key in ('snapshot_sha256','registered_count','total','goals','points','assessments','assessment_points'):
        if recomputed[key]!=calc[key]:raise ValueError('缓存计算无法复核：'+key)
    from .evaluation_rigor import review
    calc['evaluation_rigor']=review(compiled['inputs'],calc,load(source/'evaluation_profile.json'))
    host_repair={};writing=verified_sections(source,facts,repair_host_candidates,host_repair)
    require_empty(out)
    manifest=copy.deepcopy(original)
    manifest.update(fingerprint=fp,status='running',refresh_origin={'run':str(source),'manifest_sha256':digest(source/'run_manifest.json')},
                    model_inference_this_run=False,refresh_scope='重用原始成绩、已验证计算和模型方案记录；教学方式按本课活动原文整理，重新生成表图和版式')
    save(out/'run_manifest.json',manifest)
    # 原始过程记录不改写，保留原运行与失败记录的定位。
    for file in source.iterdir():
        if file.name in ('run_manifest.json','report.docx','report.pdf','validation.json','pdf_validation.json','chart_manifest.json','table_manifest.json','assessment_summary_tables.json','sections.json','chart_text_validation.json','progress.json','progress.log'):continue
        if file.is_file():shutil.copy2(file,out/file.name)
        elif file.name=='records':shutil.copytree(file,out/file.name)
    save(out/'sections.json',writing)
    save(out/'calculations.json',calc)
    save(out/'evaluation_rigor.json',calc['evaluation_rigor'])
    save(out/'writing_refresh.json',{'methods':'deterministic_current_course_descriptions','improvements':'verified_existing_model_selection','record_origin':str(source/'records'/'improvement_design.json'),'new_model_requests':0})
    if host_repair:
        save(out/'host_candidate_repair.json',{'sections':host_repair,'source_record_sha256':digest(source/'records'/'improvement_design.json'),
            'new_model_requests':0,'origin':'deterministic_host_text_repair_not_finetuning_gain'})
    save(out/'presentation_names_refresh.json',{'methods':[{'method_id':m['id'],'label':method_label(facts,m),
        'original_record_presentation_name':m.get('presentation_name'),'source_name':m.get('name')} for m in facts['methods']],
        'scope':'最终显示名称；原teaching_names及模型记录保持原样，不将展示修正冒称模型输出。'})
    view=copy.deepcopy(compiled['inputs'])
    comparison_file=path(case['compact_material_preparation']['profile_file']).parent/'registered_component_comparison.json' if case.get('compact_material_preparation') else None
    if comparison_file is not None and comparison_file.is_file():
        rule=verified_total_rule_description(facts,load(comparison_file))
        if rule:
            view['metadata']['total_rule_text']=rule['text']
            view['metadata']['grade_formula_text']=rule['text']
            save(out/'render_metadata_enrichment.json',{'verified_total_rule':rule,'numeric_inputs_changed':False,
                'scope':'仅渲染补充已知规则说明，原始facts、计算输入和数值缓存保留；原登记百分比及全样本核算均为必要条件。'})
    data=tables.build(view,calc,template['contract']);save(out/'table_manifest.json',data)
    save(out/'assessment_summary_tables.json',tables.assessment_summaries(view,calc))
    figures=charts.generate(view,calc,out,path(runtime['chart_font_path']) if runtime.get('chart_font_path') else None)
    rendered=render.generate(template,view,calc,data,figures,writing,out/'report.docx',draft=True)
    d=Document(out/'report.docx');paras=[p.text for p in d.paragraphs]
    if '【' in '\n'.join(paras):raise ValueError('仍有占位符')
    audit=charts.validate_captions(paras,figures);save(out/'chart_text_validation.json',audit)
    if len(data)!=11 or any(j['status']!='filled' for j in figures):raise ValueError('表图不完整')
    missing=calc.get('issues',[])+rendered['missing_fields']
    if not facts.get('review',{}).get('approved'):missing+=['抽取事实尚未经人工确认']
    validation={'data_snapshot':calc['snapshot_sha256'],'tables_required':11,'table_types_present':sorted(data),
                'charts_filled':len(figures),'charts_missing':0,'missing_items':sorted(set(missing)),
                'content_status':'draft_estimated','report_mode':manifest['report_mode'],'contains_estimates':True,
                'visual_review':'pending','model_used':True,'model_inference_this_run':False,'offline_synthetic_only':False,'chart_names_consistent':True}
    save(out/'validation.json',validation)
    result=pdf.convert(out/'report.docx',runtime.get('libreoffice'));save(out/'pdf_validation.json',result)
    manifest.update(status='draft_ready',word_sha256=digest(out/'report.docx'),pdf_sha256=digest(out/'report.pdf'),pdf_status='converted',data_snapshot=calc['snapshot_sha256'])
    save(out/'run_manifest.json',manifest)
    print({'status':'draft_ready','out':str(out),'new_model_requests':0},flush=True)
    return manifest
