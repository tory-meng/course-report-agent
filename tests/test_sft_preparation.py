import copy
import json
import unittest
import tempfile
from pathlib import Path
from training.data import validate_payload,validate_row,encode_row,encode_payload
from training.preparation import assign_families,precheck,messages,export_prepared,section_schema,grade_task
from training.length_check import inspect
from training.task_metrics import diagnose
from report_agent.structured import obj,array,text,choice,writing_schema
from report_agent.common import save,signature,write_rows,digest

class Tokenizer:
    unk_token_id=-1
    def apply_chat_template(self,*args,**kwargs):return 'PROMPT'
    def encode(self,s,**kwargs):return [99] if s=='<|im_end|>' else [ord(c) for c in s]
    def convert_tokens_to_ids(self,s):return 99

class PreparationTests(unittest.TestCase):
    def candidate(self):
        return {'id':'one','group_id':'course','split':'train','messages':messages({'task':'内容整理'},{'value':'课程内容'}),
                'review':{'approved':False,'reviewer':'','answer_isolation_checked':False}}

    def test_preparation_does_not_grant_training_approval(self):
        r=self.candidate();validate_payload(r);inspect([r],Tokenizer(),100)
        self.assertFalse(r['review']['approved'])
        with self.assertRaisesRegex(ValueError,'人工审核'):validate_row(r)
        with self.assertRaisesRegex(ValueError,'人工审核'):encode_row(Tokenizer(),r,100)

    def test_candidate_encoding_masks_input_and_rejects_truncation(self):
        r=self.candidate();enc=encode_payload(Tokenizer(),r,100)
        self.assertEqual(enc['labels'][:6],[-100]*6)
        self.assertEqual(enc['labels'][6:],enc['input_ids'][6:]);self.assertEqual(enc['labels'][-1],99)
        with self.assertRaisesRegex(ValueError,'不截断'):encode_payload(Tokenizer(),r,7)

    def test_shared_raw_file_merges_course_families_before_split(self):
        cs=[{'course_name':'甲','origin_group':'a','source_hashes':['shared']},
            {'course_name':'乙','origin_group':'b','source_hashes':['shared','second']},
            {'course_name':'丙','origin_group':'c','source_hashes':['second']},
            {'course_name':'丁','origin_group':'d','source_hashes':['other']}]
        assign_families(cs,['甲'])
        self.assertEqual([c['split'] for c in cs],['validation']*3+['train'])
        self.assertEqual(len({c['group_id'] for c in cs[:3]}),1)

    def test_same_course_alias_cannot_cross_split(self):
        cs=[{'course_name':'《数字 图像处理》','origin_group':'a','source_hashes':['x']},
            {'course_name':'数字图像处理','origin_group':'b','source_hashes':['y']},
            {'course_name':'另一课','origin_group':'c','source_hashes':['z']}]
        assign_families(cs,['数字图像处理']);self.assertEqual(cs[0]['group_id'],cs[1]['group_id'])
        with self.assertRaisesRegex(ValueError,'不存在'):assign_families(cs,['不存在'])

    def test_source_reference_outside_task_is_rejected(self):
        r=self.candidate();r.update(output_schema=obj({'value':text()},['value']),evidence_refs=['foreign'],evidence_available=['local'],task_kind='extract')
        with self.assertRaisesRegex(ValueError,'引用'):precheck(r)

    def test_choice_paragraph_cannot_become_free_writing_example(self):
        answer={'paragraphs':['计划安排本课实验并记录方法。'],'source_refs':['s:P1']}
        r=self.candidate();r.update(messages=messages({'choices':answer['paragraphs']},answer),task_kind='write_section',section_id='6.1',
            output_schema=writing_schema(['s:P1']),evidence_refs=['s:P1'],evidence_available=['s:P1'])
        with self.assertRaisesRegex(ValueError,'段落已在输入'):precheck(r)

    def test_duplicate_goal_activity_fails_even_when_json_is_valid(self):
        schema=obj({'repairs':array(obj({'goal_id':choice(['G1']),'method_ids':array(choice(['M1']),0,2),'reason':text()},['goal_id','method_ids','reason']),1,1)},['repairs'])
        data={'uncovered_goals':[{'id':'G1'}],'available_methods':[{'id':'M1'}]}
        a={'repairs':[{'goal_id':'G1','method_ids':['M1','M1'],'reason':'活动理由。'}]}
        r=self.candidate();r.update(messages=messages(data,a),output_schema=schema,evidence_refs=['s'],evidence_available=['s'],task_kind='teaching_design_gap_review')
        with self.assertRaisesRegex(ValueError,'重复'):precheck(r)

    def test_structural_pass_does_not_establish_semantic_correctness(self):
        expected={'paragraphs':['计划安排实验并记录过程。'],'source_refs':['s']}
        r=self.candidate();r.update(messages=messages({},expected),output_schema=writing_schema(['s']),evidence_refs=['s'],task_kind='write_section',section_id='6.1')
        issues,metrics=diagnose(r,expected)
        self.assertEqual(issues,[]);self.assertFalse(metrics['reference_approved'])
        self.assertEqual(metrics['semantic_quality'],'not_established_by_structural_checks')

    def test_pending_preparation_cannot_export_trainable_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.candidate();write_rows(p/'train.candidates.jsonl',[r]);write_rows(p/'validation.candidates.jsonl',[])
            save(p/'preparation_manifest.json',{'dataset_fingerprint':signature({'train':[r],'validation':[]})})
            save(p/'decisions.json',{'decisions':[{'id':r['id'],'review':{'approved':False}}]})
            with self.assertRaisesRegex(ValueError,'尚无逐条批准'):export_prepared(p,p/'decisions.json',p/'export')
            self.assertFalse((p/'export').exists())

    def test_review_becomes_invalid_when_answer_is_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);r=self.candidate();write_rows(p/'train.candidates.jsonl',[r]);write_rows(p/'validation.candidates.jsonl',[])
            save(p/'preparation_manifest.json',{'dataset_fingerprint':signature({'train':[r],'validation':[]})})
            save(p/'answer.json',{'value':'原答案'})
            decision={'id':r['id'],'candidate_sha256':signature(r),'answer_file':str(p/'answer.json'),'answer_sha256':digest(p/'answer.json'),
                      'review':{'approved':True,'reviewer':'unit-test-fixture','answer_isolation_checked':True}}
            save(p/'decisions.json',{'decisions':[decision]});save(p/'answer.json',{'value':'修改后答案'})
            with self.assertRaisesRegex(ValueError,'哈希'):export_prepared(p,p/'decisions.json',p/'export')
            self.assertFalse((p/'export').exists())

    def test_known_section_count_is_enforced_before_model_quality_scoring(self):
        from report_agent.structured import validate_schema
        schema=section_schema(['s'],2)
        with self.assertRaises(ValueError):validate_schema({'paragraphs':['重复。']*3,'source_refs':['s']},schema)
        validate_schema({'paragraphs':['段落甲。','段落乙。'],'source_refs':['s']},schema)

    def test_non_grade_sheet_preview_stops_before_student_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);mapping={'source_id':'grade','sheet':'Sheet1','header_row':2,'total':4,'items':{'I':3}}
            save(p/'grade_selection.json',{'candidate_tables':[{'source_id':'grade','sheet':'Sheet1','registered':1,'scored':1,'mapping':mapping}],
                'selected_mappings':[mapping],'excluded_sources':[]})
            save(p/'case.effective.json',{})
            sources=[{'id':'grade','path':'成绩单.xls','sha256':'g','role':'current','kind':'grades',
                'sheets':[{'name':'Sheet1','rows':[['课程名称：测试'],['学号','姓名','平时','总评'],['2025000001','学生甲',80,90]]}]},
                {'id':'attendance','path':'考勤.xls','sha256':'a','role':'current','kind':'document',
                'sheets':[{'name':'Sheet1','rows':[['考勤'],['学号','姓名'],['2025000001','学生甲']]}]}]
            data,_=grade_task('测试',p,sources,{'metadata':{},'items':[{'id':'I','name':'平时'}]})
            view=json.dumps(data,ensure_ascii=False)
            self.assertNotIn('2025000001',view);self.assertNotIn('学生甲',view)
            self.assertIn('考勤',view)

if __name__=='__main__':unittest.main()
