"""路径、原子写入、指纹；所有相对配置路径均相对项目根目录。"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def path(value):
    p = Path(value).expanduser()
    return (p if p.is_absolute() else ROOT / p).resolve()

def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))

def save(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    tmp.replace(p)

def digest(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()

def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()

def read_rows(p):
    return [json.loads(s) for s in Path(p).read_text(encoding='utf-8-sig').splitlines() if s.strip()]

def write_rows(p, rows):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False) + '\n' for r in rows), encoding='utf-8')

def require_empty(p):
    p = Path(p)
    if p.exists() and any(p.iterdir()): raise ValueError(f'输出目录非空：{p}；使用新目录或显式--resume')
    p.mkdir(parents=True, exist_ok=True)

def number(v):
    import math
    if isinstance(v, bool): raise ValueError('布尔值不是成绩')
    x = float(v)
    if not math.isfinite(x): raise ValueError('数值必须有限')
    return x

def model_files(directory, adapter=False):
    d = Path(directory)
    cfg = d / ('adapter_config.json' if adapter else 'config.json')
    if not cfg.is_file(): raise ValueError(f'缺少模型配置：{cfg}')
    names = ('adapter_model.safetensors', 'adapter_model.bin') if adapter else ('model.safetensors', 'pytorch_model.bin')
    index_names = ('adapter_model.safetensors.index.json', 'adapter_model.bin.index.json') if adapter else ('model.safetensors.index.json', 'pytorch_model.bin.index.json')
    files = [cfg]
    index = next((d / n for n in index_names if (d / n).is_file()), None)
    if index:
        files += [index] + [d / n for n in sorted(set(load(index)['weight_map'].values()))]
    else:
        weight = next((d / n for n in names if (d / n).is_file()), None)
        if weight is None: raise ValueError(f'缺少完整权重或分片索引：{d}')
        files.append(weight)
    for f in files:
        if not f.is_file() or f.stat().st_size == 0: raise ValueError(f'缺少/空权重文件：{f}')
    if adapter and load(cfg).get('peft_type') != 'LORA': raise ValueError('当前入口仅支持LoRA Adapter')
    return files
