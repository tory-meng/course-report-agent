import copy
import json
import tempfile
import unittest
from pathlib import Path
from report_agent.common import save,load,signature
from report_agent.human_review import build_questions,collect_answers,validate_answers,apply_answers

class HumanReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.case={'id':'c','course_name':'课程甲','sources':[]}
        self.facts={'metadata':{'source_refs':['grade:R1'],'quotes':{'grade:R1':'课程甲'}},'conflicts':[],
                    'data_quality_issues':['课程性质待核对：有矛盾','仍缺少目标'], 'review':{'approved':False}}
        class Policy:pass
        p=Policy();p.config={'deferred_fields':{'course_nature':'不同材料记载不同'}}
        p.actions=[{'action':'defer_field','detail':{'field':'course_nature','value':value},'record':{'source_refs':[ref],'quotes':{ref:quote}}}
                   for value,ref,quote in [('选修','grade:R1','课程性质：选修'),('必修','doc:R1','课程性质：必修')]]
        self.sources=[{'id':'attendance','role':'current','path':'考勤.xls','sheets':[{'name':'S','rows':[['学号','姓名','考勤']]}]},
                      {'id':'grade','role':'current','path':'成绩单.xls','sheets':[{'name':'S','rows':[['学号','姓名','总评成绩']]}]}]
        self.q=build_questions(self.case,self.facts,self.sources,p);self.q['context_sha256']='test-bound-context'
    def tearDown(self):self.tmp.cleanup()
    def answer(self,q,index=0):return {'question_id':q['question_id'],'question_sha256':q['question_sha256'],'status':'selected',
                                     'option_id':q['options'][index]['option_id'],'reviewer':'测试确认人','reason':'按本轮制度确认'}
    def document(self,rows):return {'protocol':self.q['protocol'],'context_sha256':self.q['context_sha256'],'answers':rows}
    def test_actual_types_and_attendance_excluded_from_grade_choices(self):
        self.assertEqual(len(self.q['questions']),7)
        grade=next(q for q in self.q['questions'] if q['target']['type']=='grade_tables')
        self.assertEqual(len(grade['options']),1)
        self.assertEqual(grade['options'][0]['value'][0]['source_id'],'grade')
        self.assertFalse(any(q['options'][0]['value']=='zero' for q in self.q['questions']))
    def test_choose_save_pause_and_resume_without_reasking_chosen(self):
        values=iter(['教师甲','2','核对大纲','q'])
        file=self.root/'answers.json'
        a=collect_answers(self.q,file,interactive=True,input_fn=lambda _:next(values),print_fn=lambda _:None)
        self.assertEqual(a['session_status'],'paused');self.assertEqual(len(a['answers']),1)
        self.assertEqual(a,load(file))
        resume=iter(['教师乙']+['0']*6)
        b=collect_answers(self.q,self.root/'b.json',a,True,input_fn=lambda _:next(resume),print_fn=lambda _:None)
        self.assertEqual(b['session_status'],'completed');self.assertEqual(b['answers'][0],a['answers'][0])
    def test_eof_keeps_prior_choices_and_unknown_has_no_default(self):
        values=iter(['甲','0'])
        def inp(_):
            try:return next(values)
            except StopIteration:raise EOFError
        a=collect_answers(self.q,self.root/'a.json',interactive=True,input_fn=inp,print_fn=lambda _:None)
        self.assertEqual(a['session_status'],'paused');self.assertEqual(a['answers'][0]['status'],'deferred')
        apply_answers(self.q,a,self.case,self.facts,self.sources,self.root)
        self.assertNotIn('course_nature',self.facts['metadata']);self.assertNotIn('evaluation_policy',self.case)
    def test_changed_context_unknown_option_duplicate_or_blank_reason_rejected(self):
        row=self.answer(self.q['questions'][0]);a=self.document([row])
        variants=[]
        b=copy.deepcopy(a);b['context_sha256']='other';variants.append(b)
        b=copy.deepcopy(a);b['answers'][0]['option_id']='o_bad';variants.append(b)
        b=copy.deepcopy(a);b['answers'].append(copy.deepcopy(row));variants.append(b)
        b=copy.deepcopy(a);b['answers'][0]['reason']=' ';variants.append(b)
        b=copy.deepcopy(a);b['answers'][0]['question_sha256']='other';variants.append(b)
        for b in variants:
            with self.assertRaises(ValueError):validate_answers(self.q,b)
    def test_confirmations_apply_only_selected_fields_and_create_user_rule_source(self):
        rows=[self.answer(q) for q in self.q['questions']]
        a=self.document(rows);apply_answers(self.q,a,self.case,self.facts,self.sources,self.root)
        self.assertEqual(self.facts['metadata']['course_nature'],'选修')
        self.assertEqual(self.facts['data_quality_issues'],['仍缺少目标'])
        self.assertFalse(self.facts['review']['approved'])
        self.assertEqual(self.case['grade_tables'][0]['total'],3)
        self.assertEqual(self.case['evaluation_policy']['normal'],'include')
        self.assertEqual(self.case['evaluation_policy']['absent'],'exclude')
        self.assertTrue(self.case['evaluation_policy']['source_refs'][0].startswith('human_policy_'))
        self.assertTrue((self.root/'user_evaluation_policy.txt').is_file())
        self.assertEqual(self.facts['metadata']['evaluation_policy'],self.case['evaluation_policy'])
    def test_confirmed_rules_work_with_actual_reader_and_calculator(self):
        from report_agent.readers import read_source,ingest
        from report_agent.grades import parse_grades
        from report_agent.compute import calculate
        from report_agent.facts import validate_facts
        file=self.root/'grades.csv';file.write_text('学号,姓名,总评成绩\n001,甲,80\n002,乙,缺考\n',encoding='utf-8')
        cfg={'id':'grade','path':str(file),'role':'current','kind':'grades'}
        case={'id':'c','course_name':'课程甲','sources':[cfg]};sources=[read_source(cfg)]
        facts={'metadata':{'course_name':'课程甲','total_max':100,'pass_score':60,
            'source_refs':['grade:grades:R1'],'quotes':{'grade:grades:R1':sources[0]['blocks'][0]['text']}},'conflicts':[]}
        q=build_questions(case,facts,sources);q['context_sha256']='full-integration'
        a={'protocol':q['protocol'],'context_sha256':q['context_sha256'],'answers':[self.answer(x) for x in q['questions']]}
        apply_answers(q,a,case,facts,sources,self.root)
        actual=ingest(case);validate_facts(facts,actual,'课程甲')
        grades=parse_grades(case,actual,facts);calc=calculate(case,facts,grades)
        self.assertEqual(calc['total']['valid_count'],1);self.assertEqual(calc['total']['mean'],80)
        self.assertFalse(facts['review']['approved'])
    def test_generic_conflict_uses_candidate_not_arbitrary_response_value(self):
        self.facts['conflicts']=[{'conflict_id':'c1','kind':'metadata','id':None,'field':'credits',
            'candidates':[{'value':2,'source_refs':['s:1'],'quotes':{'s:1':'2学分'}},{'value':3,'source_refs':['s:2'],'quotes':{'s:2':'3学分'}}]}]
        q=build_questions(self.case,self.facts,[]);q['context_sha256']='bound'
        row=self.answer(q['questions'][0],1);row['value']=99
        a={'protocol':q['protocol'],'context_sha256':'bound','answers':[row]}
        apply_answers(q,a,self.case,self.facts,[],self.root)
        self.assertEqual(self.facts['metadata']['credits'],3);self.assertFalse(self.facts['conflicts'])

if __name__=='__main__':unittest.main()
