import json
import tempfile
import unittest
from pathlib import Path
from report_agent.client import task,TaskValidationError
from report_agent.common import signature,load
from report_agent.direct_extract import labelled_row,split_direct
from report_agent.facts import validate_extracted,extract,extraction_blocks
from model_service.structured import load_backend,replay_json,tokenizer_selftest,build_tokenizer_data,make_parser,PrefixAllowedTokens,StructuredDecodeError

class ByteTokenizer:
    eos_token_id=0
    all_special_ids=[0]
    def __len__(self):return 257
    def encode(self,text,**kw):return [b+1 for b in text.encode('utf-8')]
    def decode(self,tokens,**kw):return bytes(t-1 for t in tokens if t).decode('utf-8',errors='replace')

ROW={'id':'s:R2','source_id':'s','role':'current','extract_kinds':['metadata'],
     'text':'C1=课程名称： | C3=数字图像处理 | C9=学期： | C11=2023-2024-1 | C14=教师姓名： | C17=测试龘'}

class ExtractionCompatTests(unittest.TestCase):
    def test_split_utf8_characters_and_escaped_strings(self):
        tokenizer=ByteTokenizer();factory=load_backend(tokenizer)
        self.assertTrue(all(r['passed'] for r in tokenizer_selftest(tokenizer,factory)))
    def test_previous_alphabet_reproduces_early_termination(self):
        tokenizer=ByteTokenizer();data=build_tokenizer_data(tokenizer)
        schema={'type':'object','properties':{'teacher':{'type':'string'}},'required':['teacher']}
        callback=PrefixAllowedTokens(data,make_parser(schema,data.tokenizer_alphabet),schema)
        class Seq:
            def __init__(self,x):self.x=x
            def tolist(self):return self.x
        seq=[0]
        # 逐字节生成的景不在单token字符集中；旧解析器错误地允许EOS。
        with self.assertRaisesRegex(StructuredDecodeError,'完成前允许结束'):
            for token in tokenizer.encode('{"teacher":"测试龘"}'):
                callback(0,Seq(seq));seq.append(token)
    def test_complete_labels_keep_original_name(self):
        values,covered=labelled_row(ROW)
        self.assertTrue(covered);self.assertEqual(values['teacher'],'测试龘')
        direct,rest=split_direct([ROW]);self.assertEqual(len(direct),1);self.assertEqual(rest,[])
        validate_extracted({'records':direct},[ROW],{ROW['id']:ROW})
        direct[0]['value']['teacher']='测试'
        with self.assertRaisesRegex(ValueError,'不能截短'):validate_extracted({'records':direct},[ROW],{ROW['id']:ROW})
    def test_unknown_cells_history_and_roster_are_not_swallowed(self):
        for text in ['C1=专业 | C2=班级 | C3=总评','C1=教师姓名： | C2=学期：',
                     'C1=课程名称：数字图像处理  课程代码：DEMO321212C  学分：2.0',
                     ROW['text']+' | C18=缺考按制度处理']:
            b={**ROW,'text':text};direct,rest=split_direct([b]);self.assertEqual(direct,[]);self.assertEqual(rest,[b])
        self.assertEqual(labelled_row({**ROW,'role':'history'}),({},False))
    def test_ambiguous_labels_not_silently_overwritten(self):
        b={**ROW,'text':'C1=教师姓名：甲 | C2=教师姓名：乙'}
        self.assertEqual(labelled_row(b),({},False))
    def test_non_roster_spreadsheet_keeps_objectives_and_rules(self):
        rows=[['课程目标','考核项','权重'],['G1','实验',0.3]]
        blocks=[{'id':f's:S:R{i}','source_id':'s','role':'rule','text':' | '.join(f'C{j}={v}' for j,v in enumerate(r,1))} for i,r in enumerate(rows,1)]
        source={'id':'s','role':'rule','sheets':[{'name':'S','rows':rows}],'blocks':blocks}
        out=list(extraction_blocks([source]))
        self.assertEqual(len(out),2)
        self.assertIn('goal_rules',out[0]['extract_kinds']);self.assertIn('items',out[1]['extract_kinds'])
    def test_server_invalid_schema_is_fail_fast_and_preserved(self):
        class Fake:
            calls=0;config={};identity={}
            def log(self,*a):pass
            def request(self,*a,**kw):
                self.calls+=1
                return {'text':'{"x":"景','schema_enforced':True,'schema_sha256':signature(kw['output_schema']),
                        'schema_valid':False,'schema_error':'truncated','limit_reached':False}
        c=Fake()
        with tempfile.TemporaryDirectory() as tmp:
            record=Path(tmp)/'r.json'
            with self.assertRaises(TaskValidationError):task(c,[{'role':'user','content':'原文'}],record,lambda a:None,{},output_schema={})
            self.assertEqual(c.calls,1);self.assertEqual(load(record)['attempts'][0]['phase'],'constraint')
    def test_direct_only_is_not_misreported_as_model_probe_success(self):
        class Fake:
            def require_structured(self):pass
            def request(self,*a,**kw):raise AssertionError('明确标签不应调用模型')
        src={'id':'s','role':'current','sheets':[{'name':'S','rows':[]}],'blocks':[ROW]}
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.plan_chunks',return_value=(2000,[[ROW]])):
            f=extract({'id':'c','course_name':'数字图像处理'},[src],Fake(),Path(tmp),'T',max_batches=2)
            self.assertEqual(f['metadata']['teacher'],'测试龘')
            self.assertEqual(load(Path(tmp)/'probe_validation.json')['status'],'needs_review')
            self.assertEqual(load(Path(tmp)/'extraction_audit.json')['direct_only_batches'],1)

if __name__=='__main__':unittest.main()
