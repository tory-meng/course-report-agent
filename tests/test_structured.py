import json
import string
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.common import load,signature
from report_agent.client import Client,task,TaskValidationError
from report_agent.structured import extraction_schema,hydrate_records,validate_schema
from report_agent.facts import plan_chunks,validate_extracted,extract


BLOCK={'id':'s:1','source_id':'s','role':'current','text':'课程名称：测试课；任课教师：甲/乙。','extract_kinds':['metadata']}
ANSWER={'records':[{'kind':'metadata','value':{'course_name':'测试课'},'evidence_ids':['s:1']}]}

class StructuredTests(unittest.TestCase):
    def test_actual_token_filter_rejects_bad_structure_and_early_eos(self):
        from model_service.structured import make_parser
        from lmformatenforcer.tokenenforcer import TokenEnforcer,TokenEnforcerTokenizerData
        text=json.dumps(ANSWER,ensure_ascii=False,separators=(',',':'))
        chars=list(dict.fromkeys(string.printable+'测试课程名称甲乙'))
        ids={c:i+1 for i,c in enumerate(chars)};rev={v:k for k,v in ids.items()}
        data=TokenEnforcerTokenizerData([(v,k,False) for k,v in ids.items()],lambda tokens:''.join(rev.get(t,'') for t in tokens),0,False,len(chars)+1)
        enforcer=TokenEnforcer(data,make_parser(extraction_schema([BLOCK],['metadata']),data.tokenizer_alphabet))
        tokens=[99999]
        for i,c in enumerate(text):
            allowed=enforcer.get_allowed_tokens(tokens).allowed_tokens
            self.assertIn(ids[c],allowed,(i,text[:i]))
            self.assertNotIn(0,allowed,'不能在结构完成前EOS')
            tokens.append(ids[c])
        self.assertIn(0,enforcer.get_allowed_tokens(tokens).allowed_tokens)
        def allows(candidate):
            e=TokenEnforcer(data,make_parser(extraction_schema([BLOCK],['metadata']),data.tokenizer_alphabet));seq=[99999]
            for c in candidate:
                if ids[c] not in e.get_allowed_tokens(seq).allowed_tokens:return False
                seq.append(ids[c])
            return 0 in e.get_allowed_tokens(seq).allowed_tokens
        self.assertFalse(allows('{"metadata":{}}'))
        self.assertFalse(allows(text.replace('"evidence_ids":[','"evidence_ids":{')))
        self.assertFalse(allows(text.replace('s:1','s:2')))
        self.assertFalse(allows(text[:-2]+',{}]}'))
    def test_nested_numeric_and_entity_shapes_allow_chinese(self):
        from model_service.structured import make_parser
        variants=[('metadata',{'course_name':'测试课','credits':2.0}),
                  ('goals',{'id':'G1','text':'测试目标','threshold':None}),
                  ('items',{'id':'I1','name':'期末','assessment_id':'A1','max_score':100,'score_basis':'raw','goal_weights':{'G1':.3}}),
                  ('goal_rules',{'id':'G1','terms':[{'item_id':'I1','coefficient':.3}]})]
        answer={'records':[{'kind':k,'value':v,'evidence_ids':['s:1']} for k,v in variants]}
        text=json.dumps(answer,ensure_ascii=False,separators=(',',':'))
        parser=make_parser(extraction_schema([BLOCK]),''.join(set(text)))
        for i,c in enumerate(text):
            self.assertIn(c,parser.get_allowed_characters(),(i,text[:i]))
            parser=parser.add_character(c)
        self.assertTrue(parser.can_end())
        validate_schema(answer,extraction_schema([BLOCK]))

    def test_hydration_uses_original_not_model_quote(self):
        r=hydrate_records(ANSWER,[BLOCK])[0]
        self.assertEqual(r['value']['quotes'],{'s:1':BLOCK['text']})
        self.assertEqual(validate_extracted(ANSWER,[BLOCK],{'s:1':BLOCK})[0],r)
        bad=json.loads(json.dumps(ANSWER));bad['records'][0]['value']['course_name']='编造课程'
        with self.assertRaises(ValueError):validate_extracted(bad,[BLOCK],{'s:1':BLOCK})
    def test_schema_rejects_old_source_shapes_and_unknown_refs(self):
        schema=extraction_schema([BLOCK],['metadata'])
        for answer in [{'metadata':{}},{'records':[{'kind':'metadata','value':{},'evidence_ids':{'s:1':'原文'}}]},
                       {'records':[{'kind':'metadata','value':{},'evidence_ids':['s:2']}]},
                       {'records':[{'kind':'metadata','value':{'course_name':'测试课','quotes':{}},'evidence_ids':['s:1']}]}]:
            with self.subTest(answer=answer),self.assertRaises(ValueError):validate_schema(answer,schema)
    def test_student_scores_excluded_context_retained(self):
        rows=[['课程名称：测试课'],['学号','姓名','专业','班级','总评'],['1234','张三','软件工程','A班',80],['5678','李四','软件工程','A班',90],['说明：满分100']]
        blocks=[{'id':f's:Sheet1:R{i}','source_id':'s','role':'current','text':' | '.join(f'C{j}={v}' for j,v in enumerate(row,1))} for i,row in enumerate(rows,1)]
        source={'id':'s','role':'current','blocks':blocks,'sheets':[{'name':'Sheet1','rows':rows}]}
        _,chunks=plan_chunks({},[source]);text='\n'.join(b['text'] for chunk in chunks for b in chunk)
        self.assertNotIn('张三',text);self.assertNotIn('李四',text);self.assertNotIn('C5=80',text)
        self.assertIn('软件工程',text);self.assertIn('A班',text);self.assertIn('满分100',text)
        self.assertTrue(all(len({b['id'] for b in ch})==len(ch) for ch in chunks))
        self.assertEqual(source['sheets'][0]['rows'][2][4],80)
    def test_old_server_rejected_without_generation(self):
        c=Client.__new__(Client);c.identity={'features':[]};c.config={}
        with self.assertRaisesRegex(RuntimeError,'尚未启用'):c.request([],output_schema={})
    def test_structured_task_requires_schema_echo(self):
        class Fake:
            config={};identity={}
            def request(self,*a,**kw):return {'text':json.dumps(ANSWER)}
            def log(self,*a):pass
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError,'未确认'):task(Fake(),[{'role':'user','content':'证据'}],Path(d)/'r.json',lambda x:None,{},output_schema=extraction_schema([BLOCK]))
            self.assertFalse(load(Path(d)/'r.json')['ok'])
    def test_teacher_subset_and_true_conflict_are_distinguished(self):
        class Fake:
            config={};identity={}
            def require_structured(self):pass
            def log(self,*a):pass
            def request(self,messages,**kw):
                b=json.loads(messages[-1]['content'])['evidence'][0]
                teacher=b['text'].split('教师：')[1]
                a={'records':[{'kind':'metadata','value':{'course_name':'测试课','teacher':teacher},'evidence_ids':[b['id']]}]}
                return {'text':json.dumps(a),'schema_enforced':True,'schema_sha256':signature(kw['output_schema'])}
        for later,expected_status in [('甲/乙','passed'),('乙','needs_review')]:
            sources=[{'id':sid,'role':'current','sheets':[],'blocks':[{'id':sid+':1','source_id':sid,'role':'current','text':'课程名称：测试课；教师：'+teacher}]} for sid,teacher in [('s','甲'),('t',later)]]
            with tempfile.TemporaryDirectory() as d:
                facts=extract({'id':'c','course_name':'测试课'},sources,Fake(),Path(d),'T',max_batches=2)
                self.assertEqual(load(Path(d)/'probe_validation.json')['status'],expected_status)
                if expected_status=='passed':self.assertEqual(facts['metadata']['teacher'],'甲/乙')

    def test_schema_hash_prevents_wrong_cache_reuse(self):
        class Fake:
            config={};identity={}
            def request(self,*a,**kw):return {'text':json.dumps(ANSWER),'schema_enforced':True,'schema_sha256':signature(kw['output_schema'])}
            def log(self,*a):pass
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'r.json';messages=[{'role':'user','content':'证据'}]
            task(Fake(),messages,p,lambda x:None,{},output_schema=extraction_schema([BLOCK],['metadata']))
            with self.assertRaisesRegex(ValueError,'输入改变'):task(Fake(),messages,p,lambda x:None,{},output_schema=extraction_schema([BLOCK]))
    def test_structured_format_failure_no_recursive_split(self):
        class Fake:
            config={};identity={}
            def require_structured(self):pass
            def log(self,*a):pass
            def request(self,*a,**kw):return {'text':'{"records":[','schema_enforced':True,'schema_sha256':signature(kw['output_schema'])}
        source={'id':'s','role':'current','blocks':[{**BLOCK,'text':BLOCK['text']*20}],'sheets':[]}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(TaskValidationError):extract({'id':'c','course_name':'测试课'},[source],Fake(),Path(d),'T')
            self.assertEqual(len(list((Path(d)/'records').glob('*.json'))),1)
            self.assertEqual(len(load(Path(d)/'records/extract_0000.json')['attempts']),2)
    def test_probe_resume_reuses_cache_and_keeps_unreviewed(self):
        class Fake:
            config={};identity={};calls=0
            def require_structured(self):pass
            def log(self,*a):pass
            def request(self,messages,**kw):
                self.calls+=1
                prompt=json.loads(messages[-1]['content']);blocks=prompt['evidence']
                answer={'records':[{'kind':'metadata','value':{'course_name':'测试课'},'evidence_ids':[blocks[0]['id']]}]}
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_sha256':signature(kw['output_schema'])}
        source={'id':'s','role':'current','blocks':[BLOCK],'sheets':[]};fake=Fake()
        with tempfile.TemporaryDirectory() as d:
            extract({'id':'c','course_name':'测试课'},[source],fake,Path(d),'T',max_batches=2)
            self.assertEqual(load(Path(d)/'probe_validation.json')['checked_batches'],1)
            facts=extract({'id':'c','course_name':'测试课'},[source],fake,Path(d),'T')
            self.assertEqual(fake.calls,1);self.assertFalse(facts['review']['approved'])
            self.assertTrue((Path(d)/'facts.extracted.json').is_file())

if __name__=='__main__':unittest.main()
