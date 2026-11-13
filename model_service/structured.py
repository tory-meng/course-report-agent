"""用LM Format Enforcer约束解码；缺依赖或校验失败时不静默回退。"""
import json
from report_agent.common import signature

class StructuredDecodeError(RuntimeError):
    pass

def make_parser(schema, alphabet):
    from lmformatenforcer import JsonSchemaParser
    from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig
    # 0.11.3会缓存初始字符串字符集；必须在构造时传入中文词表字符集。
    return JsonSchemaParser(schema,config=CharacterLevelParserConfig(alphabet=alphabet))


def build_tokenizer_data(tokenizer):
    """桥接tokenizer公共接口与LMFE核心，不导入其旧Transformers集成模块。"""
    from lmformatenforcer.tokenenforcer import TokenEnforcerTokenizerData
    vocab_size=len(tokenizer);special=set(tokenizer.all_special_ids)
    eos=tokenizer.eos_token_id
    if not isinstance(eos,int) or not 0<=eos<vocab_size:
        raise ValueError('tokenizer必须提供有效的整数eos_token_id')
    anchor=tokenizer.encode('0',add_special_tokens=False)
    if not anchor:raise ValueError('tokenizer无法编码词表锚点0')
    anchor_id=anchor[-1]
    def decode(tokens):
        return tokenizer.decode(tokens,skip_special_tokens=False,clean_up_tokenization_spaces=False)
    anchor_text=decode([anchor_id])
    if not anchor_text:raise ValueError('tokenizer词表锚点解码为空')
    regular=[]
    for token_id in range(vocab_size):
        if token_id in special:continue
        contextual=decode([anchor_id,token_id])
        if not contextual.startswith(anchor_text):
            raise ValueError(f'tokenizer不支持稳定前缀解码，token_id={token_id}；请提供tokenizer信息')
        after=contextual[len(anchor_text):]
        standalone=decode([token_id])
        regular.append((token_id,after,len(after)>len(standalone)))
        if (token_id+1)%20000==0:
            print(f'JSON约束词表：{token_id+1}/{vocab_size}',flush=True)
    # UTF-8字节片段尚未组合完成时，暂不把替代字符交给字符级解析器。
    def decode_generated(tokens):return decode(tokens).rstrip('�')
    data=TokenEnforcerTokenizerData(regular,decode_generated,eos,False,vocab_size)
    # 根节点只含各token的首字符；字符串后续字符也必须可接受。
    data.tokenizer_alphabet=''.join(sorted(set(''.join(t[1] for t in regular))))
    return data


class PrefixAllowedTokens:
    """符合model.generate(prefix_allowed_tokens_fn=...)的调用签名。"""
    def __init__(self,data,parser,schema):
        from lmformatenforcer.tokenenforcer import TokenEnforcer
        self.token_enforcer=TokenEnforcer(data,parser)
        self.schema=schema
        self.prompt_length=None
    def __call__(self,batch_id,sent):
        sequence=sent.tolist()
        if self.prompt_length is None:self.prompt_length=len(sequence)
        allowed=self.token_enforcer.get_allowed_tokens(sequence).allowed_tokens
        if self.token_enforcer.eos_token_id in allowed:
            from report_agent.client import json_answer
            from report_agent.structured import validate_schema
            text=self.token_enforcer.decoder(sequence[self.prompt_length:])
            try:validate_schema(json_answer(text),self.schema)
            except ValueError as exc:
                # LMFE某些内部异常会退化成允许EOS，禁止将它当作正常结束。
                raise StructuredDecodeError('约束器在JSON完成前允许结束；请运行probe_tokenizer.py；'
                                   f'末尾token_ids={sequence[-16:]}；{exc}') from exc
        return allowed


class _TokenSequence:
    def __init__(self,tokens):self.tokens=tokens
    def tolist(self):return list(self.tokens)


def replay_json(tokenizer,factory,schema,answer,context=''):
    """用真实分词器回放已知合法答案；不加载模型、不调用GPU。"""
    from report_agent.structured import validate_schema
    validate_schema(answer,schema)
    text=json.dumps(answer,ensure_ascii=False,separators=(',',':'))
    tokens=tokenizer.encode(text,add_special_tokens=False)
    decoded=tokenizer.decode(tokens,skip_special_tokens=False,clean_up_tokenization_spaces=False)
    if decoded!=text:raise ValueError('分词器往返解码改变原文，不能保证原样抽取')
    prefix=factory(schema,context+'\n'+text)
    sequence=[tokenizer.eos_token_id]
    for i,token in enumerate(tokens):
        allowed=prefix(0,_TokenSequence(sequence))
        if token not in allowed:
            raise ValueError(f'分词器约束回放失败：步骤{i}，token_id={token}，此前token_ids={sequence[-16:]}')
        sequence.append(token)
    if tokenizer.eos_token_id not in prefix(0,_TokenSequence(sequence)):
        raise ValueError('完整JSON之后仍不允许结束')
    return {'passed':True,'tokens':len(tokens),'characters':len(text)}


def tokenizer_selftest(tokenizer,factory):
    schema={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}
    samples=['ASCII 0.3','示例龘同学','合成示例课程','引号"与反斜杠\\和换行\n','𠀀🙂']
    return [replay_json(tokenizer,factory,schema,{'value':s}) for s in samples]


def load_backend(tokenizer):
    try:
        from lmformatenforcer.tokenenforcer import TokenEnforcer,TokenEnforcerTokenizerData
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError as exc:
        missing=(exc.name or '').split('.')[0]
        if missing in ('lmformatenforcer','jsonschema'):
            raise RuntimeError(f'qwen38缺少{missing}：python -m pip install -r requirements-structured.txt') from exc
        raise RuntimeError(f'结构化解码依赖内部模块缺失：{exc}；请提供完整报错，不要盲目重装torch/transformers') from exc
    except ImportError as exc:
        raise RuntimeError(f'结构化解码接口不兼容：{exc}；依赖已找到，不是未安装') from exc
    print('建立JSON约束词表缓存（使用项目内兼容适配器，仅启动时构建一次）',flush=True)
    data=build_tokenizer_data(tokenizer)
    def prefix(schema,context=''):
        Draft202012Validator.check_schema(schema)
        # 单个token解码中不存在的字，也可能由多个UTF-8片段拼成。
        # 每次请求加入原始材料及schema字符；共享词表树，隔离请求状态。
        import copy
        request_data=copy.copy(data)
        request_data.tokenizer_alphabet=''.join(sorted(set(data.tokenizer_alphabet+context+json.dumps(schema,ensure_ascii=False))))
        return PrefixAllowedTokens(request_data,make_parser(schema,request_data.tokenizer_alphabet),schema)
    return prefix

def response_contract(text,schema):
    if schema is None:return {'schema_enforced':False}
    from report_agent.client import json_answer
    from report_agent.structured import validate_schema
    try:validate_schema(json_answer(text),schema)
    except ValueError as exc:return {'schema_enforced':True,'schema_sha256':signature(schema),'schema_valid':False,'schema_error':str(exc)}
    return {'schema_enforced':True,'schema_sha256':signature(schema),'schema_valid':True}
