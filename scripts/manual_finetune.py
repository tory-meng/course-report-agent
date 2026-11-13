#!/usr/bin/env python3
"""用户手动执行：逐条审核 → 原模型基线 → 冒烟 → 正式训练 → 恢复原服务。"""
import argparse
import copy
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from report_agent.common import load,save,path,read_rows,digest,signature
from report_agent.client import Client
from training.data import validate_row
from training.preparation import precheck,export_prepared

DOC_PY=Path(sys.executable)
FT_PY=Path(sys.executable)
SERVICE_PY=Path(sys.executable)

def prepared_rows(preparation):
    rows=read_rows(preparation/'train.candidates.jsonl')+read_rows(preparation/'validation.candidates.jsonl')
    manifest=load(preparation/'preparation_manifest.json')
    if signature({'train':[r for r in rows if r['split']=='train'],'validation':[r for r in rows if r['split']=='validation']})!=manifest['dataset_fingerprint']:
        raise ValueError('准备数据已改变；重新整理并审核，不能沿用旧批准')
    return rows

def reviewed_row(row,decision):
    review=decision.get('review',{})
    if review.get('approved') is not True:return None
    if not str(review.get('reviewer','')).strip() or review.get('answer_isolation_checked') is not True:
        raise ValueError(row['id']+'缺少逐条审核信息')
    if signature(row)!=decision.get('candidate_sha256'):raise ValueError(row['id']+'候选内容已改变')
    answer_file=path(decision['answer_file'])
    if digest(answer_file)!=decision.get('answer_sha256'):raise ValueError(row['id']+'答案已修改，需重新审核绑定')
    selected=copy.deepcopy(row);selected['review']=copy.deepcopy(review)
    selected['messages'][-1]['content']=json.dumps(load(answer_file),ensure_ascii=False,separators=(',',':'))
    validate_row(selected);precheck(selected);return selected

def review_status(preparation):
    rows=prepared_rows(preparation);decisions=load(preparation/'review_decisions.json')['decisions']
    by_id={d['id']:d for d in decisions}
    if len(by_id)!=len(decisions) or set(by_id)!={r['id'] for r in rows}:raise ValueError('审核清单与候选ID不一致')
    selected=[r for row in rows if (r:=reviewed_row(row,by_id[row['id']])) is not None]
    counts={split:sum(r['split']==split for r in selected) for split in ('train','validation')}
    print(f"候选 {len(rows)} 条，已逐条批准 {len(selected)} 条（训练 {counts['train']} / 验证 {counts['validation']}）。",flush=True)
    return rows,selected

def interactive_review(preparation,reviewer=None):
    rows=prepared_rows(preparation);file=preparation/'review_decisions.json';document=load(file)
    decisions={d['id']:d for d in document['decisions']}
    reviewer=(reviewer or input('请输入你的审核签名：')).strip()
    if not reviewer:raise ValueError('审核签名不能为空')
    print('逐条显示完整任务与候选答案。y=确认本条内容及答案隔离；n=暂不批准；r=编辑答案文件后刷新；q=保存退出。没有批量批准。')
    for index,row in enumerate(rows,1):
        decision=decisions[row['id']]
        try:
            if reviewed_row(row,decision) is not None:continue
        except ValueError:
            # 不复用失效审批；用户重新看本条内容后才能确认。
            decision['review']={'approved':False,'reviewer':'','answer_isolation_checked':False};save(file,document)
        while True:
            answer_file=path(decision['answer_file']);answer=load(answer_file)
            displayed_sha256=digest(answer_file)
            print(f"\n[{index}/{len(rows)}] {row['course_name']} / {row['task_kind']} / {row['split']}\nID: {row['id']}")
            print('任务输入：\n'+row['messages'][-2]['content'])
            print('候选答案：\n'+json.dumps(answer,ensure_ascii=False,indent=2))
            print('来源片段：',preparation/'evidence'/(row['case_id']+'.json'))
            print('修改答案文件：',answer_file)
            action=input('[y/n/r/q]：').strip().lower()
            if action=='q':save(file,document);review_status(preparation);return
            if action=='r':continue
            if action=='y':
                if digest(answer_file)!=displayed_sha256:
                    print('答案文件已改变，重新显示后再确认。');continue
                candidate=copy.deepcopy(row);candidate['messages'][-1]['content']=json.dumps(answer,ensure_ascii=False,separators=(',',':'))
                precheck(candidate)
                decision.update(candidate_sha256=signature(row),answer_sha256=digest(answer_file),
                    review={'approved':True,'reviewer':reviewer,'answer_isolation_checked':True,'reviewed_at':datetime.now().astimezone().isoformat()})
                save(file,document);break
            if action in ('','n'):break
            print('请输入y、n、r或q。')
    review_status(preparation)

def run_logged(args,log,env=None):
    print('\n执行：', ' '.join(map(str,args)), '\n日志：',log,flush=True)
    with log.open('x',encoding='utf-8') as f:
        proc=subprocess.Popen(list(map(str,args)),cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,bufsize=1,env=env,start_new_session=True)
        try:
            for line in proc.stdout:
                print(line,end='',flush=True);f.write(line);f.flush()
            code=proc.wait()
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGINT)
                try:proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=10)
            raise
    if code:raise RuntimeError(f'子步骤退出 {code}，请查看 {log}')

def checked_service_pid(port):
    result=subprocess.run(['ss','-ltnp',f'sport = :{port}'],capture_output=True,text=True,check=True)
    pids={int(x) for x in re.findall(r'pid=(\d+)',result.stdout)}
    if len(pids)!=1:raise ValueError('无法唯一确认本任务监听PID，不自动停止任何进程')
    pid=pids.pop();proc=Path('/proc')/str(pid)
    if proc.stat().st_uid!=os.getuid() or (proc/'cwd').resolve()!=ROOT:raise ValueError('监听进程不属于当前用户/项目')
    args=(proc/'cmdline').read_bytes().decode().strip('\0').split('\0')
    if Path(args[0]).resolve()!=SERVICE_PY.resolve():raise ValueError('监听进程不属于--service-python指定的解释器')
    rest=args[1:]
    while rest and rest[0] in ('-u','-B','-O'):rest=rest[1:]
    if not rest or path(rest[0])!=ROOT/'scripts/start_service.py':raise ValueError('监听进程不是本项目start_service.py，不自动停止')
    return pid,args

def stop_original_service(runtime,out):
    cfg=load(runtime);client=Client(cfg['model']);state=client.http('/progress')
    if state.get('busy'):raise ValueError('原推理服务正在生成，等待结束后再训练；没有发送停止信号')
    url=urlsplit(cfg['model']['base_url'])
    if url.hostname not in ('127.0.0.1','localhost'):raise ValueError('只管理本机项目服务')
    pid,args=checked_service_pid(url.port or 80)
    record={'runtime':str(runtime),'runtime_sha256':digest(runtime),'identity':client.identity,'pid':pid,'argv':args,'cwd':str(ROOT)}
    save(out/'original_service.json',record)
    os.kill(pid,signal.SIGTERM)
    for _ in range(60):
        result=subprocess.run(['ss','-ltnp',f'sport = :{url.port or 80}'],capture_output=True,text=True,check=True)
        if f'pid={pid},' not in result.stdout:return record
        time.sleep(.5)
    raise RuntimeError('原服务未在30秒内退出，不自动开始训练')

def restore_original_service(record,out):
    runtime=path(record['runtime'])
    if digest(runtime)!=record['runtime_sha256']:raise ValueError('原runtime已改变，不用新配置冒充恢复原服务')
    cfg=load(runtime)
    try:
        client=Client(cfg['model']);print('原服务已经ready，无需重复启动。',flush=True);return
    except (OSError,RuntimeError,ValueError):pass
    port=urlsplit(cfg['model']['base_url']).port or 80
    listening=subprocess.run(['ss','-ltnp',f'sport = :{port}'],capture_output=True,text=True,check=True)
    if 'LISTEN' in listening.stdout:raise ValueError('端口已被其他服务占用，不停止或覆盖该服务')
    identity=record['identity']
    command=[str(SERVICE_PY),'-u','scripts/start_service.py','--runtime',str(runtime),
             '--max-input-tokens',str(int(identity['max_input_tokens'])),
             '--max-output-tokens',str(int(identity['max_output_tokens'])),'--device-map','single']
    with (out/'service_restore.log').open('a',encoding='utf-8') as log:
        proc=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    save(out/'service_restore_pid.json',{'pid':proc.pid,'command':command})
    for attempt in range(120):
        if proc.poll() is not None:raise RuntimeError('原服务恢复进程退出，查看service_restore.log')
        try:
            client=Client(cfg['model']);print('原Adapter推理服务已恢复ready。',flush=True);return
        except (OSError,RuntimeError,ValueError):pass
        if attempt%10==0:print('等待原推理服务恢复……',flush=True)
        time.sleep(2)
    raise RuntimeError('恢复进程仍在启动，查看service_restore.log；不重复启动')

def manual_train(preparation,out,runtime,gpu):
    _,selected=review_status(preparation)
    if not selected:raise ValueError('尚无逐条审核通过样本。先运行--stage review；没有导出、停止服务或训练。')
    if {r['split'] for r in selected}!={'train','validation'}:raise ValueError('审核样本必须同时包含训练与验证课程')
    if ROOT/'outputs' not in out.parents:raise ValueError('--out必须位于本项目outputs下')
    if out.exists():raise ValueError('输出目录已存在，请换新的--out；不覆盖或自动续训')
    for exe in (DOC_PY,FT_PY,SERVICE_PY):
        if not exe.is_file():raise ValueError('环境Python不存在：'+str(exe))
    cfg=load(preparation/'training_config.proposed.json');active=load(runtime)['model']
    if path(cfg['adapter_path'])!=path(active['expected_adapter_path']) or cfg['expected_adapter_sha256']!=active['expected_adapter_sha256']:
        raise ValueError('训练起点与当前原Adapter不一致')
    out.mkdir(parents=True);data=out/'dataset';config=out/'training_config.json';save(config,cfg)
    try:
        exported=export_prepared(preparation,preparation/'review_decisions.json',data)
        save(out/'run_status.json',{'status':'preparing','new_training':False,'dataset':exported})
        run_logged([FT_PY,'train_v2.py','train','--config',config,'--dataset',data,'--out',out/'prepare',
                    '--stage','formal','--prepare-only'],out/'prepare.log')
        run_logged([DOC_PY,'train_v2.py','evaluate','--runtime',runtime,'--data',data/'validation.jsonl',
                    '--out',out/'baseline_before.json'],out/'baseline_before.log')
        if load(out/'baseline_before.json')['status']!='complete':raise ValueError('原模型基线没有完整完成')
        try:
            record=stop_original_service(runtime,out)
            env=os.environ.copy();env['CUDA_VISIBLE_DEVICES']=gpu
            run_logged([FT_PY,'train_v2.py','train','--config',config,'--dataset',data,'--out',out/'smoke','--stage','smoke'],out/'smoke.log',env)
            smoke=load(out/'smoke/training_results.json')
            if smoke['global_step']!=5 or smoke['stage']!='smoke' or smoke['data_fingerprint']!=exported['fingerprint']:
                raise ValueError('冒烟步数、阶段或数据指纹不符')
            if not math.isfinite(smoke['after']['after_loss']):raise ValueError('冒烟loss不正常')
            # formal配置仍指向原Adapter，不使用smoke/adapter作为起点。
            run_logged([FT_PY,'train_v2.py','train','--config',config,'--dataset',data,'--out',out/'formal','--stage','formal'],out/'formal.log',env)
            save(out/'run_status.json',{'status':'trained_candidate_not_promoted','candidate':str(out/'formal/adapter'),
                'original_adapter':cfg['adapter_path'],'next':'使用同一验证输入与原模型比较，再复核完整报告；未自动上线候选。'})
        finally:
            if (out/'original_service.json').is_file():
                restore_original_service(load(out/'original_service.json'),out)
    except BaseException as exc:
        save(out/'last_error.json',{'error_type':type(exc).__name__,'message':str(exc)})
        raise
    print('\n正式候选Adapter：',out/'formal/adapter','\n训练结果：',out/'formal/training_results.json',flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['review','check','train','restore'],required=True)
    parser.add_argument('--preparation',default='data/prepared')
    parser.add_argument('--out',default='outputs/sft_manual')
    parser.add_argument('--runtime',default='configs/runtime.json');parser.add_argument('--reviewer')
    parser.add_argument('--doc-python',default=sys.executable,help='报告/评测解释器；默认当前Python')
    parser.add_argument('--train-python',default=sys.executable,help='训练解释器；可指定独立CUDA环境Python')
    parser.add_argument('--service-python',default=sys.executable,help='推理服务实际使用的解释器；用于严格核验监听进程')
    parser.add_argument('--gpu',default='0');a=parser.parse_args()
    global DOC_PY,FT_PY,SERVICE_PY
    DOC_PY=Path(a.doc_python).expanduser().resolve()
    FT_PY=Path(a.train_python).expanduser().resolve()
    SERVICE_PY=Path(a.service_python).expanduser().resolve()
    if not re.fullmatch(r'\d+',a.gpu):parser.error('--gpu须为单个物理GPU编号')
    try:
        if a.stage=='review':interactive_review(path(a.preparation),a.reviewer)
        elif a.stage=='check':
            _,selected=review_status(path(a.preparation))
            if not selected:raise ValueError('尚无逐条批准样本；先运行--stage review。')
            if {r['split'] for r in selected}!={'train','validation'}:raise ValueError('还缺少训练侧或验证侧的批准样本')
            print('审核绑定检查通过；正式启动仍将复核来源并运行真实Tokenizer检查。')
        elif a.stage=='restore':restore_original_service(load(path(a.out)/'original_service.json'),path(a.out))
        else:manual_train(path(a.preparation),path(a.out),path(a.runtime),a.gpu)
    except (ValueError,RuntimeError,OSError) as exc:
        print('已停止：'+str(exc),file=sys.stderr);return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
