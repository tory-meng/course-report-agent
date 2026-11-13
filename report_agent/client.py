"""连接既有/health、/generate服务；超时不自动重复提交长推理。"""
import json
from pathlib import Path
import socket
import re
import threading
import time
import urllib.request
import urllib.error
from .common import save, signature, path
from .request_budget import InputBudgetError, fit_messages, reference_view, dumps

class Client:
    def __init__(self, config):
        self.config = config
        self.base = config['base_url'].rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.identity = self.http('/health', timeout=15)
        expected = config.get('expected_adapter_path')
        if not expected: raise ValueError('先配置expected_adapter_path；禁止自动接受任意服务Adapter')
        for key, wanted in [('adapter_path', str(path(expected))), ('model_path', str(path(config['expected_model_path'])))]:
            actual = self.identity.get(key)
            if not actual or str(Path(actual).expanduser().resolve()) != wanted:
                raise ValueError(f'服务身份不符：{key}={actual}；要求{wanted}')
        if self.identity.get('bits') != config['expected_bits']: raise ValueError('服务量化位数不符')
        if config.get('expected_adapter_sha256') and config['expected_adapter_sha256']!=self.identity.get('adapter_sha256'):
            raise ValueError('服务Adapter内容哈希不符或旧服务不提供哈希；请用v2服务加载选定权重')
        if self.identity.get('status') not in (None, 'ready'): raise ValueError('服务未就绪')

    def http(self, endpoint, payload=None, timeout=30):
        data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
        req=urllib.request.Request(self.base+endpoint,data=data,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'模型接口HTTP {exc.code}: '+exc.read().decode(errors='replace')[:1000]) from exc

    def count_tokens(self, messages, output_tokens):
        if 'count_tokens' not in self.identity.get('features', []):
            raise RuntimeError('模型服务未提供count_tokens，无法保证完整输入预算；请重启本包模型服务')
        return self.http('/count_tokens', {'messages':messages, 'max_new_tokens':output_tokens}, timeout=30)

    def prepare_messages(self, messages, output_tokens):
        return fit_messages(self, messages, output_tokens)

    def request(self, messages, max_new_tokens=None, output_schema=None):
        if output_schema is not None:self.require_structured()
        budget = max_new_tokens or self.config.get('max_new_tokens', 2048)
        if 'count_tokens' in self.identity.get('features', []):
            counts = self.count_tokens(messages, budget)
            hard = int(counts.get('max_input_tokens', self.identity.get('max_input_tokens',8192)))
            context = counts.get('max_context_tokens', self.identity.get('max_context_tokens'))
            usable = min(hard, int(context)-budget) if context is not None else hard
            if counts['input_tokens'] > usable:
                raise InputBudgetError({**counts,'max_input_tokens':hard,'target_tokens':usable,'output_tokens':budget}, messages)
        payload = {'messages': messages, 'max_new_tokens': budget, 'temperature': 0.1, 'do_sample': False}
        if output_schema is not None:payload['output_schema']=output_schema
        finished = threading.Event(); box = {}; started = time.monotonic()
        def generate():
            try: box['result'] = self.http('/generate', payload, timeout=self.config.get('timeout_seconds', 3600))
            except Exception as exc: box['error'] = exc
            finally: finished.set()
        threading.Thread(target=generate, daemon=True).start()
        interval = max(0.01, float(self.config.get('progress_interval_seconds', 15)))
        while not finished.wait(interval):
            elapsed = time.monotonic() - started
            message = f'模型请求等待 {elapsed:.0f} 秒'
            if 'progress' in self.identity.get('features', []):
                try:
                    state = self.http('/progress', timeout=3)
                    message += f"；服务 busy={state.get('busy')}，已输出 {state.get('output_tokens', '?')} tokens"
                except Exception:
                    message += '；暂无法读取服务进度，继续等待原请求'
            self.log(message)
        if 'error' in box:
            exc = box['error']
            if isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(getattr(exc, 'reason', None), (TimeoutError, socket.timeout)):
                raise RuntimeError('请求超时；服务端可能仍在生成。检查/progress后再--resume，不自动重发。') from exc
            raise exc
        result = box['result']
        self.log(f"模型请求完成，耗时 {time.monotonic()-started:.1f} 秒，输出 {result.get('output_tokens', '?')} tokens")
        if not isinstance(result.get('text'), str): raise ValueError('服务返回缺少text')
        return result

    def require_structured(self):
        if 'json_schema' not in self.identity.get('features',[]):
            raise RuntimeError('模型服务尚未启用JSON结构约束。请在qwen38安装requirements-structured.txt，并停止旧服务后重新启动model_service/serve.py。')

    def log(self, message):
        progress = getattr(self, 'progress', None)
        if progress: progress.note(message)
        else: print(message, flush=True)

class TaskValidationError(ValueError):
    def __init__(self, message, splittable=False):
        super().__init__(message)
        self.splittable = splittable


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('JSON含重复键：'+key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError('JSON含非法数值：'+value)


def parse_answer(text):
    """仅移除BOM、完整外层围栏、字符串外尾逗号；不猜测字段或修补截断内容。"""
    changes = []; value = text.strip()
    if value.startswith('\ufeff'):
        value = value.lstrip('\ufeff').strip(); changes.append('removed_bom')
    fence = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n?```', value, flags=re.S | re.I)
    if fence:
        value = fence.group(1).strip(); changes.append('removed_outer_fence')
    # Scan quoted strings so commas inside evidence text are never changed.
    cleaned = []; inside = False; escaped = False
    for i, char in enumerate(value):
        if inside:
            cleaned.append(char)
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': inside = False
        else:
            if char == '"': inside = True
            if char == ',' and value[i+1:].lstrip().startswith(('}', ']')):
                if 'removed_trailing_commas' not in changes: changes.append('removed_trailing_commas')
                continue
            cleaned.append(char)
    result = json.loads(''.join(cleaned), object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(result, dict): raise ValueError('模型回答须为JSON对象')
    return result, changes


def json_answer(text):
    return parse_answer(text)[0]


def task(client, messages, record_path, validate, metadata, retries=2, output_schema=None):
    from .common import load
    record_path = Path(record_path); request_hash = signature(messages if output_schema is None else {'messages':messages,'output_schema':output_schema})
    metadata={**metadata,**({'output_schema':output_schema} if output_schema is not None else {})}
    attempts = []
    if getattr(client, 'progress', None):client.progress.state['task_id']=record_path.stem
    def log(message):
        if hasattr(client, 'log'): client.log(record_path.stem+'：'+message)
        else: print(record_path.stem+'：'+message, flush=True)
    if record_path.is_file():
        previous = load(record_path)
        if previous['request_hash'] != request_hash: raise ValueError('续跑任务输入改变，请用新输出目录')
        attempts = previous.get('attempts', [])
        if previous.get('ok'):
            if output_schema is not None:
                from .structured import validate_schema
                validate_schema(previous['answer'],output_schema)
            validate(previous['answer']); log('复用已通过校验的记录'); return previous['answer']
    reuse=getattr(client,'extraction_reuse',None)
    if not record_path.exists() and reuse and metadata.get('task_kind') in ('extract','write_section','teaching_design','evaluation_design','improvement_design'):
        answer=reuse.take(request_hash,metadata,validate,record_path)
        if answer is not None:
            log('复用旧任务：输入及schema完全一致，已通过当前校验；无需模型请求')
            return answer
    current = messages
    config = getattr(client, 'config', {})
    budget = min(int(config.get('max_new_tokens', 2048)),int(metadata.get('output_budget',config.get('max_new_tokens',2048))))
    cap = int(getattr(client, 'identity', {}).get('max_output_tokens', budget))
    budget = min(budget, cap)
    for attempt in range(retries + 1):
        raw = None; phase = 'request'; changes = []; budget_audit = None
        log(f'请求 {attempt+1}/{retries+1}，输出上限 {budget} tokens')
        try:
            if hasattr(client, 'prepare_messages'):
                current, budget_audit = client.prepare_messages(current, budget)
                log(f"完整输入 {budget_audit['input_tokens']}/{budget_audit['target_tokens']} tokens；"
                    f"预算调整：{','.join(budget_audit['actions']) or '无需调整'}")
            if attempts and 'reference_lookup' in attempts[-1]:
                # 预算控制可能移除了重试参考：只按实际提交内容记录送达，不能把检索当送达。
                prior = attempts[-1]
                prior['reference_delivered_to_retry'] = any(
                    h.get('reference_id') and h['reference_id'] in current[-1]['content']
                    for h in prior['reference_lookup'].get('hits', []))
            raw = client.request(current, max_new_tokens=budget, **({'output_schema':output_schema} if output_schema is not None else {}))
            if output_schema is not None and (raw.get('schema_enforced') is not True or raw.get('schema_sha256')!=signature(output_schema)):
                raise RuntimeError('服务未确认按当前schema约束输出；请核对服务升级，不能继续无约束重试')
            phase = 'parse'
            if raw.get('limit_reached') or raw.get('finish_reason') == 'length':
                phase = 'length'; raise ValueError('输出达到长度上限，不能接受截断JSON')
            if output_schema is not None and raw.get('schema_valid') is False:
                phase='constraint'
                raise ValueError('服务端结构约束校验失败且未达到长度上限；停止重复请求。'+str(raw.get('schema_error','请运行分词器诊断')))
            if raw.get('finish_reason') in ('repetition', 'prompt_echo'):
                raise ValueError('输出重复或回显提示词')
            answer, changes = parse_answer(raw['text'])
            phase = 'validate'
            if output_schema is not None:
                from .structured import validate_schema
                validate_schema(answer,output_schema)
            validate(answer)
        except Exception as exc:
            if isinstance(exc, InputBudgetError):
                current = exc.messages; budget_audit = exc.audit; phase = 'input_budget'
            entry = {'raw': raw, 'error': str(exc), 'error_type': type(exc).__name__, 'phase': phase,
                     'messages': current, 'max_new_tokens': budget, 'format_cleanup': changes, 'input_budget':budget_audit}
            if isinstance(exc, json.JSONDecodeError):
                entry['json_error'] = {'line': exc.lineno, 'column': exc.colno, 'position': exc.pos,
                                      'context': exc.doc[max(0,exc.pos-80):exc.pos+80]}
            attempts.append(entry)
            save(record_path, {**metadata, 'messages': messages, 'request_hash': request_hash, 'ok': False, 'attempts': attempts})
            # Transport/budget failures must not submit another generation automatically.
            if phase in ('request', 'input_budget'): raise
            # 最后一次内容错误也保留参考核对结果；没有额外请求时明确标记未送入模型。
            resolver=getattr(client,'reference_resolver',None)
            if attempt == retries and phase=='validate' and metadata.get('task_kind')=='extract' and resolver:
                lookup=resolver(str(exc),messages)
                if lookup is not None:
                    entry['reference_lookup']=lookup;entry['reference_delivered_to_retry']=False
                    save(record_path,{**metadata,'messages':messages,'request_hash':request_hash,'ok':False,'attempts':attempts})
            if attempt == retries or phase=='constraint':
                raise TaskValidationError(f'{record_path.name}: {exc}；原始回答见 {record_path}',
                                          splittable=phase in ('parse','length') if output_schema is None else phase=='length') from exc
            log(f'校验未通过：{exc}；重新生成完整JSON')
            if phase == 'length': budget = min(cap, budget * 2)
            hint = ('上次回答未通过校验：'+str(exc)+
                    '。从原始证据重新生成完整JSON对象；键名和字符串用双引号，禁止尾逗号、注释、Markdown和思考过程；'
                    '遵循原任务完整返回格式；仅填写有依据的事实，不复制示例、不增加字段。来源按原任务要求只选择ID，不重写引文。')
            resolver=getattr(client,'reference_resolver',None)
            lookup = None
            if phase=='validate' and metadata.get('task_kind')=='extract' and resolver:
                lookup=resolver(str(exc),messages)
                if lookup is not None:
                    entry['reference_lookup']=lookup
                    entry['reference_delivered_to_retry']=False
                    save(record_path,{**metadata,'messages':messages,'request_hash':request_hash,'ok':False,'attempts':attempts})
                    log(f'查询参考资料库：{len(lookup["hits"])}条；仅辅助解释字段，不替代本轮证据')
            current = [dict(m) for m in messages]
            try: payload = json.loads(current[-1]['content'])
            except (ValueError,TypeError): payload = None
            if isinstance(payload,dict):
                payload['retry_instruction'] = hint
                if lookup is not None: payload['retry_reference_examples'] = reference_view(lookup)
                current[-1] = {**current[-1], 'content':dumps(payload)}
            else:
                if lookup is not None: hint+='\n参考资料检索结果（仅用于字段解释）：'+dumps(reference_view(lookup))
                current[-1] = {**current[-1], 'content': current[-1]['content']+'\n'+hint}
            continue
        attempts.append({'raw': raw, 'error': None, 'messages': current, 'max_new_tokens': budget, 'format_cleanup': changes, 'input_budget':budget_audit})
        save(record_path, {**metadata, 'messages': messages, 'request_hash': request_hash, 'ok': True,
                          'answer': answer, 'attempts': attempts, 'review': {'approved': False, 'reviewer': '', 'answer_isolation_checked': False}})
        log('通过JSON及内容校验'+('；格式清理：'+','.join(changes) if changes else ''))
        return answer
