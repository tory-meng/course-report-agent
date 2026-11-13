"""只读清点课程资料、核验成绩并安排批次；原目录不重命名、不搬动。"""
import csv
import hashlib
import io
import json
import re
import shutil
import shlex
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path,PurePosixPath
from zoneinfo import ZoneInfo
from .common import ROOT,save,load,digest,signature
from .readers import excel_sheets,read_source
from .grade_preflight import inspect_source,word_sheets

SHEETS={'.xls','.xlsx','.csv','.tsv'}
ARCHIVES={'.zip','.rar','.7z'}
DOCS={'.doc','.docx','.pdf'}
MAX_MEMBER=32*1024*1024
MAX_NESTED=256*1024*1024
STATE_NAMES={'ready_full':'第一批：完整材料核验','ready_minimal':'第二批：仅成绩或材料较少','ready_layout':'第三批：特殊成绩格式适配',
    'blocked_empty_grades':'停止：成绩为空','blocked_missing_grades':'停止：缺关键成绩单','blocked_grade_semantics':'停止：成绩列含义未明',
    'blocked_identity':'停止：课程身份冲突','blocked_unreadable':'停止：候选成绩读取不完整','deferred_graduation':'暂不处理：毕业设计','reference_only':'参考报告样本，不作为本轮成绩','unassigned_materials':'未归组：新增散放文件'}

def family_name(name):
    s=re.sub(r'^20\d{2}\s*[-－]\s*[12]\s*','',name)
    for word,family in [('数字图像','数字图像处理'),('数字逻辑','数字逻辑'),('提示词','科技文献检索与提示词工程'),('研究生专业英语','研究生专业英语'),('专业英语','专业英语'),('沟通','写作与沟通'),('写作','写作与沟通'),('创新创业','创新创业实践'),('专业感知','专业感知与实践'),('网络项目','网络工程项目设计'),('网络课设','计算机网络课程设计'),('计算机网络','计算机网络'),('贯通班','综合训练'),('综合训练','综合训练'),('毕业实习','毕业实习'),('AIGC','AIGC'),('人工智能','人工智能')]:
        if word in s:return family
    return re.sub(r'\s+','',s)

def scope(name):
    if re.search(r'毕业设计|毕业论文|毕设',name):return 'deferred_graduation'
    if '达成报告训练样本' in name:return 'reference_only'
    return 'course'

def role(relative):
    p=Path(relative);name=p.name;ext=p.suffix.lower()
    if name.startswith('~$'):return 'temporary_lock'
    if ext in SHEETS:
        if re.search(r'考勤|签到|点名',name):return 'attendance'
        if re.search(r'达成|评价表|试卷分析|成绩分析',name):return 'evaluation_summary'
        if re.search(r'获奖|竞赛名单|投保人',name):return 'student_support'
        return 'grade_candidate'
    if ext in ARCHIVES:return 'archive'
    if re.search(r'达成.*报告|达成情况|达成分析',name):return 'completed_report'
    if ext in DOCS and re.search(r'成绩单|成绩登记|记分册',name):return 'grade_candidate'
    if ext in DOCS and re.search(r'大纲|教案|日历|考核|课程教学小结|教学总结|教学进度|课程档案|指导书|评定标准',name):return 'teaching'
    if ext in {'.ppt','.pptx'}:return 'lecture'
    if ext in {'.mp4','.mov','.mkv','.png','.jpg','.jpeg','.gif'}:return 'media'
    if ext in DOCS:return 'other_document' if len(p.parts)==1 else 'student_work'
    return 'code_or_other'

def _zip_display(name):
    try:return name.encode('cp437').decode('gb18030')
    except (UnicodeError,LookupError):return name

def archive_index(p):
    """只列成员；只提取小型成绩表，不展开学生代码、视频及整包作业。"""
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            return [{'member':i.filename,'display':i.filename if i.flag_bits&0x800 else _zip_display(i.filename),'size':i.file_size,'directory':i.is_dir()} for i in z.infolist()]
    exe=shutil.which('7z')
    if not exe:raise RuntimeError('缺少7z，压缩包检查未完成')
    proc=subprocess.run([exe,'l','-slt','-ba','--',str(p)],capture_output=True,text=True,errors='replace',timeout=45)
    if proc.returncode:raise ValueError(proc.stderr.strip()[-250:] or '压缩包不能读取')
    records=[]
    for part in re.split(r'\r?\n\s*\r?\n',proc.stdout):
        values=dict(line.split(' = ',1) for line in part.splitlines() if ' = ' in line)
        if 'Path' not in values:continue
        records.append({'member':values['Path'],'display':values['Path'],'size':int(values.get('Size','0')),'directory':values.get('Folder')=='+'})
    return records

def archive_member(p,item,limit=MAX_MEMBER):
    name=item['member'];parts=PurePosixPath(name.replace('\\','/')).parts
    if '..' in parts or name.startswith(('/','\\')):raise ValueError('压缩包成员路径无效')
    if item['size']>limit:raise ValueError('压缩成员超过'+str(limit//1024//1024)+'MB，单列检查')
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            with z.open(name) as f:data=f.read(limit+1)
    else:
        proc=subprocess.run([shutil.which('7z'),'x','-so','-y','-spd',str(p),'--',name],capture_output=True,timeout=45)
        if proc.returncode:
            fallback=shutil.which('bsdtar')
            if not fallback:raise ValueError('候选成绩表解包失败：'+proc.stderr.decode(errors='replace')[-200:])
            proc=subprocess.run([fallback,'-xOf',str(p),'--',name],capture_output=True,timeout=45)
            if proc.returncode:raise ValueError('候选成绩表解包失败：'+proc.stderr.decode(errors='replace')[-200:])
        data=proc.stdout
    if len(data)>limit:raise ValueError('压缩成员超过大小限制')
    return data

def _known_cases():
    result={};overrides={}
    for p in sorted((ROOT/'cases').glob('course_*.json')):
        case=load(p)
        for sid,rule in case.get('grade_mapping_policy',{}).get('identity_overrides',{}).items():
            if rule.get('confirmation') and rule.get('sha256'):overrides[rule['sha256']]={'case_id':case['id'],**rule}
        for s in case.get('sources',[]):
            file=Path(s['path'])
            if s.get('kind')=='grades' and file.exists():result.setdefault(digest(file),[]).append(case['id'])
    return result,overrides

def grade_file(p,cache,origin=None):
    sha=digest(p);parser=signature({'files':{name:digest(Path(__file__).parent/name) for name in ('grade_preflight.py','grade_layout.py','readers.py')},'filename':p.name})
    key=cache/(sha+'_'+parser[:12]+'.json')
    if key.exists():result=load(key)
    else:
        source={'id':'file_'+sha[:16],'path':str(p),'role':'current','kind':'grades','sha256':sha}
        if p.suffix.lower() in SHEETS:source['sheets']=excel_sheets(p)
        else:
            source=read_source(source);source['sheets']=word_sheets(source)
        result=inspect_source(source);save(key,result)
    result={**result,'path':str(p)}
    if origin:result['origin']=origin
    return result

def score_count(g):return max([c['scored_count'] for t in g['tables'] for c in t['total_columns']]+[0])

def course_matches(family,names):
    return any(family in n for n in names) or (family=='计算机网络课程设计' and any(re.search(r'计算机网络.*课程设计',n) for n in names)) or (family=='研究生专业英语' and any('专业英语' in n for n in names))

def nested_grades(package,cache):
    """未找到关键成绩时，继续检查嵌套包；达到边界明确记录，不宣称已查完。"""
    seen=set();count=0
    def visit(p,outer,chain,depth):
        nonlocal count
        for member in archive_index(p):
            if member['directory']:continue
            if Path(member['display'].replace('\\','/')).name.startswith('~$'):continue
            ext=Path(member['display']).suffix.lower()
            if ext not in ARCHIVES|SHEETS:continue
            member_chain=chain+[member['display']]
            origin={'archive':outer,'member':' / '.join(member_chain),'member_chain':member_chain}
            if depth and ext in SHEETS:
                data=archive_member(p,member);sha=hashlib.sha256(data).hexdigest();target=cache/'members'/sha;target.mkdir(exist_ok=True);file=target/Path(member['display'].replace('\\','/')).name
                if not file.exists():file.write_bytes(data)
                g=grade_file(file,cache/'grades',origin);g['catalog_role']=role(member['display']);g['attendance']=bool(re.search(r'考勤|点名|签到',member['display']));package['grade_sources'].append(g)
            elif ext in ARCHIVES:
                if depth>=3 or member['size']>MAX_NESTED:
                    package['read_errors'].append({**origin,'kind':'nested_archive_limit','error':'嵌套包超过深度或256MB边界，尚未完整检查'});continue
                data=archive_member(p,member,MAX_NESTED);sha=hashlib.sha256(data).hexdigest()
                if sha in seen:continue
                seen.add(sha);file=cache/'members'/(sha+ext)
                if not file.exists():file.write_bytes(data)
                count+=1
                try:visit(file,outer,member_chain,depth+1)
                except Exception as exc:package['read_errors'].append({**origin,'kind':'nested_archive','error':str(exc)})
    for archive in package['archives']:
        if archive.get('nested_archive_count'):
            try:visit(Path(archive['path']),archive['path'],[],0)
            except Exception as exc:package['read_errors'].append({'path':archive['path'],'kind':'nested_archive','error':str(exc)})
    package['nested_archives_inspected']=count

def scan(root,out,previous=None,cache=None,inspect_archives=True):
    root=Path(root).resolve();out=Path(out).resolve()
    if not root.is_dir():raise FileNotFoundError('课程资料目录不存在：'+str(root))
    if out.is_relative_to(root):raise ValueError('输出清单必须放在原始资料目录之外')
    out.mkdir(parents=True,exist_ok=True)
    if (out/'catalog.json').exists():raise ValueError('清单已存在，请使用新的输出目录保留历史扫描')
    cache=Path(cache or ROOT/'outputs/material_catalog_cache').resolve()
    if cache.is_relative_to(root):raise ValueError('解析缓存必须放在原始资料目录之外')
    cache.mkdir(parents=True,exist_ok=True)
    (cache/'grades').mkdir(exist_ok=True);(cache/'members').mkdir(exist_ok=True)
    known,overrides=_known_cases();files=sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink())
    if previous and Path(previous).exists() and load(previous)['root']!=str(root):raise ValueError('增量扫描根目录与旧清单不同')
    inventory=[];packages=[]
    groups=[(d.name,d,[p for p in files if p.is_relative_to(d)]) for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())]
    loose=[p for p in files if p.parent==root]
    if loose:groups.append(('未归组散放文件',root,loose))
    for folder_name,folder,package_files in groups:
        package={'id':'pkg_'+hashlib.sha256(folder_name.encode()).hexdigest()[:12],'folder':folder_name,'path':str(folder),'scope':'unassigned_materials' if folder==root else scope(folder_name),
                 'family':family_name(folder_name),'file_count':len(package_files),'size_bytes':sum(p.stat().st_size for p in package_files),
                 'files':[],'grade_sources':[],'archives':[],'read_errors':[],'notes':[]}
        print('扫描 '+folder_name,flush=True)
        for p in package_files:
            relative=str(p.relative_to(folder));entry={'path':str(p),'relative_path':str(p.relative_to(root)),'package_id':package['id'],'role':role(relative),'extension':p.suffix.lower(),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
            inventory.append(entry);package['files'].append(entry)
            if package['scope']!='course':continue
            if entry['role']!='temporary_lock' and (p.suffix.lower() in SHEETS or entry['role']=='grade_candidate'):
                try:
                    g=grade_file(p,cache/'grades');g['catalog_role']=entry['role'];package['grade_sources'].append(g);entry['sha256']=g['sha256']
                except Exception as exc:package['read_errors'].append({'path':str(p),'kind':'grade','error':str(exc)})
            if p.suffix.lower() in ARCHIVES and inspect_archives:
                arc={'path':str(p),'status':'indexed','spreadsheet_members':[],'nested_archive_count':0}
                try:
                    members=archive_index(p);arc['member_count']=len(members)
                    arc['nested_archive_count']=sum(Path(m['display']).suffix.lower() in ARCHIVES for m in members if not m['directory'])
                    choices=[m for m in members if not m['directory'] and not Path(m['display'].replace('\\','/')).name.startswith('~$') and (Path(m['display']).suffix.lower() in SHEETS or (Path(m['display']).suffix.lower() in {'.doc','.docx'} and re.search(r'成绩单|记分册|成绩登记',m['display'])))]
                    for member in choices:
                        origin={'archive':str(p),'member':member['display'],'exact_member':member['member']}
                        arc['spreadsheet_members'].append(origin)
                        try:
                            data=archive_member(p,member);sha=hashlib.sha256(data).hexdigest();target=cache/'members'/sha;target.mkdir(exist_ok=True)
                            saved=target/Path(member['display'].replace('\\','/')).name
                            if not saved.exists():saved.write_bytes(data)
                            g=grade_file(saved,cache/'grades',origin);g['attendance']=bool(re.search(r'考勤|签到|点名',member['display']))
                            g['catalog_role']=role(member['display'])
                            package['grade_sources'].append(g)
                        except Exception as exc:package['read_errors'].append({**origin,'kind':'archive_grade','error':str(exc)})
                except Exception as exc:
                    arc.update(status='unreadable',error=str(exc));package['read_errors'].append({'path':str(p),'kind':'archive','error':str(exc)})
                package['archives'].append(arc)
        package['source_signature']=signature([(f['relative_path'],f['size'],f['mtime_ns']) for f in package['files']])
        if package['scope']!='course':
            package['status']=package['scope'];package['reason']=STATE_NAMES[package['scope']];packages.append(package);continue
        if not any(score_count(g) and not g['attendance'] for g in package['grade_sources']) and inspect_archives:nested_grades(package,cache)
        grade_sources=[g for g in package['grade_sources'] if not g['attendance'] and g.get('catalog_role') not in ('student_support','evaluation_summary')]
        scored=[g for g in grade_sources if score_count(g)]
        # 相同内容只列一次有效候选；保留全部路径，不相加重复学生人数。
        unique={g['sha256']:g for g in scored};scored=list(unique.values())
        scored.sort(key=lambda g:(-bool(course_matches(package['family'],g['written_courses']) or g['sha256'] in overrides),-bool(g['written_courses']),-bool(re.search(r'成绩单|成绩登记',Path(g.get('origin',{}).get('member',g['path'])).name)),g['path']))
        package['scored_candidates']=scored;package['existing_cases']=sorted({c for g in scored for c in known.get(g['sha256'],[])})
        package['family_group_id']='family_'+hashlib.sha256(package['family'].encode()).hexdigest()[:12]
        teaching=[f for f in package['files'] if f['role']=='teaching'];package['teaching_files']=teaching
        package['requires_mapping_adapter']=any(t.get('normalized_english_headers') or t.get('requires_name_only_adapter') or t.get('requires_headerless_adapter') for g in scored for t in g['tables'])
        if scored:
            main=scored[0];package['primary_grade']=main;package['scored_count']=score_count(main)
            package['registered_count']=max([t['registered_count'] for t in main['tables']]+[0])
            names=[n for g in scored for n in g['written_courses']]
            package['course_name']=main['written_courses'][0] if main['written_courses'] else package['family']
            if main['sha256'] in overrides:
                package['identity_override']=overrides[main['sha256']];package['course_name']=load(ROOT/'cases'/(overrides[main['sha256']]['case_id']+'.json'))['course_name']
            if len(scored)>1:package['notes'].append('多份有分成绩表保留独立来源，生成前按学号核对，不直接拼接或叠加人数。')
            package['status']='ready_layout' if package['requires_mapping_adapter'] or not main['written_courses'] else 'ready_full' if len(teaching)>=2 else 'ready_minimal'
            package['reason']='已读到学生姓名和实际成绩；'+('英文或姓名式表头需适配后生成。' if package['requires_mapping_adapter'] else '有教学材料，可进入完整课程验证。' if len(teaching)>=2 else '教学材料少，按成绩优先安排并筛选同类课程教学参考。')
            if not main['written_courses']:package['notes'].append('成绩表未明确课程名称，课程名暂按资料包登记，生成前核对课程身份。')
            compatible=course_matches(package['family'],main['written_courses'])
            if main['written_courses'] and not compatible and not package.get('identity_override'):
                package['status']='blocked_identity';package['reason']='成绩表课程抬头“'+'、'.join(main['written_courses'])+'”与资料包课程不同；停止生成，保留身份差异供核对。'
        else:
            ambiguous=any(t.get('unresolved_numeric_columns') for g in grade_sources for t in g['tables'])
            blank=any(t['total_columns'] for g in grade_sources for t in g['tables'])
            package['status']='blocked_grade_semantics' if ambiguous else 'blocked_empty_grades' if blank else 'blocked_missing_grades'
            package['reason']={'blocked_grade_semantics':'有学生数值记录，但总评列未命名、只给分项或填在备注中；停止生成，先识别列含义。','blocked_empty_grades':'成绩表存在，但未发现有效总评；停止生成并提示补充实际成绩。','blocked_missing_grades':'未发现包含学生姓名及实际成绩的关键成绩单；停止生成。'}[package['status']]
        if not scored and package['read_errors']:
            package['status']='blocked_unreadable';package['reason']='候选表或压缩包未读完，不能判定为缺成绩；停止生成，先处理读取问题。'
        if sum(a.get('nested_archive_count',0) for a in package['archives']):package['notes'].append('嵌套学生作业不作为当前课程事实；缺关键成绩时进一步检查嵌套包，检查边界和错误保留记录。')
        package['source_signature']=signature([(f['relative_path'],f['size'],f['mtime_ns']) for f in package['files']])
        packages.append(package)
    # 同类资料仅提供教学参考候选；成绩不跨年度、班级或课程资料包合并。
    for p in packages:
        if p['scope']!='course':continue
        related=[q for q in packages if q['scope']=='course' and q['family']==p['family'] and q['id']!=p['id'] and q.get('teaching_files')]
        related.sort(key=lambda q:q['folder'],reverse=True)
        p['teaching_reference_candidates']=[{'package_id':q['id'],'folder':q['folder'],'paths':[f['path'] for f in q['teaching_files']],'scope':'仅用于教学方案参考；成绩及其学期不得借用'} for q in related]
    old={p['id']:p for p in (load(previous)['packages'] if previous and Path(previous).exists() else [])}
    changed={'added':[p['id'] for p in packages if p['id'] not in old],
             'changed':[p['id'] for p in packages if p['id'] in old and p.get('source_signature')!=old[p['id']].get('source_signature')],
             'unchanged':[p['id'] for p in packages if p['id'] in old and p.get('source_signature')==old[p['id']].get('source_signature')],
             'removed':[p for p in old if p not in {q['id'] for q in packages}]}
    changed['reclassified']=[{'id':p['id'],'before':old[p['id']]['status'],'after':p['status']} for p in packages if p['id'] in old and p['status']!=old[p['id']]['status']]
    catalog={'version':'course_material_catalog_v1','root':str(root),'scanned_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
             'file_count':len(files),'size_bytes':sum(p.stat().st_size for p in files),'package_count':len(packages),
             'status_counts':dict(Counter(p['status'] for p in packages)),'packages':packages,'changes':changed,
             'policy':{'original_files_modified':False,'graduation_generation':False,'missing_grades_stop':True,'current_packages_all_available_for_development':True,'new_independent_blind_claim':False,'archive_contents_executed':False}}
    save(out/'catalog.json',catalog);save(out/'file_inventory.json',inventory)
    write_outputs(catalog,out)
    return catalog

def safe_csv(v):
    text=str(v if v is not None else '')
    return "'"+text if text.startswith(('=','+','-','@')) else text

def prepare_case(package,out):
    """生成可追溯输入清单；只安排探测，不宣称事实抽取或报告已经完成。"""
    rows=[package['primary_grade']]+[g for g in package['grade_sources'] if not g['attendance'] and g.get('catalog_role') not in ('student_support','evaluation_summary') and course_matches(package['family'],g['written_courses'])]
    seen=set();sources=[];rules={}
    for g in rows:
        if g['sha256'] in seen:continue
        seen.add(g['sha256']);sid='grade_'+g['sha256'][:16]
        sources.append({'id':sid,'path':g['path'],'role':'current','kind':'grades'})
        if package.get('identity_override') and package['identity_override']['sha256']==g['sha256']:
            rules[sid]=package['identity_override']
    supporting=[f for f in package['files'] if f['role'] in ('teaching','evaluation_summary','attendance')]
    for f in supporting:
        if f['extension'] not in DOCS|SHEETS or not Path(f['path']).is_file():continue
        sha=digest(f['path'])
        if sha in seen:continue
        seen.add(sha);sources.append({'id':'support_'+sha[:16],'path':f['path'],'role':'current','kind':'document'})
    case={'id':'material_'+package['id'],'group_id':package['family_group_id'],'report_type':'course','course_name':package['course_name'],
          'sources':sources,'ocr':False,'grade_mapping_policy':{'mode':'auto_select_v1','identity_overrides':rules},
          'decision_policy':{'mode':'grade_first_minimal_v1','model_review':True},
          'autonomous_policy':{'latest_applicable_materials':True,'logical_conflict_decisions':True},
          'evaluation_policy':{'normal':'include','missing':'exclude','absent':'exclude','deferred':'exclude','exempt':'exclude','source_refs':[],'origin':'user_material_arrangement_exclude_missing_without_zero'},
          'report_presentation':{'style':'course_report','show_student_names':True,'chart_analysis':True,'chapter6':'improvement_plan','show_student_grade_table':True},
          'material_intake':{'package_id':package['id'],'folder':package['folder'],'source_signature':package['source_signature'],'catalog_status':package['status'],'phase':'preflight_only','requires_mapping_adapter':package['requires_mapping_adapter'],'new_facts_or_evaluation_profile_not_yet_approved':True},
          'data_partition':'development_all_available_materials_not_independent_blind_evaluation'}
    filename=out/'cases'/(case['id']+'.json');save(filename,case)
    return filename

def write_outputs(catalog,out):
    rows=[];queue=[]
    order={'ready_full':1,'ready_minimal':2,'ready_layout':3}
    for p in catalog['packages']:
        rows.append([p['id'],p['folder'],p.get('course_name',p['family']),STATE_NAMES[p['status']],p.get('registered_count',''),p.get('scored_count',''),p['file_count'],len(p.get('teaching_files',[])),p.get('primary_grade',{}).get('origin',{}).get('member',p.get('primary_grade',{}).get('path','')),p['reason'],'；'.join(p['notes']),p['path']])
        if p['status'] in order:
            case_file=prepare_case(p,out)
            probe=ROOT/'outputs'/('material_probe_'+p['id'])
            queue.append({
                'package_id':p['id'],'course_name':p['course_name'],'batch':order[p['status']],
                'status':'queued' if p['status']!='ready_layout' else 'adapter_required',
                'folder':p['folder'],'family_group_id':p['family_group_id'],
                'catalog_status':p['status'],'existing_cases':p['existing_cases'],
                'generation_started':False,'case_path':str(case_file),
                'next_action':'核对成绩布局/课程身份后做事实抽取探测' if p['status']=='ready_layout' else '事实抽取探测，再构造并核验评价方案',
                'probe_command':shlex.join([sys.executable,str(ROOT/'run_report.py'),'run','--case',str(case_file),'--out',str(probe),'--probe-extraction']),
            })
    queue.sort(key=lambda q:(q['batch'],bool(q['existing_cases']),q['folder']),reverse=False)
    # 每批先覆盖不同课程家族，再处理同类课程的其他年份/班级；不留材料不用。
    arranged=[]
    for batch in sorted(order.values()):
        pending=[q for q in queue if q['batch']==batch];family_count=Counter()
        while pending:
            chosen=min(pending,key=lambda q:(bool(q['existing_cases']),family_count[q['family_group_id']],-int(re.search(r'20\d{2}',q['folder'])[0]) if re.search(r'20\d{2}',q['folder']) else 0,q['folder']))
            arranged.append(chosen);pending.remove(chosen);family_count[chosen['family_group_id']]+=1
    queue=arranged
    for rank,item in enumerate(queue,1):item['order']=rank
    save(out/'batch_queue.json',queue)
    save(out/'stopped_packages.json',[{'package_id':p['id'],'folder':p['folder'],'status':p['status'],'reason':p['reason'],'notes':p['notes'],'read_errors':p['read_errors'],'generation_allowed':False} for p in catalog['packages'] if p['status'].startswith('blocked_')])
    with (out/'课程资料安排.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['资料包编号','目录','课程名称','处理安排','主表登记人数','主表有效成绩人数','文件数','教学材料数','主要成绩来源','原因','注意事项','原目录'])
        w.writerows([[safe_csv(v) for v in row] for row in rows])
    summary=['课程资料筛选与安排',f'扫描根目录：{catalog["root"]}',f'扫描时间：{catalog["scanned_at"]}',f'共{catalog["package_count"]}个资料目录、{catalog["file_count"]}个文件。','原始文件不移动、不改名；成绩不跨班次或年度混用；毕业设计不进入课程报告队列。','所有可用课程均进入开发验证安排，不将已查看材料冒充独立盲测。','']
    for status,label in STATE_NAMES.items():
        group=[p for p in catalog['packages'] if p['status']==status]
        if not group:continue
        summary.append(label+f'（{len(group)}个）')
        summary += ['- '+p['folder']+'：'+p['reason'] for p in group];summary.append('')
    summary+=['后续新增资料：再次运行相同扫描命令，并用--previous指向本次catalog.json；稳定资料包ID及文件签名用于区分新增、变更和未变。','停止状态不运行模型、不生成报告。成绩存在但语义未明与真正缺成绩分别提示。','压缩包只读目录并提取小型成绩表；没有执行材料内的脚本或学生程序。嵌套学生作业包保留索引，遇到读取不完整单列提示。','本次仅完成材料清点和批次安排，尚未将所有队列课程认定为报告生成成功。']
    (out/'资料筛选与后续安排.txt').write_text('\n'.join(summary)+'\n')
