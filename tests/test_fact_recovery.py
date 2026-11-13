import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.common import save,load,digest,signature
from report_agent.fact_merge import FactMerger,merge_value
from report_agent.facts import LISTS,extract
from report_agent.readers import ingest
from scripts.recover_facts import recover,apply_decisions

class MergeTests(unittest.TestCase):
    def test_numeric_conflict_not_rounded_or_voted(self):
        self.assertEqual(merge_value('weight',.3,.30),(True,.3))
        self.assertFalse(merge_value('weight',.3,.3001)[0])
        self.assertFalse(merge_value('credits',2,3)[0])
        self.assertFalse(merge_value('credits',True,1)[0])
    def test_typography_and_term_equivalence_only(self):
        self.assertTrue(merge_value('course_name','数字图像处理','数字图像\n处理')[0])
        self.assertFalse(merge_value('course_code','AB C','ABC')[0])
        self.assertTrue(merge_value('academic_year','2023—2024学年','2023-2024')[0])
        self.assertTrue(merge_value('semester','第一学期','1')[0])
        self.assertFalse(merge_value('semester','1','2')[0])
        self.assertTrue(merge_value('term','2023-2024学年第1学期','2023-2024-1')[0])
    def test_teacher_explicit_superset_not_disjoint_union(self):
        self.assertEqual(merge_value('teacher','甲','甲/乙'),(True,'甲/乙'))
        self.assertTrue(merge_value('teacher','甲/乙','乙、甲')[0])
        self.assertFalse(merge_value('teacher','甲','乙')[0])
    def test_partial_mappings_and_terms_detect_conflicting_coefficient(self):
        self.assertEqual(merge_value('goal_weights',{'G1':.2},{'G2':.8}),(True,{'G1':.2,'G2':.8}))
        self.assertFalse(merge_value('goal_weights',{'G1':.2},{'G1':.8})[0])
        a=[{'item_id':'I1','coefficient':.4}];b=[{'item_id':'I2','coefficient':.6}]
        self.assertEqual(merge_value('terms',a,b),(True,a+b))
        self.assertFalse(merge_value('terms',a,[{'item_id':'I1','coefficient':.5}])[0])
        self.assertFalse(merge_value('terms',a,a+a)[0])
    def test_true_conflict_has_both_original_sources_and_stable_id(self):
        facts={k:[] for k in LISTS};facts.update(metadata={},conflicts=[])
        ev={f's:{i}':{'id':f's:{i}','source_id':'s','text':f'学分：{i}'} for i in (2,3)}
        m=FactMerger(facts,ev)
        for i in (2,3,3):m.add({'kind':'metadata','value':{'credits':i,'source_refs':[f's:{i}'],'quotes':{f's:{i}':ev[f's:{i}']['text']}}},f'task{i}')
        self.assertEqual(len(facts['conflicts']),1)
        self.assertEqual(len(facts['conflicts'][0]['candidates']),2)
        with tempfile.TemporaryDirectory() as t:
            report=m.write_review(Path(t),[{'id':'s','path':'source.docx'}])
            self.assertIn('s:2',(Path(t)/'fact_conflicts.txt').read_text())
            self.assertEqual(report['conflicts'][0]['candidates'][1]['locations'][0]['path'],'source.docx')
            decisions={'report_sha256':signature(report),'reviewer':'教师','decisions':[{'conflict_id':facts['conflicts'][0]['conflict_id'],'candidate':2,'reason':'本轮制度明确3学分'}]}
            apply_decisions(facts,report,decisions)
            self.assertFalse(facts['conflicts']);self.assertEqual(facts['metadata']['credits'],3)
            self.assertEqual(facts['conflict_resolutions'][0]['selected_evidence']['source_refs'],['s:3'])
    def test_same_entity_id_different_name_not_silently_renamed(self):
        self.assertFalse(merge_value('name','实验','期末考试')[0])
        self.assertFalse(merge_value('text','掌握基础概念','能独立设计系统')[0])

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.case={'id':'c','course_name':'数字图像处理','sources':[],'extract_chunk_chars':2000}
        for i,credits in enumerate((2,2),1):
            f=self.root/f's{i}.txt';f.write_text(f'课程名称：数字图像处理；学分：{credits}'+('；另一个待核对版本学分：3' if i==2 else ''),encoding='utf-8')
            self.case['sources'].append({'id':f's{i}','path':str(f)})
        self.casepath=self.root/'case.json';save(self.casepath,self.case)
        self.sources=ingest(self.case);self.run=self.root/'old';self.run.mkdir()
        self.contract={'template_id':'T','assets':{},'asset_sha256':{}}
        cp=self.root/'contract.json';save(cp,self.contract)
        registry=self.root/'registry.json';save(registry,{'templates':{'course':{'contract':str(cp)}}})
        self.manifest={'case_id':'c','offline':False,'inputs':{s['id']:s['sha256'] for s in self.sources},
                       'template_id':'T','template_hashes':{'contract':digest(cp)},'runtime_config':{'template_registry':str(registry)}}
        save(self.run/'run_manifest.json',self.manifest);save(self.run/'source_manifest.json',self.sources)
        class Fake:
            config={};identity={};count=0
            def require_structured(inner):pass
            def log(inner,*a):pass
            def request(inner,messages,**kw):
                inner.count+=1;p=json.loads(messages[-1]['content'])
                answer={'records':[{'kind':'metadata','value':{'course_name':'数字图像处理','credits':2},'evidence_ids':[p['evidence'][0]['id']]}]}
                return {'text':json.dumps(answer,ensure_ascii=False),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        self.fake=Fake()
        extract(self.case,self.sources,self.fake,self.run,'T',template_contract=self.contract)
    def tearDown(self):self.tmp.cleanup()
    def test_full_cached_recovery_and_new_case_no_model(self):
        with patch('report_agent.client.Client.request',side_effect=AssertionError('不得请求模型')):
            code=recover(str(self.casepath),str(self.run),str(self.root/'recovery'))
        self.assertEqual(code,0)
        f=load(self.root/'recovery/facts.recovered.json');self.assertFalse(f['review']['approved'])
        self.assertEqual(load(self.root/'recovery/recovery_result.json')['batches_reused'],2)
        self.assertFalse(load(self.root/'recovery/runtime.recovered.json')['reference_library']['enabled'])
        self.assertEqual(load(self.casepath),self.case)
    def test_policy_preflights_stage_four_without_model(self):
        cfg={'policy_id':'test','case_id':'c','course_name':'数字图像处理',
             'evidence_anchors':{},'metadata_sources':{'course_name':'s1'},'methods':[]}
        pp=self.root/'policy.json';save(pp,cfg)
        code=recover(str(self.casepath),str(self.run),str(self.root/'preflight'),policy_path=str(pp))
        self.assertEqual(code,2)
        self.assertEqual(load(self.root/'preflight/recovery_result.json')['status'],'facts_ready_generation_blocked')
        self.assertFalse((self.root/'preflight/next_command.txt').exists())
        self.assertEqual(load(self.root/'preflight/next_stage_check.json')['model_requests'],0)
    def test_changed_source_or_incomplete_audit_refused(self):
        f=Path(self.case['sources'][0]['path']);f.write_text('已修改',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'材料已改变'):recover(str(self.casepath),str(self.run),str(self.root/'bad'))
    def test_missing_accepted_record_is_not_empty_fact(self):
        (self.run/'records/extract_0001.json').unlink()
        with self.assertRaisesRegex(ValueError,'缺少通过'):recover(str(self.casepath),str(self.run),str(self.root/'bad'))
    def test_true_conflict_generates_review_not_next_command(self):
        p=self.run/'records/extract_0001.json';r=load(p);r['answer']['records'][0]['value']['credits']=3;save(p,r)
        # 两个候选数字均在合成证据中出现；未获证据支持的数字应由来源校验直接拒绝。
        code=recover(str(self.casepath),str(self.run),str(self.root/'review'))
        self.assertEqual(code,2);self.assertFalse((self.root/'review/next_command.txt').exists())
        self.assertTrue((self.root/'review/conflict_review.zip').exists())
    def test_case_identity_or_template_change_refused(self):
        cp=self.root/'contract.json';save(cp,{**self.contract,'extra':'changed'})
        with self.assertRaisesRegex(ValueError,'模板已改变'):recover(str(self.casepath),str(self.run),str(self.root/'bad'))

if __name__=='__main__':unittest.main()
