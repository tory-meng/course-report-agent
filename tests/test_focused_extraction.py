import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.common import save,load,signature
from report_agent.extraction_quality import formal_goal_records,registry,validate_quality,route
from report_agent.grading_schemes import collect_schemes,prepare_scheme_question,apply_scheme
from report_agent.structured import hydrate_records
from report_agent.facts import validate_extracted,extract
from report_agent.human_review import collect_answers,apply_answers
from report_agent.readers import read_source


def block(ref,text,**kw):return {'id':ref,'text':text,'source_id':ref.split(':')[0],'role':'current','_quality':True,**kw}
def record(kind,value,refs):return {'kind':kind,'value':value,'evidence_ids':refs}
def source(bs,sid='s'):return {'id':sid,'role':'current','path':sid+'.docx','sha256':sid,'sheets':[],'blocks':bs}

class FocusedExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.case={'id':'c','course_name':'测试课','sources':[],'decision_policy':{'mode':'grade_first_minimal_v1','model_review':False}}
    def tearDown(self):self.tmp.cleanup()
    def test_author_not_teacher_and_calendar_not_report_date(self):
        b=block('s:P1','撰写人：张老师；审核人：李老师；2024年6月24日')
        for v,msg in [({'teacher':'张老师'},'撰写人'),({'report_date':'2024-06-24'},'报告日期')]:
            with self.assertRaisesRegex(ValueError,msg):validate_extracted({'records':[record('metadata',v,[b['id']])]},[b],{b['id']:b})
    def test_wrong_citation_and_wrong_section_rejected(self):
        for text,quote,msg in [('本轮采取小组合作解决实践不足问题。','本轮平均分为78.5。','连续原文'),
                               ('课程档案（实践课）','课程档案（实践课）','标题')]:
            b=block('s:P1',quote)
            with self.assertRaisesRegex(ValueError,msg):validate_extracted({'records':[record('narratives',{'id':'N1','section':'6.2','text':text},[b['id']])]},[b],{b['id']:b})
    def test_formal_matrix_preserves_relationship_without_inventing_coefficient(self):
        bs=[block('s:T1R1','支撑的毕业要求 | 支撑的毕业要求指标点 | 学习目标 | 本课程对指标点的权重系数'),
            block('s:T1R2','1.工程知识 | 1-3掌握程序设计原理 | 能够了解无人车基本结构，完成控制及通讯任务。 | 0.2')]
        wire,consumed=formal_goal_records(bs);self.assertEqual(len(wire),2)
        self.assertEqual(wire[1]['value']['goal_weights'],{'G1':None})
        hydrated=hydrate_records({'records':wire},bs);validate_quality(hydrated,bs)
        with self.assertRaisesRegex(ValueError,'覆盖检查'):validate_quality([],bs)
    def test_empty_assessment_and_method_are_not_success(self):
        for focus,text in [('assessment','本课程平时成绩占50%，期末成绩占50%。'),('teaching','通过小组合作学习，培养学生协调合作的能力。')]:
            with self.assertRaisesRegex(ValueError,'覆盖检查'):validate_quality([],[block('s:P1',text,focus=focus)])
    def test_registry_never_carries_old_narrative_or_goal_prose(self):
        r=registry({'goals':[{'id':'G1','text':'旧目标的正文'}],'narratives':[{'id':'N1','text':'旧叙述'}]})
        self.assertEqual(r,{'goals':[{'id':'G1'}]})
    def test_word_merges_preserve_coordinates_without_repeating_text(self):
        from docx import Document
        doc=Document();t=doc.add_table(rows=2,cols=3);t.cell(0,0).merge(t.cell(0,2)).text='课程名称'
        t.cell(1,0).text='目标';t.cell(1,1).text='描述';t.cell(1,2).text='权重'
        path=self.root/'m.docx';doc.save(path);s=read_source({'id':'s','path':str(path)})
        b=s['blocks'][0];self.assertEqual(b['text'],'课程名称');self.assertEqual(len(b['cells']),3)
        self.assertEqual([c['anchor_column'] for c in b['cells']],[1,1,1])
    def test_excel_formulas_and_caches_kept_separate(self):
        import openpyxl
        w=openpyxl.Workbook();w.active['A1']='=1+1';p=self.root/'f.xlsx';w.save(p)
        s=read_source({'id':'s','path':str(p)}) if False else None
        from report_agent.readers import excel_sheets
        sh=excel_sheets(p)[0];self.assertEqual(sh['formulas']['A1']['expression'],'=1+1');self.assertIn('A1',sh['formula_cells'])
    def scheme_sources(self):
        return [source([block('s:P1','本课程平时成绩占50%，项目考核成绩占50%。'),
                        block('s:P2','按百分制的总评成绩=平时成绩（满分20分）+验收答辩成绩（满分40分）+设计报告成绩（满分40分）。')])]
    def test_distinct_complete_schemes_not_mixed(self):
        cs=collect_schemes(self.scheme_sources());self.assertEqual(len(cs),2)
        self.assertEqual(sorted(x['weight'] for x in cs[0]['components']),[.5,.5])
        self.assertEqual(sorted(x['weight'] for x in cs[1]['components']),[.2,.4,.4])
        f={'metadata':{},'grading_schemes':cs}
        q,a=prepare_scheme_question(self.case,f,self.scheme_sources(),None,self.root)
        self.assertEqual(q['target']['type'],'grading_scheme');self.assertNotIn('grading_scheme_id',self.case)
        apply_scheme(self.case,f,cs[1],'user_confirmation')
        self.assertEqual(self.case['grading_scheme_id'],cs[1]['id']);self.assertNotIn('goal_rules',f)
    def test_hierarchical_table_keeps_inner_and_total_percentages(self):
        bs=[block('s:T1R1','考核方式 | 考核方式 | 占比 | 占比'),block('s:T1R2','考核方式 | 项目 | 内部 | 总评'),
            block('s:T1R3','过程性考核 | 表现 | 20% | 50%'),block('s:T1R4','过程性考核 | 结果 | 80% | 50%'),
            block('s:T1R5','终结性考核 | 报告 | 40% | 50%'),block('s:T1R6','终结性考核 | 答辩 | 60% | 50%')]
        cs=collect_schemes([source(bs)]);self.assertEqual(len(cs),1)
        self.assertEqual(cs[0]['children'][0]['components'][0]['within_parent_weight'],.2)
        self.assertEqual(cs[0]['components'][0]['weight'],.5)
    def test_user_selection_is_atomic_and_resume_stable(self):
        ss=self.scheme_sources();f={'metadata':{},'grading_schemes':collect_schemes(ss)}
        q,a=prepare_scheme_question(self.case,f,ss,None,self.root)
        qs={'protocol':'human_review_v1','context_sha256':'hash','questions':[q],'source_paths':{'s':'大纲.docx'}}
        inputs=iter(['教师','2','依据本轮确认的考核方案'])
        answer=collect_answers(qs,self.root/'answers.json',interactive=True,input_fn=lambda _:next(inputs),print_fn=lambda _:None)
        apply_answers(qs,answer,self.case,f,ss,self.root)
        second,a=prepare_scheme_question(self.case,f,ss,None,self.root);self.assertIsNone(second)
        self.assertEqual(f['grading_scheme_resolution']['status'],'selected')
    def test_model_can_choose_reference_but_cannot_establish_current_weights(self):
        ss=self.scheme_sources();f={'metadata':{},'grading_schemes':collect_schemes(ss)};self.case['decision_policy']['model_review']=True
        class Client:
            config={'max_new_tokens':512};identity={'max_output_tokens':512}
            def reference_resolver(inner,*args,**kw):return {'hits':[{'reference_id':'ref_a','file':'参考课程.pdf','locator':'page:3','text':'考核划分'}]}
            def request(inner,messages,**kw):
                answer={'candidate':f['grading_schemes'][0]['id'],'reason':'参考划分相似，但当前依据不足需确认','evidence_ids':['s:P1'],'reference_id':'ref_a'}
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        q,a=prepare_scheme_question(self.case,f,ss,Client(),self.root)
        self.assertIsNotNone(q);self.assertIn('参考课程.pdf',q['explanation']);self.assertNotIn('grading_scheme_id',self.case)
    def test_quality_failures_block_full_run_and_are_saved(self):
        class Client:
            config={};identity={}
            def require_structured(self):pass
            def request(self,messages,**kw):return {'text':'{"records":[]}','schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        bs=[block('s:P1','通过小组合作学习，培养学生协调合作的能力。')]
        f=extract(self.case,[source(bs)],Client(),self.root,'T',defer_validation=True)
        self.assertEqual(f['extraction_quality']['status'],'blocked');self.assertTrue(load(self.root/'extraction_coverage.json')['failures'])
    def test_training_export_rejects_blocked_coverage(self):
        from training.data import export
        run=self.root/'run';save(run/'run_manifest.json',{'case_id':'c'});save(run/'extraction_coverage.json',{'status':'blocked'})
        with self.assertRaisesRegex(ValueError,'覆盖检查'):export([run],self.root/'train')

if __name__=='__main__':unittest.main()

class MatrixRegressionTests(unittest.TestCase):
    def test_matrix_sum_row_not_mistaken_for_another_assessment(self):
        from report_agent.extraction_quality import direct_matrices
        bs=[block('s:T1R1','学习目标 | 学习目标 | 1 | 2 | 总计'),
            block('s:T1R2','方式 | 权重 | 目标1 | 目标2 | 权重'),
            block('s:T1R3','平时成绩 | 0.3 | 0.4 | 0.6 | 1'),
            block('s:T1R4','答辩 | 0.7 | 0.6 | 0.4 | 1'),
            block('s:T1R5','权重 | 1.0 | 1 | 1 | 2')]
        records,_=direct_matrices(bs)
        self.assertEqual(len([r for r in records if r['kind']=='assessment_goal_weights']),4)
    def test_conflicting_matrices_selected_as_whole(self):
        from report_agent.grading_schemes import collect_matrix_record,prepare_matrix_question,apply_matrix
        f={'metadata':{},'assessments':[{'id':'A1','name':'答辩'}]};case={}
        for table,weight in [('T1',.3),('T2',.4)]:
            b=block('s:'+table+'R2','答辩 | 目标1 | '+str(weight))
            r=hydrate_records({'records':[record('assessment_goal_weights',{'id':'A1:G1','assessment_id':'A1','goal_id':'G1','weight':weight},[b['id']])]},[b])[0]
            collect_matrix_record(f,r)
        q=prepare_matrix_question(case,f);self.assertEqual(len(q['options']),2)
        apply_matrix(case,f,q['options'][1]['value'],'user_confirmation')
        self.assertEqual(f['assessment_goal_weights'][0]['weight'],.4)
        self.assertIsNone(prepare_matrix_question(case,f))
    def test_same_temporary_id_for_different_entities_is_rejected(self):
        from report_agent.extraction_quality import normalize_entities
        answer={'records':[record('assessments',{'id':'A1','name':'平时'},['s:1']),record('assessments',{'id':'A1','name':'答辩'},['s:2'])]}
        with self.assertRaisesRegex(ValueError,'同一临时ID'):normalize_entities(answer,{})
    def test_teacher_role_not_confused_when_author_in_same_paragraph(self):
        b=block('s:P1','任课教师：甲；撰写人：乙')
        with self.assertRaisesRegex(ValueError,'撰写人'):
            validate_extracted({'records':[record('metadata',{'teacher':'乙'},[b['id']])]},[b],{b['id']:b})

class ReferenceDeliveryTests(unittest.TestCase):
    def test_active_lookup_is_in_actual_request(self):
        class Client:
            config={};identity={};seen=None
            def require_structured(self):pass
            def log(self,*args):pass
            def reference_resolver(self,*args,**kw):return {'hits':[{'reference_id':'ref_a','text':'字段解释'}]}
            def request(self,messages,**kw):
                self.seen=json.loads(messages[-1]['content'])
                answer={'records':[record('metadata',{'course_name':'测试课'},['s:P1'])]}
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
        c=Client();case={'id':'c','course_name':'测试课','decision_policy':{'mode':'grade_first_minimal_v1'}}
        with tempfile.TemporaryDirectory() as tmp:
            extract(case,[source([block('s:P1','课程名称：测试课')])],c,Path(tmp),'T',defer_validation=True)
        self.assertEqual(c.seen['reference_examples']['hits'][0]['reference_id'],'ref_a')
    def test_final_validation_failure_records_lookup_without_claiming_delivery(self):
        from report_agent.client import task,TaskValidationError
        class Client:
            config={};identity={}
            def request(self,*args,**kw):return {'text':'{}'}
            def reference_resolver(self,*args):return {'hits':[{'reference_id':'ref_a'}]}
        def reject(a):raise ValueError('课程名称证据不一致')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'r.json'
            with self.assertRaises(TaskValidationError):task(Client(),[{'role':'user','content':'证据'}],p,reject,{'task_kind':'extract'},retries=0)
            entry=load(p)['attempts'][-1]
            self.assertTrue(entry['reference_lookup']['hits']);self.assertFalse(entry['reference_delivered_to_retry'])
