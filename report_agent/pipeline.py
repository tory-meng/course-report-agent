"""资料→事实→母版→计算/模型填充→核验→Word→PDF，可恢复同配置任务。"""
import importlib.metadata
import json
from pathlib import Path
from .common import ROOT,path,load,save,digest,signature,require_empty
from .readers import ingest,evidence_index
from .facts import extract,validate_facts
from .grades import parse_grades
from .compute import calculate
from .client import Client
from .progress import Progress
from .writing import write_sections
from . import tables,charts,render,pdf
from .reference_db import reference_identity,attach_resolver
from .decision_policy import enabled as decisions_enabled,prepare_review,source_rank

def load_template(runtime,report_type):
    registry=load(path(runtime['template_registry']));entry=registry['templates'].get(report_type)
    if not entry or not entry.get('enabled'):raise ValueError('该类型模板尚未接入：'+report_type+'；请提供已确认母版，不能回退到课程模板')
    cp=path(entry['contract']);contract=load(cp);root=cp.parent
    for key,rel in contract['assets'].items():
        file=root/rel
        if not file.is_file() or digest(file)!=contract['asset_sha256'][key]:raise ValueError('模板资源缺失或内容变动：'+str(file)+'；需更新版本及绑定后再运行')
    if contract['template_id']!=entry['template_id']:raise ValueError('注册表与contract的template_id不一致')
    return {'contract':contract,'word_path':root/contract['assets']['word_template'],
            'prompt':(root/contract['assets']['system_prompt']).read_text(encoding='utf-8'),
            'hashes':{'contract':digest(cp),**contract['asset_sha256']}}

def inspect(case_path,out):
    case=load(path(case_path));sources=ingest(case);save(path(out),sources)
    for s in sources:
        print(s['id'],s['role'],len(s['blocks']),'blocks')
        for sh in s['sheets']:print('  sheet=',sh['name'],'rows=',len(sh['rows']),'formula_missing=',sh['formula_cells'])
    print('检查结果：',path(out))

def fingerprint(case,runtime,template,reference=None):
    inputs={s['id']:digest(path(s['path'])) for s in case['sources']}
    if case.get('facts_path'):inputs['reviewed_facts']=digest(path(case['facts_path']))
    code={str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'report_agent').glob('*.py'))}
    code.update({str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'prompts').glob('*.txt'))})
    payload={'case':case,'runtime':runtime,'template':template['hashes'],'inputs':inputs,'code':code,'reference_library':reference}
    if case.get('evaluation_profile'):payload['evaluation_profile_sha256']=digest(path(case['evaluation_profile']))
    return signature(payload),inputs

def run(case_path,runtime_path,out,resume=False,offline=False,probe_extraction=False,interactive=False,reuse_extraction_from=None,evaluation_profile=None,allow_estimates=False):
    case=load(path(case_path));runtime=load(path(runtime_path));out=path(out)
    if not case.get('id') or not case.get('course_name'):raise ValueError('case需要id、course_name')
    if probe_extraction and (offline or case.get('facts_path')):raise ValueError('--probe-extraction仅用于在线抽取；不能与offline或facts_path一起使用')
    if offline and not case.get('synthetic_demo'):raise ValueError('--offline仅供显式synthetic_demo案例，不能冒充真实模型生成')
    if reuse_extraction_from and offline:raise ValueError('--reuse-extraction-from仅用于在线任务')
    from .grade_preflight import case_preflight,stop_without_model
    preflight=case_preflight(case)
    if preflight['status']!='passed':return stop_without_model(case,out,preflight,resume,runtime_path)
    if resume and (out/'run_manifest.json').is_file():
        prior=load(out/'run_manifest.json')
        if prior.get('status','').startswith('blocked_') and not prior.get('fingerprint'):
            raise ValueError('成绩资料已补齐；原目录是停止提示记录，请使用新的输出目录开始生成')
    template=load_template(runtime,case.get('report_type','course'));template['offline']=offline
    refs=None if offline else reference_identity(runtime)
    profile=None
    design=case.get('evaluation_design',{})
    if not evaluation_profile and design.get('enabled') and design.get('auto_apply'):
        evaluation_profile=design['profile'];allow_estimates=True
    if evaluation_profile:
        if offline:raise ValueError('评价配置试算不能使用离线演示模式')
        if not allow_estimates:raise ValueError('候选配置含代理估计；仅在明确加--allow-estimates时生成试算草稿')
        from .evaluation_profiles import validate_profile
        profile=load(path(evaluation_profile));case['evaluation_profile']=str(path(evaluation_profile));case['allow_estimates']=True
        validate_profile(profile,case,template,refs,{s['id']:digest(path(s['path'])) for s in case['sources']})
    elif allow_estimates:raise ValueError('--allow-estimates需要显式指定--evaluation-profile')
    fp,inputs=fingerprint(case,runtime,template,refs);manifest_path=out/'run_manifest.json'
    old=None
    if resume:
        old=load(manifest_path)
        if old.get('status')=='extraction_probe_review_required' and not probe_extraction:
            raise ValueError('探测批次尚未通过事实检查，请先核对probe_validation.json，不继续消耗完整抽取请求')
        if old['fingerprint']!=fp or old['offline']!=offline:raise ValueError('代码/配置/输入/模板变化，不能复用旧结果；请换输出目录')
    else:require_empty(out)
    if old and old.get('reuse_extraction_from'):
        origin=old['reuse_extraction_from']
        if reuse_extraction_from and str(path(reuse_extraction_from))!=origin:raise ValueError('续跑不能更换抽取复用来源')
        reuse_extraction_from=origin
    manifest={'case_id':case['id'],'group_id':case.get('group_id',case['id']),'fingerprint':fp,'inputs':inputs,
       'template_id':template['contract']['template_id'],'template_hashes':template['hashes'],'offline':offline,'synthetic_demo':bool(case.get('synthetic_demo')),
       'status':'running','runtime_path':str(path(runtime_path)),'runtime_config':runtime,'case_path':str(path(case_path))}
    if reuse_extraction_from:manifest['reuse_extraction_from']=str(path(reuse_extraction_from))
    manifest['report_mode']='scenario_estimate' if profile else 'evidence_based'
    if profile:manifest['evaluation_profile']={'path':str(path(evaluation_profile)),'sha256':digest(path(evaluation_profile)),'id':profile['id'],'status':'proposed'}
    save(manifest_path,manifest)
    progress=Progress(out)
    try:
        progress.stage(1,'检查关键成绩单')
        save(out/'grade_preflight.json',preflight)
        progress.note('关键成绩单检查通过；继续读取教学材料并核对课程身份。')
        sources=ingest(case,progress)
        score_formula_report=None
        if case.get('score_formula_policy',{}).get('enabled'):
            from .score_formulas import inventory,enforce
            score_formula_report=inventory(case,sources,out)
            if case.get('grade_tables'):enforce(case['grade_tables'],score_formula_report)
        from .material_scope import inspect as inspect_material_scope
        material_scope=inspect_material_scope(case,sources);save(out/'material_version_decisions.json',material_scope)
        from .grade_layout import diagnostic
        save(out/'grade_layout_diagnostic.json',diagnostic(sources))
        if decisions_enabled(case):
            sources.sort(key=lambda s:-source_rank(s))
            progress.note('已启用成绩资料优先与重大问题交互；先处理正式成绩表、记分册，考勤表不作为总评来源')
        formula_results=[]
        if case.get('grade_formula_inference'):
            from .grade_formulas import audit
            formula_results=audit(case,sources,out)
            progress.note('已读取本轮记分册真实公式并逐行独立核验；不按数值拟合猜测权重')
        save(out/'source_manifest.json',sources)
        progress.stage(2,'核对模型服务与Adapter')
        client=None if offline else Client(runtime['model'])
        if client:
            client.progress=progress;client.require_structured()
            if refs:
                # 本轮固定快照，运行过程中即使整理库发生变化，也不改变本轮检索依据。
                attach_resolver(client,refs,out,case.get('report_type','course'))
                manifest['reference_library']=refs
                progress.note('已启用本地参考资料库：按模板字段主动检索、歧义复查与方案推荐；使用本轮固定快照')
        if old and not offline and old.get('model') and old['model']!=client.identity:
            raise ValueError('服务模型身份与原任务不同；请恢复原服务或使用新输出目录')
        manifest['model']={'mode':'offline_fixture'} if offline else client.identity
        save(manifest_path,manifest)
        if reuse_extraction_from:
            from .extraction_reuse import ExtractionReuse
            client.extraction_reuse=ExtractionReuse(path(reuse_extraction_from),manifest,out)
            progress.note('已核对旧任务输入、模板、模型与参考库；仅复用提示词/schema完全一致且通过当前校验的抽取记录')
        progress.stage(3,'按证据分批抽取事实')
        if case.get('facts_path'):
            facts=load(path(case['facts_path']))
            if not decisions_enabled(case):validate_facts(facts,sources,case['course_name'])
        elif client:
            facts=extract(case,sources,client,out,template['contract']['template_id'],progress,max_batches=2 if probe_extraction else None,template_contract=template['contract'],defer_validation=decisions_enabled(case))
        else:raise ValueError('离线演示需要明确facts_path')
        if case.get('grade_mapping_policy',{}).get('mode')=='auto_select_v1':
            from .grade_selection import select
            try:
                mappings,selection=select(case,sources,facts)
            except ValueError as exc:
                save(out/'grade_selection.json',{'status':'review_required','reason':str(exc),'source_hashes':{s['id']:s['sha256'] for s in sources}})
                raise
            case['grade_tables']=mappings;save(out/'grade_selection.json',selection)
            if score_formula_report:
                from .score_formulas import enforce
                enforce(mappings,score_formula_report)
            progress.note('已按课程身份、表头与学号自动选择总评、补充分项及空白名册；裁决详见grade_selection.json')
        if formula_results:
            from .grade_formulas import apply_verified
            apply_verified(case,facts,sources,formula_results)
            save(out/'facts.formula_enriched.json',facts)
        if probe_extraction:
            probe=load(out/'probe_validation.json')
            manifest.update(status='extraction_probe_review_required' if probe['status']!='passed' else 'extraction_probe_ready');save(manifest_path,manifest)
            message='探测批次存在事实错误、漏抽或课程身份尚未确认，请先查看probe_validation.json。' if probe['status']!='passed' else '探测批次的结构、来源及覆盖检查通过；尚未生成报告。使用相同命令去掉--probe-extraction并加--resume继续。'
            progress.finish(manifest['status'],message)
            return manifest
        if facts.get('extraction_quality',{}).get('status')=='blocked':
            manifest.update(status='extraction_quality_blocked',error='有未通过的事实批次；查看extraction_coverage.json，不进入生成或训练')
            save(out/'facts.json',facts);save(manifest_path,manifest)
            progress.finish(manifest['status'],manifest['error']);return manifest
        if decisions_enabled(case):
            from .human_review import collect_answers,apply_answers
            audit_rounds=[]
            # 每轮上下文绑定：先成绩来源，再针对该来源实际出现的状态确认。
            for round_number in range(1,4):
                review_dir=out/'review'/f'round_{round_number:02}'
                review_dir.mkdir(parents=True,exist_ok=True)
                questions,grade_error=prepare_review(case,facts,sources,client,review_dir)
                save(out/'user_questions.json',questions)
                audit_rounds.append(load(review_dir/'decision_audit.json'))
                save(out/'decision_audit.json',{'rounds':audit_rounds,'remaining_conflicts':facts.get('conflicts',[])})
                if not questions['questions']:
                    if grade_error:
                        manifest.update(status='generation_blocked',error=grade_error);save(manifest_path,manifest)
                        save(out/'case.effective.json',case);save(out/'facts.json',facts)
                        progress.finish(manifest['status'],grade_error);return manifest
                    break
                progress.note(f'仍有{len(questions["questions"])}项影响计算或结论的问题需确认；普通缺项已记录，不提问')
                answer_file=review_dir/'user_answers.json'
                answers=collect_answers(questions,answer_file,load(answer_file) if answer_file.is_file() else None,interactive)
                chosen=apply_answers(questions,answers,case,facts,sources,review_dir)
                if answers.get('session_status')=='paused' or len(chosen)<len(questions['questions']):
                    manifest.update(status='awaiting_user',questions_path=str(review_dir/'user_questions.json'),answers_path=str(answer_file))
                    save(out/'facts.json',facts);save(out/'case.effective.json',case);save(manifest_path,manifest)
                    progress.finish('awaiting_user','已保存抽取结果和选择。原命令加 --resume --interactive 继续，无需重新请求已完成批次。')
                    return manifest
            validate_facts(facts,sources,case['course_name'])
            save(out/'case.effective.json',case);save(out/'source_manifest.effective.json',sources)
        from .teaching_presentation import prepare as prepare_names,scope_audit
        save(out/'teaching_names.json',prepare_names(facts))
        scope=scope_audit(case,sources,facts);save(out/'source_scope_audit.json',scope)
        if scope['issues']:
            if case.get('autonomous_policy',{}).get('latest_applicable_materials'):
                scope['decision']={'origin':'user_authorized_latest_applicable_version','preserve_registered_grade_term':True,
                                   'scope':'教学方案采用最新适用材料版本，原始成绩与其学期不改写'}
                save(out/'source_scope_audit.json',scope)
                facts.setdefault('data_quality_issues',[]).append('材料版本差异已按授权处理：教学方案优先最新适用版本，成绩保留原登记学期；详见版本裁决记录。')
            else:facts.setdefault('data_quality_issues',[]).append('部分课程材料标注学年与本轮成绩册不同，教学活动及执行版本的适用性待核验。')
        if client and case.get('teaching_reference_policy',{}).get('enabled'):
            from .teaching_reference import prepare
            prepare(case,facts,client,out)
        index=evidence_index(sources)
        if not case.get('evaluation_policy') and facts['metadata'].get('evaluation_policy'):
            case['evaluation_policy']=facts['metadata']['evaluation_policy']
        if any(r not in index for r in case.get('evaluation_policy',{}).get('source_refs',[])):raise ValueError('评价制度来源不存在')
        if case.get('report_presentation'):facts['report_presentation']=case['report_presentation']
        save(out/'facts.json',facts)
        progress.stage(4,'标准化成绩并计算统计')
        grades=parse_grades(case,sources,facts);save(out/'normalized_grades.json',grades)
        save(out/'grade_layout_diagnostic.json',diagnostic(sources,facts.get('items',[])))
        from .aggregate_evidence import audit_sources
        aggregate=audit_sources(case,sources,facts,grades)
        save(out/'aggregate_evidence.json',aggregate)
        if case.get('evaluation_design',{}).get('enabled'):
            if not profile or not client:raise ValueError('目标方案构造需要评价配置和在线模型')
            from .course_design import infer_grade_scheme,design_goals
            scheme=infer_grade_scheme(case,facts,grades,sources,out)
            profile=design_goals(case,facts,profile,scheme,client,out)
            save(out/'facts.json',facts)
        view_facts=facts
        if profile:
            from .evaluation_profiles import compile_plan
            view_facts=compile_plan(profile,case,facts,aggregate)
            save(out/'evaluation_profile.json',profile)
            save(out/'calculation_inputs.json',{'scope':'候选计算模型输入，不是已确认事实或训练标签','evaluation_context':view_facts['evaluation_context'],'inputs':view_facts})
            progress.note('已按显式评价方案计算；来源、适用范围和假设保留在审计记录中')
        calc=calculate(case,view_facts,grades,aggregate if aggregate['audits'] else None)
        from .course_presentation import enabled as course_style,student_records
        if course_style(view_facts):
            calc['student_records']=student_records(grades)
            calc['student_labels']={s['key']:s['name'] for s in calc['student_records']}
            save(out/'student_grade_table.json',calc['student_records'])
        from .evaluation_rigor import review as review_rigor
        calc['evaluation_rigor']=review_rigor(view_facts,calc,profile)
        save(out/'evaluation_rigor.json',calc['evaluation_rigor'])
        calc['issues']+=tables.missing_inputs(view_facts);save(out/'calculations.json',calc)
        from .report_analysis import build as analyze
        save(out/'report_analysis.json',analyze(calc))
        save(out/'report_scope.json',calc['report_scope'])
        progress.stage(5,'填写启用章节')
        writing,writing_issues=write_sections(case,facts,calc,template,client,out,offline,progress)
        save(out/'sections.json',writing)
        progress.stage(6,'生成表格和图表')
        table_data=tables.build(view_facts,calc,template['contract']);save(out/'table_manifest.json',table_data)
        save(out/'assessment_summary_tables.json',tables.assessment_summaries(view_facts,calc))
        figure_data=charts.generate(view_facts,calc,out,path(runtime['chart_font_path']) if runtime.get('chart_font_path') else None,progress)
        progress.stage(7,'按模板生成Word')
        rendered=render.generate(template,view_facts,calc,table_data,figure_data,writing,out/'report.docx',draft=True)
        progress.stage(8,'检查板块、表型和占位符')
        missing=calc['issues']+writing_issues+rendered['missing_fields']
        if not facts.get('review',{}).get('approved') or not facts.get('review',{}).get('reviewer'):missing.append('抽取事实尚未经人工确认')
        if len(table_data)!=11:raise RuntimeError('必选表型不完整')
        from docx import Document
        doc=Document(out/'report.docx');text='\n'.join(p.text for p in doc.paragraphs)
        caption_audit=charts.validate_captions([p.text for p in doc.paragraphs],figure_data)
        save(out/'chart_text_validation.json',caption_audit)
        text+='\n'+'\n'.join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        if '【' in text:raise RuntimeError('Word残留未替换占位符')
        if any(any(r.font.strike for r in p.runs) for p in doc.paragraphs):raise RuntimeError('Word残留停用删除线段落')
        validation={'data_snapshot':calc['snapshot_sha256'],'tables_required':11,'table_types_present':sorted(table_data),
                    'charts_filled':sum(c['status']=='filled' for c in figure_data),'charts_missing':sum(c['status']!='filled' for c in figure_data),
                    'missing_items':sorted(set(missing)),'content_status':'draft_estimated' if profile else ('draft_missing_data' if missing else 'ready_for_review'),
                    'report_mode':manifest['report_mode'],'contains_estimates':bool(profile),
                    'visual_review':'pending','model_used':not offline,'offline_synthetic_only':offline}
        validation['chart_names_consistent']=caption_audit['status']=='passed'
        save(out/'validation.json',validation)
        manifest.update(word_sha256=digest(out/'report.docx'),pdf_status='pending',data_snapshot=calc['snapshot_sha256'])
        save(manifest_path,manifest)
        progress.stage(9,'Word转换PDF并核验')
        pdf_result=pdf.convert(out/'report.docx',runtime.get('libreoffice'));save(out/'pdf_validation.json',pdf_result)
        manifest.update(status='draft_ready' if missing or offline else 'generated_review_required',word_sha256=digest(out/'report.docx'),
                        pdf_sha256=digest(out/'report.pdf'),pdf_status='converted',data_snapshot=calc['snapshot_sha256'])
        save(manifest_path,manifest);print(json.dumps({'status':manifest['status'],'out':str(out),'missing_count':len(set(missing))},ensure_ascii=False),flush=True)
        progress.finish(manifest['status'],'流程完成；请查看validation.json并人工审阅Word/PDF')
        return manifest
    except KeyboardInterrupt:
        manifest.update(status='interrupted',error='用户中断；服务端可能仍在推理');save(manifest_path,manifest)
        progress.finish('interrupted','已中断；确认服务空闲后再续跑');raise
    except Exception as e:
        manifest.update(status='failed',error=f'{type(e).__name__}: {e}');save(manifest_path,manifest)
        progress.finish('failed',f'失败于阶段 {progress.state["stage"]}/9（{progress.state["stage_name"]}）：{e}');raise

def retry_pdf(run_dir,runtime_path):
    """仅转换本任务已完成内容核验的Word；保持草稿与审核状态。"""
    out=path(run_dir);runtime=load(path(runtime_path));manifest=load(out/'run_manifest.json')
    if manifest.get('pdf_status') not in ('pending','converted') or not manifest.get('word_sha256'):
        raise ValueError('该任务尚未完成Word内容核验，不能直接标记PDF完成')
    if digest(out/'report.docx')!=manifest['word_sha256']:
        raise ValueError('Word已在生成后改动，请重新核验；不把手工改稿标记为原任务输出')
    validation=load(out/'validation.json');result=pdf.convert(out/'report.docx',runtime.get('libreoffice'))
    save(out/'pdf_validation.json',result)
    manifest.update(pdf_status='converted',pdf_sha256=result['sha256'],
                    status='draft_ready' if validation['missing_items'] or manifest['offline'] else 'generated_review_required')
    manifest.pop('error',None);save(out/'run_manifest.json',manifest);return result
