"""按本地config.json选择模型架构；推理和训练共用加载规则。"""
from pathlib import Path
from report_agent.common import load,model_files

def tokenizer_directory(base,adapter=None):
    if adapter:
        a=Path(adapter)
        if (a/'tokenizer_config.json').is_file() and ((a/'tokenizer.json').is_file() or (a/'vocab.json').is_file()):return str(a)
    return str(base)

def load_base(base,bits=4,device_map=None,attention='sdpa'):
    import torch
    import transformers
    from transformers import BitsAndBytesConfig
    model_files(base)
    cfg=load(Path(base)/'config.json')
    if cfg.get('quantization_config'):raise ValueError('本入口针对原始基座+运行时bnb量化；预量化模型需另行适配')
    arches=cfg.get('architectures',[])
    if 'Qwen3_5ForConditionalGeneration' in arches:
        cls=getattr(transformers,'Qwen3_5ForConditionalGeneration',None)
        if cls is None:raise RuntimeError('当前transformers不支持本地Qwen3_5架构，使用v1已跑通的qwen38/qwen_ft环境')
    elif any(a in arches for a in ['Qwen3ForCausalLM','Qwen2ForCausalLM']):cls=transformers.AutoModelForCausalLM
    else:raise ValueError('未验证的模型架构：'+str(arches)+'；请提供基座config.json，不修改架构名冒充')
    if not torch.cuda.is_available():raise RuntimeError('CUDA不可用；请在本地GPU环境运行')
    dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    if bits==4:q=BitsAndBytesConfig(load_in_4bit=True,bnb_4bit_quant_type='nf4',bnb_4bit_use_double_quant=True,bnb_4bit_compute_dtype=dtype)
    elif bits==8:q=BitsAndBytesConfig(load_in_8bit=True)
    else:raise ValueError('bits只能是4或8')
    model=cls.from_pretrained(str(base),local_files_only=True,trust_remote_code=False,quantization_config=q,
                             dtype=dtype,device_map=device_map or {'':0},attn_implementation=attention)
    return model,dtype
