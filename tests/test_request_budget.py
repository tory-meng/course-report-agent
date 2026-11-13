"""预算回归使用可控计数器，不冒充真实Qwen tokenizer/GPU验证。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.client import Client, task
from report_agent.common import load, signature
from report_agent.facts import extract
from report_agent.request_budget import InputBudgetError, evidence_view, fit_messages, split_evidence, dumps
from training.data import actual_messages


def messages(payload):
    return [{'role':'system','content':'仅从本轮证据抽取。'}, {'role':'user','content':dumps(payload)}]


class Counter:
    """Tests count one Unicode character as one token, deliberately deterministic."""
    config = {'input_safety_tokens':32}
    identity = {'max_input_tokens':1000, 'max_output_tokens':2048}
    prepare_messages = Client.prepare_messages
    def count_tokens(self, ms, output_tokens):
        return {'input_tokens':sum(len(m['content']) for m in ms), **self.identity}


class BudgetTests(unittest.TestCase):
    def test_projection_does_not_mutate_or_duplicate_layout(self):
        b = {'id':'s:Sheet1:R2','text':'C2=原文 | C9=70','source_id':'s','role':'current',
             'cells':[{'column':n,'text':'重复内容'} for n in range(400)],
             'merged_cells':['A1:ZZ9']*2000, '_quality':True}
        original=copy.deepcopy(b); result=evidence_view(b)
        self.assertEqual(b,original);self.assertEqual(result['text'],b['text'])
        self.assertNotIn('cells',result);self.assertNotIn('merged_cells',result)
        self.assertLess(len(dumps(result)),200)

    def test_word_projection_preserves_merged_column_positions(self):
        b={'id':'s:T1R2','text':'平时 | 50%', 'cells':[
            {'column':1,'anchor_column':1,'grid_span':2,'text':'平时'},
            {'column':2,'anchor_column':1,'grid_span':2,'text':'平时'},
            {'column':3,'anchor_column':3,'grid_span':1,'text':'50%'}]}
        v=evidence_view(b)
        self.assertEqual([(x[0],x[1]) for x in v['column_spans']],[(1,2),(3,1)])
        for span in v['column_spans']:
            self.assertIn(v['text'][span[2]:span[3]],('平时','50%'))

    def test_reference_budget_preserves_current_evidence_and_ids(self):
        payload={'evidence':[{'id':'s:1','text':'当前材料权重50%'}], 'rules':['不得补数'],
                 'known_entities':{'goals':[{'id':'G1'}]},
                 'reference_examples':{'hits':[{'reference_id':'ref_a','text':'样本'*3000}]}}
        before=copy.deepcopy(payload)
        fitted,audit=fit_messages(Counter(),messages(payload),100)
        p=json.loads(fitted[-1]['content'])
        self.assertEqual(p['evidence'],before['evidence']);self.assertEqual(p['known_entities'],before['known_entities'])
        self.assertEqual(payload,before);self.assertLessEqual(audit['input_tokens'],audit['target_tokens'])
        self.assertIn('short_references',audit['actions'])

    def test_combined_context_includes_output_and_margin(self):
        c=Counter();c.identity={**c.identity,'max_context_tokens':800}
        _,a=fit_messages(c,messages({'evidence':[]}),400)
        self.assertEqual(a['target_tokens'],368)

    def test_hard_overflow_is_typed_and_never_truncates_evidence(self):
        ms=messages({'evidence':[{'id':'s:1','text':'证据'*1000}]})
        with self.assertRaises(InputBudgetError) as ctx:fit_messages(Counter(),ms,100)
        self.assertEqual(json.loads(ctx.exception.messages[-1]['content'])['evidence'],json.loads(ms[-1]['content'])['evidence'])
        self.assertIn('component_characters',ctx.exception.audit)

    def test_table_split_repeats_header_keeps_all_rows(self):
        blocks=[{'id':'s:T1R1','text':'考核方式 | 系数','context_only':True}]+[
            {'id':f's:T1R{i}','text':str(i)*80} for i in range(2,6)]
        children=split_evidence(blocks)
        self.assertTrue(all(x[0]['id']=='s:T1R1' for x in children))
        self.assertEqual([b['id'] for bs in children for b in bs if not b.get('context_only')], [b['id'] for b in blocks[1:]])

    def test_one_long_paragraph_split_preserves_every_character(self):
        text='第一段。'*90+'第二段。'*90
        b={'id':'s:P1','text':text,'cells':[{'text':'不应回流'}]}
        parts=split_evidence([b]);covered=set()
        for child in parts:
            p=child[0];start,end=p['source_char_range'];covered.update(range(start,end))
            self.assertEqual(p['text'],text[start:end]);self.assertNotIn('cells',p)
        self.assertEqual(covered,set(range(len(text))))
        self.assertIsNone(split_evidence([{'id':'s:1','text':'短'}]))

    def test_mixed_paragraph_table_split_carries_needed_table_header(self):
        blocks=[{'id':'s:P1','text':'正文'*100},
                {'id':'s:T1R1','table_id':'s:T1','row':1,'text':'考核方式 | 权重'},
                {'id':'s:T1R2','table_id':'s:T1','row':2,'text':'平时'*200},
                {'id':'s:T1R3','table_id':'s:T1','row':3,'text':'期末'*200}]
        children=split_evidence(blocks)
        self.assertEqual(children[1][0]['id'],'s:T1R1')
        self.assertTrue(children[1][0]['context_only'])

    def test_retry_stays_json_is_recounted_and_records_actual_input(self):
        class Fake(Counter):
            count=0;calls=[]
            def log(self,*args):pass
            def reference_resolver(self,*args):return {'hits':[{'reference_id':'ref_a','text':'参考'*5000}]}
            def request(self,ms,**kw):
                self.calls.append(copy.deepcopy(ms));p=json.loads(ms[-1]['content'])
                self.assertion = p['evidence']==[{'id':'s:1','text':'本轮原文'}]
                self.count+=1
                return {'text':dumps({'valid':self.count>1})}
        c=Fake();c.calls=[]
        def validate(a):
            if not a['valid']:raise ValueError('字段不匹配')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'record.json'
            task(c,messages({'evidence':[{'id':'s:1','text':'本轮原文'}]}),path,validate,{'task_kind':'extract'},retries=1)
            r=load(path)
            self.assertEqual(c.count,2);self.assertTrue(c.assertion)
            self.assertIn('retry_instruction',json.loads(c.calls[1][-1]['content']))
            self.assertTrue(all(a['input_budget']['input_tokens']<=a['input_budget']['target_tokens'] for a in r['attempts']))
            self.assertEqual(actual_messages(r),c.calls[-1])
            task(c,messages({'evidence':[{'id':'s:1','text':'本轮原文'}]}),path,validate,{'task_kind':'extract'},retries=1)
            self.assertEqual(c.count,2)

    def test_transport_failure_is_not_resubmitted(self):
        class Fake(Counter):
            calls=0
            def log(self,*args):pass
            def request(self,*args,**kwargs):self.calls+=1;raise ConnectionResetError('lost connection')
        c=Fake()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConnectionResetError):task(c,messages({'evidence':[]}),Path(tmp)/'r.json',lambda a:None,{})
            self.assertEqual(c.calls,1)

    def test_input_overflow_record_is_saved_without_generation(self):
        class Fake(Counter):
            def log(self,*args):pass
            def request(self,*args,**kwargs):raise AssertionError('不可生成')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.json'
            with self.assertRaises(InputBudgetError):task(Fake(),messages({'evidence':[{'text':'原文'*3000}]}),p,lambda a:None,{})
            self.assertEqual(load(p)['attempts'][-1]['phase'],'input_budget')
            self.assertIsNone(load(p)['attempts'][-1]['raw'])

    def test_extractor_splits_overflow_and_resumes_without_more_requests(self):
        class Fake:
            config={};identity={};calls=[]
            def log(self,*args):pass
            def require_structured(self):pass
            def prepare_messages(self,ms,budget):
                p=json.loads(ms[-1]['content']);n=sum(len(b['text']) for b in p['evidence'])
                a={'input_tokens':n,'target_tokens':300,'max_input_tokens':400,'output_tokens':budget,'actions':[]}
                if n>300:raise InputBudgetError(a,ms)
                return ms,a
            def request(self,ms,**kw):
                self.calls.append(ms)
                return {'text':'{"records":[]}','schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        blocks=[{'id':f's:P{i}','text':('甲' if i==1 else '乙')*250,'role':'current','source_id':'s'} for i in (1,2)]
        sources=[{'id':'s','role':'current','blocks':blocks,'sheets':[]}]
        c=Fake();c.calls=[]
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.validate_facts',side_effect=lambda f,*a:f):
            case={'id':'c','course_name':'测试'};out=Path(tmp)
            extract(case,sources,c,out,'T')
            self.assertEqual(len(c.calls),2)
            split=load(out/'records/extract_0000.split.json');self.assertEqual(split['split_kind'],'input_budget')
            extract(case,sources,c,out,'T');self.assertEqual(len(c.calls),2)
            self.assertEqual([b['id'] for ms in c.calls for b in json.loads(ms[-1]['content'])['evidence']],['s:P1','s:P2'])


if __name__=='__main__':unittest.main()
