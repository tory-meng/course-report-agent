import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.client import Client, task, parse_answer, TaskValidationError
from report_agent.common import load
from report_agent.progress import Progress
from report_agent.facts import extract

class FakeClient:
    config={'max_new_tokens':20}
    identity={'max_output_tokens':40}
    def require_structured(self):pass
    def __init__(self, replies):self.replies=iter(replies);self.calls=[]
    def request(self,messages,**kwargs):
        self.calls.append((messages,kwargs))
        reply=next(self.replies)
        if isinstance(reply,Exception):raise reply
        if kwargs.get('output_schema') is not None:
            from report_agent.common import signature
            reply={**reply,'schema_enforced':True,'schema_sha256':signature(kwargs['output_schema'])}
        return reply
    def log(self,message):pass

class JSONProgressTests(unittest.TestCase):
    def test_cleanup_preserves_string_evidence(self):
        a,changes=parse_answer('\ufeff```json\n{"text":"引用 ,} 和 ,] 以及 \\\"", "items":[1,],}\n```')
        self.assertEqual(a,{'text':'引用 ,} 和 ,] 以及 "','items':[1]})
        self.assertEqual(len(changes),3)
    def test_ambiguous_or_incomplete_json_rejected(self):
        for raw in ['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}',"{'x':1}",'{x:1}','{"x":1','说明：{"x":1}','{"x":1} junk']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_answer(raw)
    def test_retry_keeps_evidence_not_bad_output(self):
        c=FakeClient([{'text':'BROKEN_SECRET_OUTPUT'},{'text':'{"x":1}'}]);messages=[{'role':'user','content':'原始证据'}]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.json';task(c,messages,p,lambda a:None,{})
            self.assertNotIn('BROKEN_SECRET_OUTPUT',json.dumps(c.calls[1][0]))
            self.assertIn('原始证据',c.calls[1][0][0]['content'])
            self.assertEqual(load(p)['attempts'][0]['raw']['text'],'BROKEN_SECRET_OUTPUT')
    def test_length_budget_capped(self):
        c=FakeClient([{'text':'{}','limit_reached':True}]*3)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskValidationError):task(c,[{'role':'user','content':'证据'}],Path(tmp)/'r.json',lambda a:None,{})
        self.assertEqual([k['max_new_tokens'] for _,k in c.calls],[20,40,40])
    def test_transport_never_retries(self):
        c=FakeClient([RuntimeError('timeout')])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.json'
            with self.assertRaises(RuntimeError):task(c,[{'role':'user','content':'证据'}],p,lambda a:None,{})
            self.assertEqual(load(p)['attempts'][0]['phase'],'request')
        self.assertEqual(len(c.calls),1)
    def test_semantic_validation_not_bypassed(self):
        c=FakeClient([{'text':'{"x":1,}'}]*3)
        def reject(a):raise ValueError('没有来源')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TaskValidationError) as caught:task(c,[{'role':'user','content':'证据'}],Path(tmp)/'r.json',reject,{})
        self.assertFalse(caught.exception.splittable)
    def test_heartbeat_uses_one_generation(self):
        c=Client.__new__(Client);c.config={'progress_interval_seconds':.01};c.identity={'features':['progress']}
        calls=[];notes=[]
        def http(endpoint,payload=None,timeout=30):
            calls.append(endpoint)
            if endpoint=='/generate':time.sleep(.07);return {'text':'{}','output_tokens':5}
            return {'busy':True,'output_tokens':3}
        c.http=http;c.log=notes.append
        self.assertEqual(c.request([])['text'],'{}')
        self.assertEqual(calls.count('/generate'),1);self.assertIn('/progress',calls)
        self.assertTrue(any('3 tokens' in s for s in notes))
    def test_failure_progress_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Progress(Path(tmp));p.stage(3,'事实抽取');p.units(1,4,'批次');p.finish('failed','JSON格式错误')
            state=load(Path(tmp)/'progress.json')
            self.assertEqual((state['stage'],state['completed'],state['total'],state['status']),(3,1,4,'failed'))
    def test_split_and_resume_preserve_all_evidence(self):
        blocks=[{'id':'s:1','text':'甲'*120,'role':'current'}, {'id':'s:2','text':'乙'*120,'role':'current'}]
        sources=[{'id':'s','role':'current','blocks':blocks,'sheets':[]}]
        c=FakeClient([{'text':'{','limit_reached':True}]*2+[{'text':'{"records":[]}'},{'text':'{"records":[]}'}])
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.validate_facts',side_effect=lambda facts,*args:facts):
            out=Path(tmp);case={'id':'c','course_name':'测试'}
            extract(case,sources,c,out,'T')
            self.assertEqual(len(c.calls),4)
            evidence=[json.loads(m[0][1]['content'])['evidence'] for m in c.calls[-2:]]
            self.assertEqual([b['id'] for group in evidence for b in group],['s:1','s:2'])
            extract(case,sources,c,out,'T');self.assertEqual(len(c.calls),4)
            self.assertTrue((out/'facts.partial.json').is_file())

if __name__=='__main__':unittest.main()
