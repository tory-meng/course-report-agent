import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.common import save,load
from report_agent.readers import read_source
from report_agent.decision_policy import (VERSION,source_rank,choose_grades,resolve_conflicts,prepare_review,used_states)
from report_agent.compute import calculate
from report_agent.grades import parse_grades
from report_agent.human_review import collect_answers,apply_answers

class DecisionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.case={'id':'c','course_name':'课程甲','sources':[],'decision_policy':{'mode':VERSION,'model_review':False}}
        self.facts={'metadata':{'course_name':'课程甲','total_max':100},'conflicts':[]}
    def tearDown(self):self.tmp.cleanup()
    def source(self,name,body,sid):
        file=self.root/name;file.write_text(body,encoding='utf-8')
        cfg={'id':sid,'path':str(file),'role':'current','kind':'grades'};self.case['sources'].append(cfg)
        return read_source(cfg)
    def conflict(self,field,values):
        return {'kind':'metadata','id':None,'field':field,'conflict_id':'c_'+field,
                'candidates':[{'value':v,'source_refs':[ref],'quotes':{ref:q}} for v,ref,q in values]}
    def test_attendance_excluded_and_register_generic_score_selected(self):
        attendance=self.source('考勤表.csv','学号,姓名,成绩\n001,甲,99\n','att')
        register=self.source('学生记分册.csv','课程名称：课程甲\n学号,姓名,期末,成绩\n001,甲,70,80\n','reg')
        self.assertEqual(source_rank(attendance),0)
        result=choose_grades(self.case,[attendance,register],self.facts)
        self.assertEqual(result['action'],'selected_grade_source')
        self.assertEqual(self.case['grade_tables'][0]['total'],4)
        grades=parse_grades(self.case,[attendance,register],self.facts)
        self.assertEqual(grades['students'][0]['total'],80)
    def test_formal_grade_has_precedence_over_register(self):
        a=self.source('记分册.csv','学号,姓名,成绩\n001,甲,70\n','a')
        b=self.source('成绩单.csv','学号,姓名,总评成绩\n001,甲,80\n','b')
        choose_grades(self.case,[a,b],self.facts)
        self.assertEqual(self.case['grade_tables'][0]['source_id'],'b')
    def test_equal_rank_and_ambiguous_columns_require_choice(self):
        s=self.source('成绩单.csv','学号,姓名,总评,总评成绩\n001,甲,70,80\n','a')
        result=choose_grades(self.case,[s],self.facts)
        self.assertEqual(result['action'],'ambiguous_mapping');self.assertNotIn('grade_tables',self.case)
        q,_=prepare_review(self.case,self.facts,[s],None,self.root)
        self.assertEqual(len(q['questions']),1);self.assertEqual(q['questions'][0]['target']['type'],'grade_tables')
    def test_duplicate_score_columns_default_to_left_integer_but_keep_override(self):
        s=self.source('学生记分册.csv','学号,姓名,成绩,成绩\n001,甲,91,91.2\n002,乙,78,78.8\n','a')
        result=choose_grades(self.case,[s],self.facts)
        self.assertEqual(result['action'],'selected_integer_duplicate_total')
        self.assertEqual(self.case['grade_tables'][0]['total'],3)
        self.case['grade_tables'][0]['total']=4
        self.assertEqual(choose_grades(self.case,[s],self.facts)['action'],'explicit_mapping')
    def test_grade_label_overrides_document_not_other_grade(self):
        a=self.source('记分册.csv','课程名称：课程甲\n学号,总评\n001,80\n','a')
        c=self.conflict('course_name',[('课程乙','d:P1','课程名称：课程乙'),('课程甲','a:记分册:R1','C1=课程名称：课程甲')])
        self.facts['conflicts']=[c];self.facts['metadata']['course_name']='课程乙'
        audit=resolve_conflicts(self.case,self.facts,[a],None,self.root)
        self.assertEqual(self.facts['metadata']['course_name'],'课程甲');self.assertFalse(self.facts['conflicts'])
        self.assertEqual(audit[0]['action'],'grade_source_priority')
        b=self.source('记分册2.csv','课程名称：课程乙\n学号,总评\n001,80\n','b')
        c['candidates'][0].update(source_refs=['b:记分册2:R1'],quotes={'b:记分册2:R1':'C1=课程名称：课程乙'})
        self.facts['conflicts']=[c]
        resolve_conflicts(self.case,self.facts,[a,b],None,self.root)
        self.assertEqual(len(self.facts['conflicts']),1)
    def test_noncritical_conflict_left_unknown_not_randomly_chosen(self):
        self.facts['metadata']['course_nature']='必修'
        self.facts['conflicts']=[self.conflict('course_nature',[('必修','a:P1','必修'),('选修','b:P1','选修')])]
        resolve_conflicts(self.case,self.facts,[],None,self.root)
        self.assertNotIn('course_nature',self.facts['metadata']);self.assertFalse(self.facts['conflicts'])
        self.assertTrue(self.facts['data_quality_issues'])
    def test_no_special_students_no_questions_or_fabricated_policy(self):
        s=self.source('成绩单.csv','学号,姓名,总评\n001,甲,80\n002,乙,0\n','a')
        q,error=prepare_review(self.case,self.facts,[s],None,self.root)
        self.assertIsNone(error);self.assertEqual(q['questions'],[])
        self.assertNotIn('exempt',self.case['evaluation_policy'])
        calc=calculate(self.case,self.facts,parse_grades(self.case,[s],self.facts))
        self.assertEqual(calc['total']['valid_count'],2);self.assertEqual(calc['total']['mean'],40)
    def test_only_observed_absent_asked_then_calculation_succeeds(self):
        s=self.source('成绩单.csv','学号,姓名,总评\n001,甲,80\n002,乙,缺考\n','a')
        q,_=prepare_review(self.case,self.facts,[s],None,self.root)
        self.assertEqual([x['target'] for x in q['questions']],[{'type':'evaluation_policy','state':'absent'}])
        answers=iter(['教师甲','1','本轮考核制度明确缺考排除'])
        a=collect_answers(q,self.root/'a.json',interactive=True,input_fn=lambda _:next(answers),print_fn=lambda _:None)
        apply_answers(q,a,self.case,self.facts,[s],self.root)
        calc=calculate(self.case,self.facts,parse_grades(self.case,[s],self.facts))
        self.assertEqual(calc['total']['valid_count'],1)
        self.assertNotIn('exempt',self.case['evaluation_policy'])
    def test_missing_whole_item_does_not_fill_zero(self):
        s=self.source('成绩单.csv','学号,姓名,总评\n001,甲,80\n','a')
        self.facts.update(goals=[{'id':'G1','threshold':.6}],items=[{'id':'I1','name':'作业','max_score':100}],
                          goal_rules=[{'id':'G1','terms':[{'item_id':'I1','coefficient':1}]}])
        # 未映射目标分项：只生成总评统计，目标不补值，也不询问不存在的免修制度。
        q,_=prepare_review(self.case,self.facts,[s],None,self.root)
        self.assertFalse(q['questions'])
        calc=calculate(self.case,self.facts,parse_grades(self.case,[s],self.facts))
        self.assertEqual(calc['goals']['G1']['valid_count'],0)
    def test_make_case_infers_name_and_includes_doc(self):
        from scripts.make_case import create_case
        inputs=self.root/'inputs';inputs.mkdir()
        (inputs/'学生记分册.csv').write_text('课程名称：课程甲/考查\n学号,姓名,成绩\n001,甲,80\n',encoding='utf-8')
        (inputs/'教案.doc').write_bytes(b'not read during registration')
        case=create_case(inputs,self.root/'case.json','c')
        self.assertEqual(case['course_name'],'课程甲');self.assertEqual(len(case['sources']),2)
        self.assertEqual(case['decision_policy']['mode'],VERSION)

    def test_pipeline_pauses_and_reuses_saved_selection_on_resume(self):
        from report_agent.common import ROOT
        from report_agent.pipeline import run
        case=load(ROOT/'examples/demo_course/case.json')
        facts=load(ROOT/'examples/demo_course/facts.json')
        case['decision_policy']={'mode':VERSION,'model_review':False}
        ref=facts['metadata']['source_refs'][0];quote=facts['metadata']['quotes'][ref]
        facts['conflicts']=[self.conflict('course_code',[('CODE1',ref,quote),('CODE2',ref,quote)])]
        save(self.root/'facts.json',facts);case['facts_path']=str(self.root/'facts.json')
        save(self.root/'case.json',case)
        save(self.root/'runtime.json',{'template_registry':'configs/template_registry.json'})
        output=self.root/'report'
        first=run(self.root/'case.json',self.root/'runtime.json',output,offline=True)
        self.assertEqual(first['status'],'awaiting_user')
        q=load(Path(first['questions_path']));answer_file=Path(first['answers_path'])
        answers=iter(['教师甲','2','核对原始登记信息'])
        collect_answers(q,answer_file,load(answer_file),True,input_fn=lambda _:next(answers),print_fn=lambda _:None)
        with patch('report_agent.pipeline.calculate',side_effect=RuntimeError('TEST_REACHED_CALCULATION')) as calc:
            with self.assertRaisesRegex(RuntimeError,'TEST_REACHED_CALCULATION'):
                run(self.root/'case.json',self.root/'runtime.json',output,resume=True,offline=True)
            calc.assert_called_once()
        effective=load(output/'facts.json')
        self.assertEqual(effective['metadata']['course_code'],'CODE2');self.assertFalse(effective['conflicts'])

    def test_fixed_validation_group_does_not_enter_training(self):
        from training.data import export
        from report_agent.common import write_rows,read_rows
        rows=[]
        for g in ['course_001','course_002','course_003']:
            rows.append({'id':g,'group_id':g,'messages':[{'role':'user','content':'按证据提取'},
                {'role':'assistant','content':'{"course":"'+g+'"}'}],
                'review':{'approved':True,'reviewer':'test','answer_isolation_checked':True}})
        write_rows(self.root/'replay.jsonl',rows)
        result=export([],self.root/'dataset',self.root/'replay.jsonl',validation_groups=['course_002'])
        self.assertEqual(result['validation_groups'],['course_002'])
        self.assertNotIn('course_002',result['train_groups'])

    def test_model_preference_cannot_override_equal_rank_numeric_conflict(self):
        from report_agent.common import signature
        class Client:
            config={'max_new_tokens':512};identity={'max_output_tokens':512}
            def request(self,messages,**kw):
                answer={'candidate':'1','reason':'候选1更可能','evidence_ids':['a:P1']}
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        self.case['decision_policy']['model_review']=True
        self.facts['conflicts']=[self.conflict('total_max',[(100,'a:P1','满分100分'),(50,'b:P1','满分50分')])]
        audit=resolve_conflicts(self.case,self.facts,[],Client(),self.root)
        self.assertEqual(audit[0]['action'],'ask_critical')
        self.assertEqual(audit[0]['model_review']['candidate'],'1')
        self.assertEqual(len(self.facts['conflicts']),1)

    def test_doc_conversion_keeps_original_path_hash_and_evidence(self):
        from report_agent.common import digest
        from docx import Document
        from types import SimpleNamespace
        original=self.root/'教案.doc';original.write_bytes(b'legacy-doc-fixture')
        def convert(cmd,**kw):
            folder=Path(cmd[cmd.index('--outdir')+1]);doc=Document();doc.add_paragraph('本轮课程教学目标')
            doc.save(folder/'教案.docx');return SimpleNamespace(returncode=0,stderr='')
        with patch('report_agent.readers.shutil.which',return_value='/test/libreoffice'),patch('report_agent.readers.subprocess.run',side_effect=convert):
            parsed=read_source({'id':'legacy','path':str(original),'role':'current'})
        self.assertEqual(parsed['path'],str(original));self.assertEqual(parsed['sha256'],digest(original))
        self.assertTrue(any('教学目标' in b['text'] for b in parsed['blocks']))
        self.assertTrue(all(b['id'].startswith('legacy:') for b in parsed['blocks']))

if __name__=='__main__':unittest.main()
