"""4-bit QLoRA继续微调/显式新建LoRA；完整checkpoint恢复，最终保存独立Adapter。"""
import functools
import inspect
import math
from pathlib import Path
from report_agent.common import path,load,save,digest,signature,read_rows,model_files
from .data import validate_row,encode_row,collate

def compatible_arguments(kwargs,arguments_class):
    """只适配已确认的Transformers接口变化；不丢弃未知训练参数。"""
    kwargs=dict(kwargs);supported=inspect.signature(arguments_class.__init__).parameters
    if 'warmup_ratio' in kwargs and 'warmup_ratio' not in supported:
        warmup=supported.get('warmup_steps')
        if warmup is None or warmup.annotation not in (float,'float'):
            raise RuntimeError('当前TrainingArguments不支持已确认的比例warmup接口')
        kwargs['warmup_steps']=float(kwargs.pop('warmup_ratio'))
    if 'eval_strategy' not in supported and 'evaluation_strategy' in supported:
        kwargs['evaluation_strategy']=kwargs.pop('eval_strategy')
    unknown=set(kwargs)-set(supported)
    if unknown:raise RuntimeError('TrainingArguments版本不兼容：'+str(sorted(unknown)))
    return kwargs

def training_kwargs(cfg,out,stage,arguments_class):
    accumulation=cfg.get('smoke_gradient_accumulation_steps',4) if stage=='smoke' else cfg['gradient_accumulation_steps']
    interval=5 if stage=='smoke' else cfg.get('checkpoint_interval',25)
    kwargs=dict(output_dir=str(out),per_device_train_batch_size=cfg['batch_size'],per_device_eval_batch_size=1,
        gradient_accumulation_steps=accumulation,num_train_epochs=cfg['epochs'],max_steps=5 if stage=='smoke' else -1,
        learning_rate=cfg['learning_rate'],warmup_ratio=cfg['warmup_fraction'],lr_scheduler_type='linear',
        weight_decay=cfg.get('weight_decay',0),max_grad_norm=1.0,optim=cfg.get('optimizer','paged_adamw_8bit'),
        bf16=True,fp16=False,gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False},
        logging_steps=1,logging_nan_inf_filter=False,save_strategy='steps',save_steps=interval,eval_strategy='steps',eval_steps=interval,
        save_total_limit=cfg.get('save_total_limit',3),load_best_model_at_end=True,metric_for_best_model='eval_loss',greater_is_better=False,
        report_to=[],dataloader_num_workers=0,remove_unused_columns=False,prediction_loss_only=True,label_names=['labels'],seed=cfg['seed'],data_seed=cfg['seed'])
    return compatible_arguments(kwargs,arguments_class)

def train(config,dataset,out,stage,prepare_only=False,resume=None):
    cfg=load(path(config));data=path(dataset);out=path(out);manifest=load(data/'dataset_manifest.json')
    raw_train=read_rows(data/'train.jsonl');raw_val=read_rows(data/'validation.jsonl')
    if not raw_train or not raw_val:raise ValueError('训练/验证集为空')
    for r in raw_train+raw_val:validate_row(r)
    if {r['group_id'] for r in raw_train}&{r['group_id'] for r in raw_val}:raise ValueError('课程组跨集合泄漏')
    fp=signature({'train':raw_train,'validation':raw_val})
    if fp!=manifest['fingerprint']:raise ValueError('数据集内容与导出指纹不一致')
    if stage=='formal' and (all(r.get('synthetic',False) for r in raw_train+raw_val) or
                            any(r.get('review',{}).get('kind')=='synthetic_fixture_review' for r in raw_train+raw_val)):
        raise ValueError('合成测试夹具只允许smoke，不进行正式训练；正式SFT须使用另行审核的数据集')
    model_path=path(cfg['model_path']);model_files(model_path)
    init=cfg.get('initialization','continue_adapter');adapter=path(cfg['adapter_path']) if cfg.get('adapter_path') else None
    origin={}
    if init=='continue_adapter':
        if adapter is None:raise ValueError('继续微调必须填写adapter_path')
        origin={p.name:digest(p) for p in model_files(adapter,True)}
        if cfg.get('expected_adapter_sha256') and origin!=cfg['expected_adapter_sha256']:raise ValueError('起始Adapter哈希变化')
    elif init!='new_lora':raise ValueError('initialization必须continue_adapter或new_lora')
    if out==model_path or model_path in out.parents or (adapter and (out==adapter or adapter in out.parents)):
        raise ValueError('训练输出不能覆盖或放进起始模型目录')
    import torch
    from transformers import AutoTokenizer,Trainer,TrainingArguments,TrainerCallback,set_seed
    from peft import PeftModel,prepare_model_for_kbit_training,LoraConfig,get_peft_model
    from model_service.loading import load_base,tokenizer_directory
    tokenizer=AutoTokenizer.from_pretrained(tokenizer_directory(model_path,adapter),local_files_only=True,trust_remote_code=False)
    if tokenizer.pad_token_id is None:tokenizer.pad_token=tokenizer.eos_token
    tokenizer.padding_side='right'
    rows=[encode_row(tokenizer,r,cfg['max_length']) for r in raw_train];val=[encode_row(tokenizer,r,cfg['max_length']) for r in raw_val]
    if stage=='smoke':rows=sorted(rows,key=lambda r:len(r['input_ids']),reverse=True)[:20]
    # GPU加载前检查接口，prepare-only也覆盖参数兼容性。
    kwargs=training_kwargs(cfg,out,stage,TrainingArguments)
    run_config={'config':cfg,'data_fingerprint':fp,'stage':stage,'origin_sha256':origin}
    run_fp=signature(run_config);state=out/'run_metadata.json'
    checkpoint=None
    if resume:
        checkpoint=path(resume)
        if checkpoint.parent!=out or not checkpoint.name.startswith('checkpoint-'):
            raise ValueError('仅恢复本训练输出目录内的v2 checkpoint；不能混用其他轮次或v1断点')
        if not state.is_file() or load(state)['fingerprint']!=run_fp:raise ValueError('断点续训配置/数据/起始Adapter变化')
        for name in ['trainer_state.json','optimizer.pt','scheduler.pt']:
            if not (checkpoint/name).is_file():raise ValueError('完整恢复缺少'+name+'；仅有Adapter只能作为新一轮起点')
        if not list(checkpoint.glob('rng_state*.pth')):raise ValueError('完整恢复缺少RNG状态')
        model_files(checkpoint,True)
    elif out.exists() and any(out.iterdir()):raise ValueError('训练输出非空，使用新目录或--resume具体checkpoint')
    prepared={**run_config,'fingerprint':run_fp,'max_tokens':max(len(r['input_ids']) for r in rows+val),'train_rows_used':len(rows),'validation_rows':len(val),
              'supervision':'final assistant tokens plus im_end only; prompt/padding=-100',
              'training_arguments_compatibility':{'passed':True,'keywords':sorted(kwargs),'warmup':{k:kwargs[k] for k in ('warmup_ratio','warmup_steps') if k in kwargs}}}
    if prepare_only:
        print(prepared);return prepared
    if not torch.cuda.is_available():raise RuntimeError('CUDA不可用')
    if torch.cuda.device_count()!=1:raise RuntimeError('本训练入口为单卡QLoRA；请设置CUDA_VISIBLE_DEVICES=0或指定一个物理卡')
    if not torch.cuda.is_bf16_supported():raise RuntimeError('当前默认训练要求BF16 GPU')
    free,total=torch.cuda.mem_get_info()
    if free/2**30<cfg.get('minimum_free_gib',18):raise RuntimeError('空闲显存不足；先手动停止占用同一卡的推理服务')
    set_seed(cfg['seed']);base,dtype=load_base(model_path,4,{'':0},cfg.get('attention_implementation','sdpa'))
    for config_obj in [base.config,getattr(base.config,'text_config',None)]:
        if config_obj is not None:config_obj.use_cache=False
    base.generation_config.use_cache=False
    base=prepare_model_for_kbit_training(base,use_gradient_checkpointing=True,gradient_checkpointing_kwargs={'use_reentrant':False})
    if init=='continue_adapter':model=PeftModel.from_pretrained(base,str(adapter),is_trainable=True,local_files_only=True)
    else:
        targets=cfg.get('target_modules',['q_proj','k_proj','v_proj','o_proj']);names={n.rsplit('.',1)[-1] for n,_ in base.named_modules()}
        if not set(targets)<=names:raise ValueError('新LoRA target_modules与模型不匹配')
        model=get_peft_model(base,LoraConfig(r=cfg.get('rank',16),lora_alpha=cfg.get('alpha',32),lora_dropout=.05,target_modules=targets,bias='none',task_type='CAUSAL_LM'))
    names=[n for n,p in model.named_parameters() if p.requires_grad]
    if not names or any('lora_' not in n and 'modules_to_save' not in n for n in names):raise ValueError('可训练参数不是预期LoRA范围')
    if len(getattr(model,'active_adapters',[]))!=1:raise ValueError('预期一个活动Adapter')
    class Checks(TrainerCallback):
        def on_step_end(self,args,state,control,**kw):
            if state.global_step>=state.max_steps:control.should_evaluate=True;control.should_save=True
            return control
        def on_log(self,args,state,control,logs=None,**kw):
            for key,value in (logs or {}).items():
                if (key.endswith('loss') or key=='grad_norm') and not math.isfinite(float(value)):raise FloatingPointError(key+'非有限')
    kw={'model':model,'args':TrainingArguments(**kwargs),'train_dataset':rows,'eval_dataset':val,
        'data_collator':functools.partial(collate,pad_id=tokenizer.pad_token_id),'callbacks':[Checks()]}
    kw['processing_class' if 'processing_class' in inspect.signature(Trainer.__init__).parameters else 'tokenizer']=tokenizer
    trainer=Trainer(**kw);out.mkdir(parents=True,exist_ok=True);save(state,prepared)
    before=out/'baseline_metrics.json'
    if resume:
        if not before.is_file():raise ValueError('缺少训练前基线，不把恢复模型冒充起始模型')
    else:save(before,trainer.evaluate(metric_key_prefix='before'))
    set_seed(cfg['seed']);torch.cuda.reset_peak_memory_stats();result=trainer.train(resume_from_checkpoint=str(checkpoint) if checkpoint else None)
    metrics=trainer.evaluate(metric_key_prefix='after')
    if not math.isfinite(float(result.training_loss)) or not math.isfinite(float(metrics['after_loss'])):
        raise FloatingPointError('训练或验证loss非有限，拒绝保存候选Adapter')
    if not any(float(log.get('grad_norm',0))>0 for log in trainer.state.log_history):raise RuntimeError('未记录非零梯度')
    if stage=='smoke' and trainer.state.global_step!=5:raise RuntimeError('smoke未完成5个优化器步')
    model.save_pretrained(str(out/'adapter'),safe_serialization=True);tokenizer.save_pretrained(str(out/'adapter'));trainer.save_state()
    final={'stage':stage,'data_fingerprint':fp,'before':load(before),'after':metrics,'train':result.metrics,'global_step':trainer.state.global_step,
           'best_checkpoint':trainer.state.best_model_checkpoint,'best_metric':trainer.state.best_metric,
           'peak_allocated_gib':torch.cuda.max_memory_allocated()/2**30,'peak_reserved_gib':torch.cuda.max_memory_reserved()/2**30,
           'candidate_adapter':str(out/'adapter'),'deployment_status':'not_promoted_requires_generation_comparison',
           'scope':'smoke验证链路/显存/梯度；loss不是报告质量评分，需同一留出样本生成前后对照'}
    save(out/'training_results.json',final);print(final);return final
