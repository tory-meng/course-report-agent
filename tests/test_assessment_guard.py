import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.common import signature
from report_agent.assessment_guard import hierarchy,direct_hierarchy_records,evidence_registry
from report_agent.extraction_quality import GROUPS,schema_for,normalize_entities
from report_agent.structured import extraction_schema,validate_schema
from report_agent.facts import validate_extracted,extract


def block(ref,text):
    return {'id':ref,'text':text,'source_id':'s','role':'current','focus':'assessment',
            '_quality':True,'extract_kinds':GROUPS['assessment']}

def fixture():
    bs=[block('s:T1R1','考核方式 | 考核内容 | 所属项目 | 占比 | 占比')]
    rows=[('过程性考核','课堂表现','20%'),('过程性考核','项目结果','80%'),
          ('终结性考核','项目报告','40%'),('终结性考核','项目答辩','60%')]
    records=[]
    for i,(parent,name,percent) in enumerate(rows,2):
        ref=f's:T1R{i}';bs.append(block(ref,f'{parent} | {name} | 当前评分说明 | 项目一 | {percent} | 50%'))
        aid='A1' if parent=='过程性考核' else 'A2'
        if i in (2,4):records.append({'kind':'assessments','value':{'id':aid,'name':parent},'evidence_ids':[ref]})
        records.append({'kind':'items','value':{'id':f'I{i}','name':name,'assessment_id':aid},'evidence_ids':[ref]})
    return bs,{'records':records}

class AssessmentGuardTests(unittest.TestCase):
    def check(self,answer,bs,known=None):
        return validate_extracted(answer,bs,{b['id']:b for b in bs},known)
    def test_names_and_relationships_can_pass_without_invented_scores(self):
        bs,a=fixture();self.assertEqual(len(hierarchy(bs)),4)
        schema=schema_for(bs,GROUPS['assessment'],extraction_schema(bs,GROUPS['assessment']))
        validate_schema(a,schema);self.check(a,bs)
        variants={v['properties']['kind']['enum'][0]:v['properties']['value']['properties'] for v in schema['properties']['records']['items']['anyOf']}
        self.assertEqual(set(variants),{'items','assessments'})
        self.assertNotIn('terms',variants['assessments'])
        for key in ('total_weight','max_score','converted_max','score_basis','goal_weights'):
            self.assertNotIn(key,variants['items'])
        self.assertNotIn('"not":',json.dumps(schema))
    def test_wrong_parent_name_is_rejected_even_when_already_known(self):
        bs,a=fixture();a['records'][3]['value']['name']='验收答辩'
        with self.assertRaisesRegex(ValueError,'原文名称'):self.check(a,bs)
    def test_child_cannot_belong_to_wrong_parent(self):
        bs,a=fixture();a['records'][-1]['value']['assessment_id']='A1'
        with self.assertRaisesRegex(ValueError,'父级考核'):self.check(a,bs)
    def test_default_maximum_and_goal_distribution_rejected(self):
        for fields in ({'max_score':100},{'converted_max':20},{'goal_weights':{'G1':.2}}):
            bs,a=fixture();a['records'][1]['value'].update(fields)
            with self.assertRaises(ValueError):self.check(a,bs)
    def test_unrelated_full_score_paragraph_does_not_authorize_item_maximum(self):
        bs,a=fixture();bs.append(block('s:P85','实验报告（满分100分）'))
        a['records'][1]['value']['max_score']=100;a['records'][1]['evidence_ids'].append('s:P85')
        with self.assertRaisesRegex(ValueError,'其他段落'):self.check(a,bs)
    def test_empty_and_partial_answers_fail_coverage(self):
        bs,a=fixture()
        for records in ([],a['records'][:-1]):
            with self.assertRaisesRegex(ValueError,'覆盖检查'):self.check({'records':records},bs)
    def test_known_exact_parent_and_canonical_ids_are_accepted(self):
        bs,a=fixture();known={'assessments':[r['value'] for r in a['records'] if r['kind']=='assessments']}
        a['records']=[r for r in a['records'] if r['kind']=='items']
        normalize_entities(a,known);self.check(a,bs,known)
    def test_parent_percentage_not_committed_before_scheme_choice(self):
        bs,a=fixture();a['records'][0]['value']['terms']=[{'item_id':'I2','coefficient':.2}]
        with self.assertRaisesRegex(ValueError,'候选方案'):self.check(a,bs)
    def test_explicit_standalone_item_scale_is_accepted(self):
        b=block('s:P1','实验报告满分100分')
        self.check({'records':[{'kind':'items','value':{'id':'I1','name':'实验报告','assessment_id':'A1','max_score':100},'evidence_ids':[b['id']]}]},[b])
    def test_direct_table_keeps_literal_parents_and_no_unconfirmed_numbers(self):
        bs,_=fixture();records,consumed=direct_hierarchy_records(bs)
        self.check({'records':records},bs)
        self.assertEqual(len(records),6)
        self.assertEqual(consumed,{b['id'] for b in bs})
        for r in records:
            self.assertEqual(set(r['value']),{'id','name'} if r['kind']=='assessments' else {'id','name','assessment_id'})
        bs.append(block('s:P85','实验报告（满分100分）'))
        self.assertNotIn('s:P85',direct_hierarchy_records(bs)[1])
    def test_direct_table_preserves_unknown_rows_and_rejects_history(self):
        bs,_=fixture();extra=block('s:T1R9','待定项目 | 评分制度另附');bs.append(extra)
        self.assertNotIn(extra['id'],direct_hierarchy_records(bs)[1])
        self.assertEqual(direct_hierarchy_records([{**b,'role':'history'} for b in bs]),([],set()))
    def test_registry_does_not_suggest_unquoted_parent_from_another_scheme(self):
        registry={'assessments':[{'id':'A1','name':'平时考核'},{'id':'A2','name':'验收答辩'}],
                  'goals':[{'id':'G1'}],'methods':[{'id':'M1','name':'讲授'}]}
        result=evidence_registry(registry,[block('s:P1','采用平时考核和项目考核相结合的方式')])
        self.assertEqual(result['assessments'],[{'id':'A1','name':'平时考核'}])
        self.assertEqual(result['goals'],registry['goals'])
        self.assertNotIn('methods',result)
        self.assertEqual(len(registry['assessments']),2)
    def test_qualitative_mapping_and_score_composition_do_not_offer_numeric_rules(self):
        bs=[block('s:T1R1','支撑的毕业要求 | 指标点 | 学习目标 | 权重系数'),
            block('s:T2R2','教学目标1 | 平时成绩、验收答辩、设计报告'),
            block('s:P21','总评成绩=平时成绩（满分20分）+验收答辩成绩（满分40分）+设计报告成绩（满分40分）')]
        schema=schema_for(bs,GROUPS['assessment'],extraction_schema(bs,GROUPS['assessment']))
        fields={v['properties']['kind']['enum'][0]:v['properties']['value']['properties'] for v in schema['properties']['records']['items']['anyOf']}
        self.assertNotIn('terms',fields['assessments'])
        self.assertNotIn('goal_weights',fields['items'])
        for key in ('goal_rules','assessment_goal_weights','assessment_point_rules'):self.assertNotIn(key,fields)
    def test_later_item_description_cannot_reparent_without_explicit_evidence(self):
        b=block('s:P192','项目答辩：根据学生答辩情况评分，满分100分。')
        known={'assessments':[{'id':'A2','name':'终结性考核'},{'id':'A3','name':'设计报告'}],
               'items':[{'id':'I1','name':'项目答辩','assessment_id':'A2'}]}
        answer={'records':[{'kind':'items','value':{'id':'I1','name':'项目答辩','assessment_id':'A3','max_score':100},'evidence_ids':[b['id']]}]}
        with self.assertRaisesRegex(ValueError,'跨批次'):self.check(answer,[b],known)
        answer['records'][0]['value']['assessment_id']='A2';self.check(answer,[b],known)
        context=evidence_registry(known,[b])
        self.assertEqual(context['assessments'],[known['assessments'][0]])
    def test_recognized_table_does_not_need_model_or_claim_live_probe(self):
        bs,_=fixture()
        class Client:
            config={};identity={}
            def require_structured(self):pass
            def log(self,*args):pass
            def request(self,*args,**kwargs):raise AssertionError('明确表格不应请求模型')
        case={'id':'c','course_name':'测试课','decision_policy':{'mode':'grade_first_minimal_v1'}}
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.plan_chunks',return_value=(1400,[bs])):
            result=extract(case,[{'id':'s','role':'current','path':'s.docx','blocks':bs,'sheets':[]}],Client(),Path(tmp),'T',max_batches=2,defer_validation=True)
            probe=json.loads((Path(tmp)/'probe_validation.json').read_text())
        self.assertEqual(len(result['items']),4)
        self.assertIn('尚未验证模型抽取',' '.join(probe['issues']))
    def test_actual_assessment_prompt_matches_schema_and_has_no_empty_example(self):
        bs,answer=fixture()
        class Client:
            config={};identity={};seen=None
            def require_structured(self):pass
            def log(self,*args):pass
            def request(self,messages,**kw):
                self.seen=json.loads(messages[-1]['content'])
                fields={v['properties']['kind']['enum'][0]:list(v['properties']['value']['properties'])
                        for v in kw['output_schema']['properties']['records']['items']['anyOf']}
                if self.seen['value_fields']!=fields:raise AssertionError('提示字段与schema不一致')
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        c=Client();case={'id':'c','course_name':'测试课','decision_policy':{'mode':'grade_first_minimal_v1'}}
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.plan_chunks',return_value=(1400,[bs])),patch('report_agent.assessment_guard.direct_hierarchy_records',return_value=([],set())):
            result=extract(case,[{'id':'s','role':'current','path':'s.docx','blocks':bs,'sheets':[]}],c,Path(tmp),'T',defer_validation=True)
        self.assertEqual(len(result['items']),4)
        self.assertNotIn('output_example_not_evidence',c.seen)
        self.assertEqual(len(c.seen['assessment_guidance']['expected_hierarchy']),4)
