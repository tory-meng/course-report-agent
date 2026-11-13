import copy
import tempfile
import unittest
from pathlib import Path
import openpyxl
from report_agent.grade_layout import layouts,mappings,basis
from report_agent.grades import auto_mapping,parse_grades
from report_agent.decision_policy import grade_candidates
from report_agent.grade_formulas import audit,apply_verified
from report_agent.teaching_presentation import prepare,scope_audit,uncovered_goals
from report_agent.report_scope import method_label
from report_agent.report_analysis import build,below_details

def source(rows,merged=None):
    return {'id':'s','path':'/fixtures/成绩表.xlsx','role':'current','kind':'grades',
            'sheets':[{'name':'表1','rows':rows,'merged_cells':merged or [],'formula_cells':[]}]}

class LayoutTests(unittest.TestCase):
    def test_two_level_header_has_one_identity_and_real_column_paths(self):
        s=source([['学号','姓名','成绩构成','',''],['','','平时考核','期末考核','总评'],['001','甲',80,82,81]],['A1:A2','B1:B2','C1:E1'])
        m=auto_mapping(s,[])[0]
        self.assertEqual((m['student_id'],m['name'],m['total'],m['header_row']),(1,2,5,2))
        self.assertEqual(m['header_paths']['4'],['成绩构成','期末考核'])
        self.assertEqual(len(grade_candidates([s])),1)
        self.assertEqual(s['sheets'][0]['rows'][1][0],'')

    def test_teacher_aliases_and_xls_merged_coordinates(self):
        s=source([['学籍号','学生姓名','总成绩'],['','',''],['0001','甲',80]],[[0,2,0,1],[0,2,1,2],[0,2,2,3]])
        m=auto_mapping(s,[])[0];self.assertEqual((m['header_row'],m['total']),(2,3))
        g=parse_grades({'grade_tables':[m]},[s],{'items':[]})
        self.assertEqual(g['students'][0]['id'],'0001')

    def test_duplicate_total_needs_confirmation_and_name_is_not_identity(self):
        with self.assertRaisesRegex(ValueError,'total'):
            auto_mapping(source([['学号','总评','总成绩'],['001',80,81]]),[])
        with self.assertRaises(ValueError):auto_mapping(source([['姓名','总评'],['甲',80]]),[])

    def test_converted_header_cannot_silently_be_used_as_raw(self):
        s=source([['学号','平时考核（折算分20%）','总成绩'],['001',16,80]])
        items=[{'id':'u','name':'平时考核','score_basis':'raw'}]
        m=auto_mapping(s,items)[0]
        self.assertEqual(m['item_metadata']['u']['score_basis'],'converted')
        with self.assertRaisesRegex(ValueError,'口径冲突'):parse_grades({'grade_tables':[m]},[s],{'items':items})

    def test_percentage_alone_does_not_prove_converted_and_zero_is_preserved(self):
        s=source([['学号','平时考核（20%）','总评'],['001',0,0]])
        items=[{'id':'u','name':'平时考核','score_basis':'raw'}];m=auto_mapping(s,items)[0]
        self.assertEqual(m['item_metadata']['u']['score_basis'],'unknown')
        self.assertEqual(parse_grades({'grade_tables':[m]},[s],{'items':items})['students'][0]['scores']['u'],0)
        self.assertEqual(basis('未折算原始分'),'raw')

    def test_raw_and_converted_subcolumns_selected_by_explicit_basis(self):
        s=source([['学号','平时考核','','总评'],['','原始分','折算分',''],['001',80,16,80]],['A1:A2','B1:C1','D1:D2'])
        m=auto_mapping(s,[{'id':'u','name':'平时考核','score_basis':'raw'}])[0]
        self.assertEqual(m['items']['u'],2)

    def test_unlabelled_generic_score_not_assumed_total(self):
        with self.assertRaises(ValueError):auto_mapping(source([['学号','成绩'],['001',80]]),[])

class FormulaLayoutTests(unittest.TestCase):
    def test_single_total_and_alias_headers_do_not_add_course_specific_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'actual.xlsx';wb=openpyxl.Workbook();ws=wb.active;ws.title='表1'
            rows=[['学籍号','学生姓名','平时考核','期末考核','总成绩'],['001','甲',80,90,88],['002','乙',0,0,0]]
            for r in rows:ws.append(r)
            for n in (2,3):ws.cell(n,5,f'=0.2*C{n}+0.8*D{n}')
            wb.save(p)
            s=source(rows);s.update(path=str(p),sha256='fixture',blocks=[{'id':'s:表1:R1','text':'表头','role':'current'}])
            result=audit({},[s],Path(tmp));self.assertEqual(result[0]['status'],'verified')
            f={'metadata':{},'assessments':[],'items':[]};case={}
            apply_verified(case,f,[s],result)
            self.assertEqual([c['weight'] for c in result[0]['components']],[.2,.8])
            self.assertNotIn('报告与答辩',f['metadata']['grade_formula_text'])
            self.assertNotIn('ROUND',f['metadata']['grade_formula_text'])

class PresentationTests(unittest.TestCase):
    def test_missing_teaching_support_is_exposed_not_forced(self):
        f={'goals':[{'id':'G1'},{'id':'G2'},{'id':'G3'}],'methods':[{'goal_ids':['G1']}]}
        self.assertEqual(uncovered_goals(f,[{'goal_ids':['G2']}]),['G3'])
    def test_display_names_do_not_change_original_names_or_ids(self):
        f={'methods':[{'id':'m1','name':'通过STM32控制器原理讲授。'}, {'id':'m2','name':'小组合作学习。'}]}
        original=copy.deepcopy(f);prepare(f)
        self.assertEqual(method_label(f,f['methods'][0]),'讲授与演示（控制器开发）')
        self.assertEqual(method_label(f,f['methods'][1]),'小组协作')
        self.assertEqual([(m['id'],m['name']) for m in f['methods']],[(m['id'],m['name']) for m in original['methods']])

    def test_only_explicit_academic_year_conflicts_flagged(self):
        s=[{'id':'s','role':'current','blocks':[{'id':'s:1','text':'20 24—20 25学年'}, {'id':'s:2','text':'撰写日期2025年4月'}]}]
        r=scope_audit({},s,{'metadata':{'academic_year':'2023-2024'}})
        self.assertEqual(len(r['issues']),1);self.assertEqual(r['issues'][0]['source_ref'],'s:1')

    def test_low_values_and_differences_are_same_source_calculations(self):
        row={'threshold':.7,'values':{'S1':.65,'S2':.8},'mean':.725}
        c={'goals':{'G1':row},'aggregate_goals':{'G1':{'mean':.72}},'evaluation_context':{'mode':'scenario_estimate'}}
        r=build(c)['goals']['G1'];self.assertEqual(r['below'][0]['student_id'],'S1')
        self.assertAlmostEqual(r['below'][0]['gap'],.05);self.assertAlmostEqual(r['difference'],.005)
        self.assertEqual(r['evidence_status'],'proxy_estimate')
        self.assertEqual(below_details({**row,'threshold':None}),[])

if __name__=='__main__':unittest.main()
