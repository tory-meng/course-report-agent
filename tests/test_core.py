import copy
import json
import tempfile
import unittest
from pathlib import Path
from report_agent.common import ROOT,load,save
from report_agent.readers import ingest
from report_agent.facts import validate_facts
from report_agent.grades import parse_grades
from report_agent.compute import calculate,summary
from report_agent.tables import build
from training.data import validate_row,encode_row,export

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.case=load(ROOT/'examples/demo_course/case.json');self.facts=load(ROOT/'examples/demo_course/facts.json')
        self.sources=ingest(self.case);self.grades=parse_grades(self.case,self.sources,self.facts)
    def test_evidence_and_real_formula(self):
        validate_facts(self.facts,self.sources,self.case['course_name']);c=calculate(self.case,self.facts,self.grades)
        expected=(49*.3+53*.35)/(100*.3+100*.35)
        self.assertAlmostEqual(c['goals']['G1']['values']['S001'],expected)
        self.assertEqual(sum(c['total']['counts']),12)
        self.assertEqual(summary({'a':.6,'b':.59},.6)['passed'],1)
        self.assertNotEqual(summary({'a':.6,'b':.59},.6)['mean'],.5)
    def test_missing_not_zero_and_denominator(self):
        self.grades['students'][0]['scores']['I1']=None;self.grades['students'][0]['score_status']['I1']='missing'
        c=calculate(self.case,self.facts,self.grades)
        self.assertEqual(c['goals']['G1']['valid_count'],11)
        self.assertEqual(c['goals']['G2']['valid_count'],12)
        self.assertNotIn('S001',c['goals']['G1']['values'])
    def test_zero_is_valid(self):
        self.grades['students'][0]['scores']['I1']=0
        c=calculate(self.case,self.facts,self.grades);self.assertEqual(c['goals']['G1']['valid_count'],12)
    def test_unknown_policy_rejected(self):
        self.case['evaluation_policy'].pop('missing')
        with self.assertRaises(ValueError):calculate(self.case,self.facts,self.grades)
    def test_duplicate_and_conflicting_grades(self):
        s=next(s for s in self.sources if s['id']=='grades');s['sheets'][0]['rows'].append(s['sheets'][0]['rows'][1])
        with self.assertRaises(ValueError):parse_grades(self.case,self.sources,self.facts)
    def test_wrong_course_and_quote_rejected(self):
        with self.assertRaises(ValueError):validate_facts(self.facts,self.sources,'另一门课程')
        self.facts['goals'][0]['quotes'][self.facts['goals'][0]['source_refs'][0]]='编造原文'
        with self.assertRaises(ValueError):validate_facts(self.facts,self.sources,self.case['course_name'])
    def test_tables_dimensions(self):
        c=calculate(self.case,self.facts,self.grades)
        contract=load(ROOT/'templates/course/buu_course_r2/template_contract.json');result=build(self.facts,c,contract)
        self.assertEqual(len(result),11)
        for group in result.values():
            for table in group:
                for row in table['rows']:self.assertEqual(len(row),len(table['widths_mm']))
                self.assertAlmostEqual(sum(table['widths_mm']),146,delta=.1)
    def test_ambiguous_total_column_rejected(self):
        self.case['grade_tables']=[];s=next(s for s in self.sources if s['id']=='grades')
        for i,row in enumerate(s['sheets'][0]['rows']):row.append('总评成绩' if i==0 else 80)
        with self.assertRaises(ValueError):parse_grades(self.case,self.sources,self.facts)
    def test_four_goal_tables_remain_unified_without_invalid_merges(self):
        self.facts['goals'].append({**self.facts['goals'][0],'id':'G4'})
        self.facts['goal_rules'].append({**self.facts['goal_rules'][0],'id':'G4'})
        c=calculate(self.case,self.facts,self.grades)
        result=build(self.facts,c,load(ROOT/'templates/course/buu_course_r2/template_contract.json'))
        for key in ['T2-3','T3-1','T3-2','T4-1']:
            self.assertEqual(len(result[key]),1)
            for gid in ('G1','G2','G3','G4'):self.assertIn(gid,str(result[key][0]['rows']))
            for table in result[key]:
                self.assertAlmostEqual(sum(table['widths_mm']),146,delta=.1)
                for row in table['rows']:self.assertEqual(len(row),len(table['widths_mm']))
                for r1,c1,r2,c2 in table['merges']:
                    self.assertTrue(0<=r1<=r2<len(table['rows']))
                    self.assertTrue(0<=c1<=c2<len(table['widths_mm']))
    def test_missing_threshold_remains_unknown(self):
        self.facts['goals'][0]['threshold']=None
        c=calculate(self.case,self.facts,self.grades)
        self.assertIsNone(c['goals']['G1']['rate']);self.assertGreater(c['goals']['G1']['valid_count'],0)
        self.assertTrue(any('G1缺少达成标准' in issue for issue in c['issues']))
    def test_missing_table_weight_not_assumed_zero(self):
        from report_agent.tables import missing_inputs
        self.assertEqual(missing_inputs(self.facts),[])
        self.facts['assessment_goal_weights']=[]
        self.assertEqual(len(missing_inputs(self.facts)),6)
    def test_pdf_retry_preserves_draft_and_rejects_changed_word(self):
        from report_agent.pipeline import retry_pdf
        from report_agent.common import digest
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'report.docx').write_bytes(b'test fixture')
            save(out/'runtime.json',{});save(out/'validation.json',{'missing_items':['缺数据']})
            save(out/'run_manifest.json',{'status':'failed','pdf_status':'pending','word_sha256':digest(out/'report.docx'),'offline':False,'error':'PDF failed'})
            with patch('report_agent.pipeline.pdf.convert',return_value={'sha256':'fake-pdf'}) as convert:
                retry_pdf(out,out/'runtime.json');convert.assert_called_once()
                self.assertEqual(load(out/'run_manifest.json')['status'],'draft_ready')
                (out/'report.docx').write_bytes(b'changed')
                with self.assertRaises(ValueError):retry_pdf(out,out/'runtime.json')

class FakeTokenizer:
    unk_token_id=-1
    def apply_chat_template(self,*a,**kw):return 'PROMPT'
    def encode(self,text,**kw):return [99] if text=='<|im_end|>' else [ord(c) for c in text]
    def convert_tokens_to_ids(self,t):return 99

class TrainingTests(unittest.TestCase):
    def row(self,group='a',answer='一'):
        return {'id':group,'group_id':group,'messages':[{'role':'user','content':'请返回JSON内容。'},{'role':'assistant','content':json.dumps({'text':answer},ensure_ascii=False)}],
                'review':{'approved':True,'reviewer':'test','answer_isolation_checked':True}}
    def test_prompt_mask_and_no_truncation(self):
        encoded=encode_row(FakeTokenizer(),self.row(),100)
        self.assertEqual(encoded['labels'][:6],[-100]*6);self.assertEqual(encoded['labels'][-1],99)
        with self.assertRaises(ValueError):encode_row(FakeTokenizer(),self.row(),5)
    def test_review_required(self):
        r=self.row();r['review']['approved']=False
        with self.assertRaises(ValueError):validate_row(r)
    def test_answer_leak_rejected(self):
        r=self.row();r['messages'][0]['content']=r['messages'][-1]['content']
        with self.assertRaises(ValueError):validate_row(r)
    def test_group_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);replay=root/'replay.jsonl'
            from report_agent.common import write_rows,read_rows
            write_rows(replay,[self.row('a','第一'),self.row('b','第二'),self.row('c','第三')])
            result=export([],root/'dataset',replay)
            self.assertFalse(set(result['train_groups'])&set(result['validation_groups']))

if __name__=='__main__':unittest.main()
