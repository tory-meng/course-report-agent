"""真实执行生产load_backend路径，不依赖已移除的Transformers类导入。"""
import builtins
import json
import string
import unittest
from unittest.mock import patch
from model_service.structured import load_backend,build_tokenizer_data

class Tokenizer:
    def __init__(self):
        self.chars=list(dict.fromkeys(string.printable+'测试课程甲乙'))
        self.forward={c:i+1 for i,c in enumerate(self.chars)}
        self.reverse={v:k for k,v in self.forward.items()}
        self.eos_token_id=0;self.all_special_ids=[0]
    def __len__(self):return len(self.chars)+1
    def encode(self,s,**kwargs):return [self.forward[c] for c in s]
    def decode(self,tokens,**kwargs):return ''.join(self.reverse.get(t,'<eos>') for t in tokens)

class TensorLike:
    def __init__(self,tokens):self.tokens=tokens
    def tolist(self):return list(self.tokens)

class BackendCompatTests(unittest.TestCase):
    def test_production_backend_never_imports_transformers_integration(self):
        original=builtins.__import__;tokenizer=Tokenizer()
        def guarded(name,*args,**kwargs):
            if name.startswith('transformers') or name.startswith('lmformatenforcer.integrations'):
                raise AssertionError('不应导入旧集成模块：'+name)
            return original(name,*args,**kwargs)
        schema={'type':'object','properties':{'course':{'type':'string'}},'required':['course'],'additionalProperties':False}
        with patch('builtins.__import__',side_effect=guarded):
            factory=load_backend(tokenizer);callback=factory(schema)
            sequence=[99999]
            for c in json.dumps({'course':'测试课程'},ensure_ascii=False,separators=(',',':')):
                allowed=callback(0,TensorLike(sequence))
                self.assertIn(tokenizer.forward[c],allowed)
                self.assertNotIn(0,allowed)
                sequence.append(tokenizer.forward[c])
            self.assertIn(0,callback(0,TensorLike(sequence)))
            other=factory(schema)
            self.assertIsNot(other.token_enforcer,callback.token_enforcer)
            self.assertNotIn(0,other(0,TensorLike([88888])))
    def test_invalid_eos_fails_before_loading_model(self):
        tokenizer=Tokenizer();tokenizer.eos_token_id=None
        with self.assertRaisesRegex(ValueError,'eos_token_id'):build_tokenizer_data(tokenizer)
    def test_import_failure_is_not_misreported_as_missing_transformers(self):
        original=builtins.__import__
        def broken(name,*args,**kwargs):
            if name=='lmformatenforcer.tokenenforcer':raise ImportError('模拟接口不兼容')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'接口不兼容.*不是未安装'):load_backend(Tokenizer())

if __name__=='__main__':unittest.main()
