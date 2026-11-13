import copy
import tempfile
import unittest
from pathlib import Path
from report_agent.common import load,save,signature,digest
from report_agent.client import task
from report_agent.extraction_reuse import ExtractionReuse


class ExtractionReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old=self.root/'old';self.new=self.root/'new'
        self.manifest={'case_id':'c','group_id':'g','inputs':{'s':'hash'},'template_id':'t',
                       'template_hashes':{'contract':'hash'},'model':{'adapter_sha256':'hash'},
                       'reference_library':{'sha256':'refhash'},'runtime_config':{'model':{}},'offline':False}
        save(self.old/'run_manifest.json',self.manifest)
        self.schema={'type':'object','properties':{'records':{'type':'array','items':{'type':'string'}}},'required':['records'],'additionalProperties':False}
        self.messages=[{'role':'user','content':'当前证据'}]
        self.metadata={'task_kind':'extract','case_id':'c','group_id':'g','template_id':'t'}
        self.record={**self.metadata,'messages':self.messages,'output_schema':self.schema,
                     'request_hash':signature({'messages':self.messages,'output_schema':self.schema}),
                     'ok':True,'answer':{'records':['证据']},'review':{'approved':True,'reviewer':'previous'},
                     'attempts':[{'raw':{'text':'{"records":["证据"]}'},'error':None,'messages':self.messages}]}
        save(self.old/'records/extract_0000.json',self.record)
    def tearDown(self):self.tmp.cleanup()
    def client(self):
        class Client:
            config={};identity={};calls=0
            def log(self,*args):pass
            def request(self,messages,**kwargs):
                self.calls+=1
                return {'text':'{"records":["新结果"]}','schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kwargs['output_schema'])}
        c=Client();c.extraction_reuse=ExtractionReuse(self.old,self.manifest,self.new);return c
    def run_task(self,c,validate=lambda _:None,messages=None,schema=None):
        return task(c,messages or self.messages,self.new/'records/new.json',validate,self.metadata,
                    retries=0,output_schema=schema or self.schema)
    def test_exact_match_revalidates_without_request_preserves_origin(self):
        before=digest(self.old/'records/extract_0000.json');seen=[];c=self.client()
        self.assertEqual(self.run_task(c,lambda a:seen.append(a)),self.record['answer'])
        self.assertEqual(c.calls,0);self.assertEqual(len(seen),1)
        saved=load(self.new/'records/new.json');self.assertFalse(saved['review']['approved'])
        self.assertEqual(saved['attempts'],self.record['attempts']);self.assertIn('reused_from',saved)
        self.assertEqual(before,digest(self.old/'records/extract_0000.json'))
        c=self.client();self.run_task(c);self.assertEqual(c.calls,0)
        self.assertEqual(len(load(self.new/'extraction_reuse.json')['reused']),1)
    def test_changed_prompt_requests_model(self):
        c=self.client();self.run_task(c,messages=[{'role':'user','content':'新证据'}]);self.assertEqual(c.calls,1)
    def test_changed_schema_requests_model(self):
        schema=copy.deepcopy(self.schema);schema['properties']['records']['maxItems']=3
        c=self.client();self.run_task(c,schema=schema);self.assertEqual(c.calls,1)
    def test_failed_record_and_bad_hash_are_never_reused(self):
        for changes in ({'ok':False},{'request_hash':'wrong'}):
            save(self.old/'records/extract_0000.json',{**self.record,**changes})
            pool=ExtractionReuse(self.old,self.manifest,self.new);self.assertEqual(pool.rows,{})
    def test_current_validation_rejects_old_semantics_then_generates(self):
        def validate(a):
            if a['records']==['证据']:raise ValueError('新校验拒绝旧回答')
        c=self.client();self.assertEqual(self.run_task(c,validate),{'records':['新结果']});self.assertEqual(c.calls,1)
        self.assertEqual(len(load(self.new/'extraction_reuse.json')['rejected']),1)
    def test_changed_identity_inputs_template_reference_runtime_are_blocked(self):
        for key in ('inputs','model','template_hashes','reference_library','runtime_config','case_id','group_id'):
            changed={**self.manifest,key:'changed'}
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'不一致'):
                ExtractionReuse(self.old,changed,self.new)
    def test_cannot_overwrite_source_or_use_offline_manifest(self):
        with self.assertRaisesRegex(ValueError,'必须不同'):ExtractionReuse(self.old,self.manifest,self.old)
        save(self.old/'run_manifest.json',{**self.manifest,'offline':True})
        with self.assertRaisesRegex(ValueError,'真实在线'):ExtractionReuse(self.old,self.manifest,self.new)
    def test_auxiliary_record_requires_same_prompt_and_current_content_validation(self):
        self.metadata={**self.metadata,'task_kind':'write_section'}
        save(self.old/'records/section_6_2.json',{**self.record,**self.metadata})
        c=self.client();self.run_task(c);self.assertEqual(c.calls,0)
        saved=load(self.new/'records/new.json');self.assertEqual(saved['task_kind'],'write_section')
        self.assertFalse(saved['review']['approved'])
    def test_auxiliary_record_changed_prompt_is_generated(self):
        self.metadata={**self.metadata,'task_kind':'teaching_design'}
        save(self.old/'records/teaching_proposal.json',{**self.record,**self.metadata})
        c=self.client();self.run_task(c,messages=[{'role':'user','content':'新的参考候选'}])
        self.assertEqual(c.calls,1)
