#!/usr/bin/env python3
"""可替代旧服务的本地文本API；默认必须加载选定Adapter。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import threading
import time
from report_agent.common import load,save,digest,model_files

def start(args):
    import torch
    import uvicorn
    from fastapi import FastAPI,HTTPException
    from pydantic import BaseModel,Field
    from transformers import AutoTokenizer,TextStreamer
    from peft import PeftModel
    from model_service.loading import load_base,tokenizer_directory
    from model_service.structured import load_backend,response_contract,tokenizer_selftest,StructuredDecodeError
    base=Path(args.model_path).expanduser().resolve();adapter=Path(args.adapter_path).expanduser().resolve()
    af=model_files(adapter,True);tokenizer=AutoTokenizer.from_pretrained(tokenizer_directory(base,adapter),local_files_only=True,trust_remote_code=False)
    if tokenizer.pad_token_id is None:tokenizer.pad_token=tokenizer.eos_token
    build_prefix=load_backend(tokenizer)
    tokenizer_selftest(tokenizer,build_prefix)
    print('实际分词器JSON回放自检通过（含多字节汉字）；开始加载模型',flush=True)
    model,dtype=load_base(base,args.bits,'auto' if args.device_map=='auto' else {'':0},args.attention)
    model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False,local_files_only=True).eval()
    cfg=load(base/'config.json')
    context=cfg.get('text_config',cfg).get('max_position_embeddings',args.max_input_tokens+args.max_output_tokens)
    identity={'status':'ready','model_path':str(base),'adapter_path':str(adapter),'bits':args.bits,
              'architecture':load(base/'config.json').get('architectures',[]),'max_input_tokens':args.max_input_tokens,
              'max_output_tokens':args.max_output_tokens,'max_context_tokens':context,'features':['count_tokens','do_sample','progress','json_schema'],
              'service_protocol':'structured_v1','structured_backend_revision':'unicode_context_guard_1',
              'adapter_sha256':{p.name:digest(p) for p in af}}
    lock=threading.Lock();progress={'busy':False,'output_tokens':0,'started_at':None}
    app=FastAPI(title='graduation_agent_v2 model service')
    class Request(BaseModel):
        messages:list[dict[str,str]]
        max_new_tokens:int=Field(default=2048,ge=1)
        temperature:float=Field(default=.1,gt=0)
        do_sample:bool=False
        output_schema:dict|None=None
    def encode(messages):
        if not messages or messages[-1].get('role')!='user':raise HTTPException(422,'最后一条消息须为user')
        if any(m.get('role') not in ('system','user','assistant') or not isinstance(m.get('content'),str) for m in messages):raise HTTPException(422,'无效messages')
        prompt=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,enable_thinking=False)
        return tokenizer(prompt,return_tensors='pt',add_special_tokens=False)
    @app.get('/health')
    def health():return identity
    @app.get('/progress')
    def state():return dict(progress)
    @app.post('/count_tokens')
    def count(req:Request):return {'input_tokens':encode(req.messages)['input_ids'].shape[1],
                                  'max_input_tokens':args.max_input_tokens,'max_context_tokens':context}
    class Counter(TextStreamer):
        def __init__(self):super().__init__(tokenizer,skip_prompt=True);self.first=True
        def put(self,value):
            if self.first:self.first=False;return
            progress['output_tokens']+=int(value.numel())
        def end(self):pass
    @app.post('/generate')
    def generate(req:Request):
        if req.max_new_tokens>args.max_output_tokens:raise HTTPException(422,'max_new_tokens超过服务输出上限')
        if not lock.acquire(False):raise HTTPException(429,'已有生成任务')
        try:
            try:prefix=build_prefix(req.output_schema,'\n'.join(m['content'] for m in req.messages)) if req.output_schema is not None else None
            except Exception as exc:raise HTTPException(422,'无法建立JSON约束：'+str(exc)) from exc
            inputs=encode(req.messages);n=inputs['input_ids'].shape[1]
            if n>args.max_input_tokens or n+req.max_new_tokens>context:raise HTTPException(413,'输入/输出预算超限')
            device=model.get_input_embeddings().weight.device
            inputs={k:v.to(device) for k,v in inputs.items()};progress.update(busy=True,output_tokens=0,started_at=time.time())
            t=time.monotonic()
            with torch.inference_mode():
                result=model.generate(**inputs,max_new_tokens=req.max_new_tokens,do_sample=req.do_sample,
                    **({'temperature':req.temperature} if req.do_sample else {}),pad_token_id=tokenizer.pad_token_id,streamer=Counter(),
                    **({'prefix_allowed_tokens_fn':prefix,'eos_token_id':tokenizer.eos_token_id} if prefix is not None else {}))
            generated=result[0,n:];text=tokenizer.decode(generated,skip_special_tokens=True,clean_up_tokenization_spaces=False)
            ids=generated.tolist()
            return {'text':text,'input_tokens':n,'output_tokens':len(generated),'generation_seconds':time.monotonic()-t,
                    'finish_reason':'eos' if ids and ids[-1]==tokenizer.eos_token_id else ('length' if len(ids)>=req.max_new_tokens else 'other_stop'),
                    'generated_tail_token_ids':ids[-24:],'eos_token_id':tokenizer.eos_token_id,
                    'limit_reached':len(generated)>=req.max_new_tokens,**response_contract(text,req.output_schema)}
        except StructuredDecodeError as e:raise HTTPException(422,str(e)) from e
        except torch.cuda.OutOfMemoryError as e:raise HTTPException(507,'显存不足；缩短上下文或调整服务量化配置') from e
        finally:progress['busy']=False;lock.release()
    uvicorn.run(app,host='127.0.0.1',port=args.port,workers=1,access_log=False)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--model-path',required=True);p.add_argument('--adapter-path',required=True)
    p.add_argument('--bits',type=int,choices=[4,8],default=8);p.add_argument('--device-map',choices=['single','auto'],default='single')
    p.add_argument('--max-input-tokens',type=int,default=8192);p.add_argument('--max-output-tokens',type=int,default=8192)
    p.add_argument('--attention',default='sdpa');p.add_argument('--port',type=int,default=8000);start(p.parse_args())
