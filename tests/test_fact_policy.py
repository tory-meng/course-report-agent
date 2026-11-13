import tempfile
import unittest
from pathlib import Path
from report_agent.fact_merge import FactMerger
from report_agent.fact_policy import FactPolicy
from report_agent.facts import LISTS
from report_agent.compute import calculate

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.ev={k:{'id':k,'source_id':k.split(':')[0],'role':'current','text':v} for k,v in {
            'grade:1':'2023-2024 数字图像处理 行政班甲','other:1':'2024-2025 数字图像处理',
            'rubric:1':'文献调研：10分','plan:1':'对文献进行分析','lesson:1':'教师讲授','lesson:2':'教学反思：学生积极参与'}.items()}
        self.cfg={'policy_id':'test','case_id':'c','course_name':'数字图像处理','evidence_anchors':{'grade:1':'2023-2024 数字图像处理'},
            'excluded_sources':{'other':'不同学年'},'metadata_sources':{'class_name':'grade'},
            'deferred_fields':{'course_nature':'矛盾待核对'},
            'item_names':{'I1':{'canonical':'文献调研','aliases':['文献调研','对文献进行分析'],'canonical_ref':'rubric:1'}},
            'clear_global_assessment_weights':True,'narrative_allow_refs':['lesson:2'],'remaining_issues':['目标缺失']}
        self.case={'id':'c','course_name':'数字图像处理'}
    def record(self,kind,value,ref):return {'kind':kind,'value':{**value,'source_refs':[ref],'quotes':{ref:self.ev[ref]['text']}}}
    def test_guard_rejects_other_case_or_changed_anchor(self):
        with self.assertRaises(ValueError):FactPolicy(self.cfg,{**self.case,'id':'other'},self.ev)
        with self.assertRaises(ValueError):FactPolicy(self.cfg,self.case,{**self.ev,'grade:1':{**self.ev['grade:1'],'text':'不同课程'}})
    def test_quarantine_and_metadata_authority_are_explicit(self):
        p=FactPolicy(self.cfg,self.case,self.ev)
        self.assertIsNone(p.apply(self.record('metadata',{'class_name':'旧班'},'other:1')))
        self.assertIsNone(p.apply(self.record('metadata',{'class_name':'别名'},'lesson:1')))
        self.assertEqual(p.apply(self.record('metadata',{'class_name':'甲'},'grade:1'))['value']['class_name'],'甲')
    def test_item_alias_keeps_canonical_evidence_and_defers_global_weight(self):
        p=FactPolicy(self.cfg,self.case,self.ev)
        r=p.apply(self.record('items',{'id':'I1','name':'对文献进行分析','total_weight':.1},'plan:1'))
        self.assertEqual(r['value']['name'],'文献调研');self.assertIn('rubric:1',r['value']['source_refs'])
        self.assertNotIn('total_weight',r['value'])
        with self.assertRaises(ValueError):p.apply(self.record('items',{'id':'I1','name':'未知项目'},'plan:1'))
    def test_non_effect_narrative_quarantined_and_review_not_auto_approved(self):
        p=FactPolicy(self.cfg,self.case,self.ev)
        self.assertIsNone(p.apply(self.record('narratives',{'id':'N1','section':'6.2','text':'教师讲授'},'lesson:1')))
        self.assertIsNotNone(p.apply(self.record('narratives',{'id':'N1','section':'6.2','text':'学生积极参与'},'lesson:2')))
        self.assertIsNone(p.apply(self.record('metadata',{'course_nature':'必修'},'lesson:1')))
        p.apply(self.record('metadata',{'class_name':'甲'},'grade:1'))
        facts={'methods':[]}
        with tempfile.TemporaryDirectory() as t:p.finish(facts,[],Path(t))
        self.assertFalse(facts['review']['approved']);self.assertEqual(facts['data_quality_issues'],['目标缺失'])
    def test_same_narrative_id_different_text_is_preserved_without_conflict(self):
        f={k:[] for k in LISTS};f.update(metadata={},conflicts=[]);m=FactMerger(f,self.ev)
        for text,ref in [('教师讲授','lesson:1'),('学生积极参与','lesson:2'),('学生积极参与','lesson:2')]:
            m.add(self.record('narratives',{'id':'N11-1','section':'6.2','text':text},ref))
        self.assertEqual(len(f['narratives']),2);self.assertEqual(f['conflicts'],[])
        self.assertEqual(f['narratives'][0]['source_refs'],['lesson:1'])
        self.assertEqual(f['narratives'][1]['source_refs'],['lesson:2'])
    def test_data_issues_propagate_to_calculation(self):
        c={'evaluation_policy':{'source_refs':['s:1'],**{k:'include' for k in ['normal','missing','absent','deferred','exempt']}}}
        f={'metadata':{'total_max':100},'data_quality_issues':['课程性质待核对']}
        result=calculate(c,f,{'students':[],'warnings':[]})
        self.assertIn('课程性质待核对',result['issues'])
