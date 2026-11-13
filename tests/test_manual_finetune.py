import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from report_agent.common import save,load,write_rows,digest,signature
from scripts.manual_finetune import interactive_review,manual_train,stop_original_service
from training.train import compatible_arguments

class ManualFinetuneTests(unittest.TestCase):
    def fixture(self,p):
        row={'id':'unit','group_id':'unit-group','case_id':'unit-course','course_name':'单元测试课','task_kind':'extract','split':'train',
            'messages':[{'role':'user','content':'提取字段，返回JSON。'},{'role':'assistant','content':json.dumps({'value':'字段内容'},ensure_ascii=False)}],
            'review':{'approved':False,'reviewer':'','answer_isolation_checked':False},
            'output_schema':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False},
            'evidence_refs':['s'],'evidence_available':['s']}
        write_rows(p/'train.candidates.jsonl',[row]);write_rows(p/'validation.candidates.jsonl',[])
        save(p/'preparation_manifest.json',{'dataset_fingerprint':signature({'train':[row],'validation':[]})})
        save(p/'answer.json',{'value':'字段内容'})
        save(p/'review_decisions.json',{'decisions':[{'id':'unit','candidate_sha256':signature(row),
            'answer_file':str(p/'answer.json'),'answer_sha256':digest(p/'answer.json'),'review':row['review']}]})

    def test_skipping_a_sample_does_not_approve_it(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            p=Path(d);self.fixture(p)
            with patch('builtins.input',return_value='n'):interactive_review(p,'unit-test-reviewer')
            self.assertFalse(load(p/'review_decisions.json')['decisions'][0]['review']['approved'])

    def test_each_yes_records_the_actual_reviewer_and_answer_hash(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            p=Path(d);self.fixture(p)
            with patch('builtins.input',return_value='y'):interactive_review(p,'unit-test-reviewer')
            decision=load(p/'review_decisions.json')['decisions'][0]
            self.assertEqual(decision['review']['reviewer'],'unit-test-reviewer')
            self.assertTrue(decision['review']['approved']);self.assertEqual(decision['answer_sha256'],digest(p/'answer.json'))

    def test_editing_while_confirmation_is_pending_requires_redisplay(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            p=Path(d);self.fixture(p);count=0
            def choose(prompt):
                nonlocal count
                count+=1
                if count==1:save(p/'answer.json',{'value':'刚修改的答案'});return 'y'
                return 'q'
            with patch('builtins.input',side_effect=choose):interactive_review(p,'unit-test-reviewer')
            self.assertFalse(load(p/'review_decisions.json')['decisions'][0]['review']['approved'])

    def test_unapproved_training_does_not_stop_service_or_create_run(self):
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            p=Path(d);self.fixture(p)
            with patch('scripts.manual_finetune.stop_original_service') as stop:
                with self.assertRaisesRegex(ValueError,'尚无逐条审核'):manual_train(p,p/'run',p/'runtime.json','0')
                stop.assert_not_called();self.assertFalse((p/'run').exists())

    def test_busy_service_is_never_stopped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);save(p/'runtime.json',{'model':{}})
            client=SimpleNamespace(http=lambda endpoint:{'busy':True})
            with patch('scripts.manual_finetune.Client',return_value=client),patch('scripts.manual_finetune.os.kill') as kill:
                with self.assertRaisesRegex(ValueError,'正在生成'):stop_original_service(p/'runtime.json',p)
                kill.assert_not_called()

    def test_new_transformers_warmup_fraction_is_preserved(self):
        class NewArguments:
            def __init__(self,warmup_steps:float=0,eval_strategy='steps'):pass
        actual=compatible_arguments({'warmup_ratio':.03,'eval_strategy':'steps'},NewArguments)
        self.assertEqual(actual,{'warmup_steps':.03,'eval_strategy':'steps'})

    def test_old_warmup_api_and_eval_alias_are_supported(self):
        class OldArguments:
            def __init__(self,warmup_ratio=0,evaluation_strategy='steps'):pass
        actual=compatible_arguments({'warmup_ratio':.03,'eval_strategy':'steps'},OldArguments)
        self.assertEqual(actual,{'warmup_ratio':.03,'evaluation_strategy':'steps'})

    def test_unknown_arguments_are_not_silently_discarded(self):
        class Arguments:
            def __init__(self,warmup_ratio=0):pass
        with self.assertRaisesRegex(RuntimeError,'不兼容'):compatible_arguments({'warmup_ratio':.03,'unknown':1},Arguments)

if __name__=='__main__':unittest.main()
