"""已审核轨迹→按课程组隔离的数据集；只监督最终assistant答案。"""
import copy
import json
import random
from pathlib import Path
from report_agent.common import load,save,signature,read_rows,write_rows,require_empty

def validate_row(row):
    if not row.get('group_id') or not row.get('id'):raise ValueError('训练样本必须有id与课程group_id')
    review=row.get('review',{})
    if not review.get('approved') or not review.get('reviewer') or not review.get('answer_isolation_checked'):
        raise ValueError(row['id']+('未经完整代理核对' if review.get('kind')=='user_authorized_agent_review' else '未经完整人工审核'))
    kind=review.get('kind','human_review')
    if kind not in ('human_review','user_authorized_agent_review','synthetic_fixture_review'):raise ValueError('未知训练审核类型')
    if kind=='synthetic_fixture_review' and row.get('synthetic') is not True:
        raise ValueError('合成夹具校验只能用于synthetic=true；不授予真实课程训练资格')
    if kind=='user_authorized_agent_review':
        from .review_authority import validate_agent_review
        validate_agent_review(row)
    return validate_payload(row)

def validate_payload(row):
    """仅检查候选内容，供准备阶段使用；不表示审核批准，训练仍调用validate_row。"""
    if not row.get('group_id') or not row.get('id'):raise ValueError('样本必须有id与课程group_id')
    messages=row.get('messages',[])
    if len(messages)<2 or messages[-1].get('role')!='assistant' or not any(m.get('role')=='user' for m in messages[:-1]):raise ValueError('messages须含user且以assistant结束')
    if any(m.get('role') not in ('system','user','assistant') or not isinstance(m.get('content'),str) for m in messages):raise ValueError('训练仅支持文本对话')
    answer=messages[-1]['content'];obj=json.loads(answer)
    if not isinstance(obj,dict) or not obj:raise ValueError('训练答案须为非空JSON对象')
    if '<think>' in answer or '```' in answer:raise ValueError('答案含思考/围栏')
    if any(answer in m['content'] for m in messages[:-1]):raise ValueError('完整答案已出现在输入中')
    if any(signature(json.loads(m['content']))==signature(obj) for m in messages[:-1] if m['content'].lstrip().startswith('{') and valid_json(m['content'])):
        raise ValueError('输入包含完整目标JSON')
    return row

def valid_json(s):
    try:json.loads(s);return True
    except ValueError:return False

def actual_messages(record):
    """压缩或纠错后以成功请求的真实输入训练，避免输入与答案不对应。"""
    for attempt in reversed(record.get('attempts', [])):
        if attempt.get('error') is None and attempt.get('raw') is not None and attempt.get('messages'):
            return attempt['messages']
    return record['messages']

def approve(record,answer_file,reviewer,isolation):
    if not reviewer.strip() or not isolation:raise ValueError('需填写reviewer并显式确认答案隔离')
    p=Path(record);r=load(p)
    if not r.get('ok'):raise ValueError('失败轨迹不能直接批准；先修复生成并核对证据')
    answer=load(answer_file) if answer_file else r['answer']
    candidate={'id':p.stem,'group_id':r['group_id'],'messages':actual_messages(r)+[{'role':'assistant','content':json.dumps(answer,ensure_ascii=False)}],
               'review':{'approved':True,'reviewer':reviewer,'answer_isolation_checked':True}}
    validate_row(candidate)
    r['review']={**candidate['review'],'corrected_answer':answer};save(p,r)

def export(runs,out,replay=None,seed=42,validation_groups=None):
    out=Path(out);require_empty(out);rows=[]
    for run in runs:
        run=Path(run);manifest=load(run/'run_manifest.json')
        coverage=run/'extraction_coverage.json'
        if coverage.is_file() and load(coverage).get('status')=='blocked':
            raise ValueError('事实覆盖检查未通过，不导出训练数据：'+str(run))
        for file in sorted((run/'records').glob('*.json')):
            r=load(file)
            if not r.get('ok') or not r.get('review',{}).get('approved'):continue
            answer=r['review'].get('corrected_answer',r['answer'])
            rows.append({'id':manifest['case_id']+':'+file.stem,'group_id':r['group_id'],'case_id':r['case_id'],
                'task_kind':r.get('task_kind'),'section_id':r.get('section_id'),'template_id':r.get('template_id'),
                'synthetic':bool(manifest.get('synthetic_demo')),'origin_record':str(file.resolve()),
                'messages':actual_messages(r)+[{'role':'assistant','content':json.dumps(answer,ensure_ascii=False,separators=(',',':'))}],
                'review':{k:v for k,v in r['review'].items() if k!='corrected_answer'}})
    if replay:rows+=read_rows(replay)
    for row in rows:validate_row(row)
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)):raise ValueError('重复训练样本id')
    groups=sorted({r['group_id'] for r in rows})
    if len(groups)<2:raise ValueError('至少需要两个独立课程组才能划分train/validation；不把同一课程的章节随机拆分冒充独立验证')
    if validation_groups:
        vg=set(validation_groups)
        if not vg<=set(groups):raise ValueError('指定验证课程组没有已审核样本：'+str(sorted(vg-set(groups))))
        if vg==set(groups):raise ValueError('所有课程均在验证集，缺少训练课程')
    else:
        random.Random(seed).shuffle(groups);nval=max(1,round(len(groups)*.2));vg=set(groups[:nval])
    train=[r for r in rows if r['group_id'] not in vg];val=[r for r in rows if r['group_id'] in vg]
    valanswers={signature(json.loads(r['messages'][-1]['content'])) for r in val};removed=[];kept=[]
    for r in train:
        if signature(json.loads(r['messages'][-1]['content'])) in valanswers:removed.append(r['id'])
        else:kept.append(r)
    train=kept
    if not train or not val:raise ValueError('隔离/答案去重后训练或验证为空')
    write_rows(out/'train.jsonl',train);write_rows(out/'validation.jsonl',val)
    manifest={'seed':seed,'train_groups':sorted({r['group_id'] for r in train}),'validation_groups':sorted(vg),
              'train_rows':len(train),'validation_rows':len(val),'removed_train_duplicate_answers':removed,
              'synthetic_only':all(r.get('synthetic',False) for r in train+val),'fingerprint':signature({'train':train,'validation':val})}
    save(out/'dataset_manifest.json',manifest);return manifest

def review_queue(run,out):
    """生成可编辑答案副本及审核清单，不自动批准模型输出。"""
    run=Path(run);out=Path(out);require_empty(out);manifest=load(run/'run_manifest.json');rows=[]
    for file in sorted((run/'records').glob('*.json')):
        r=load(file)
        if not r.get('ok') or not r.get('messages') or not r.get('group_id') or not r.get('answer'):continue
        if r.get('task_kind') not in ('extract','write_section'):continue
        answer_file=out/'answers'/file.name;save(answer_file,r.get('review',{}).get('corrected_answer',r['answer']))
        rows.append({'record':str(file.resolve()),'answer_file':str(answer_file.resolve()),'task_kind':r['task_kind'],
                     'group_id':r['group_id'],'reviewed':bool(r.get('review',{}).get('approved')),
                     'check':'对照原始证据核对字段、数值、引用和章节；空records须确认没有漏抽。'})
    result={'case_id':manifest['case_id'],'count':len(rows),'records':rows,
            'note':'答案副本修改后逐条approve；此清单不会把结构通过当作人工审核，也不会导出参考库摘要为当前课程事实。'}
    save(out/'review_queue.json',result);return result

def encode_row(tokenizer,row,max_length):
    validate_row(row)
    return encode_payload(tokenizer,row,max_length)

def encode_payload(tokenizer,row,max_length):
    """候选长度/监督区间检查，不加载模型、不授予训练资格。"""
    validate_payload(row)
    prompt=tokenizer.apply_chat_template(row['messages'][:-1],tokenize=False,add_generation_prompt=True,enable_thinking=False)
    prefix=tokenizer.encode(prompt,add_special_tokens=False);answer=tokenizer.encode(row['messages'][-1]['content'],add_special_tokens=False)
    end=tokenizer.convert_tokens_to_ids('<|im_end|>')
    if end is None or end==tokenizer.unk_token_id or tokenizer.encode('<|im_end|>',add_special_tokens=False)!=[end]:raise ValueError('未确认Qwen结束标记')
    ids=prefix+answer+[end]
    if len(ids)>max_length:raise ValueError(f'{row["id"]}: {len(ids)} tokens超过max_length={max_length}；不截断答案')
    if not answer:raise ValueError('空监督答案')
    return {'input_ids':ids,'attention_mask':[1]*len(ids),'labels':[-100]*len(prefix)+answer+[end]}

def collate(rows,pad_id):
    import torch
    n=max(len(r['input_ids']) for r in rows)
    return {key:torch.tensor([r[key]+[fill]*(n-len(r[key])) for r in rows],dtype=torch.long) for key,fill in [('input_ids',pad_id),('attention_mask',0),('labels',-100)]}
