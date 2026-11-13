import copy
import unittest
from report_agent.common import ROOT,load
from report_agent.grading_schemes import apply_scheme
from report_agent.report_scope import assessments,audit
from report_agent.compute import calculate
from report_agent.tables import build
from report_agent.charts import jobs
from report_agent.readers import ingest
from report_agent.grades import parse_grades


class ReportScopeTests(unittest.TestCase):
    def setUp(self):
        self.facts=load(ROOT/'examples/demo_course/facts.json')
        self.case=load(ROOT/'examples/demo_course/case.json')
        self.facts['assessments'].append({'id':'OLD','name':'未采用方案','source_refs':['policy:L1'],'quotes':{'policy:L1':'另一个方案'}})
        self.scheme={'id':'scheme_selected','components':[{'name':'期末考核','weight':.6},{'name':'报告成绩','weight':.4}],
                     'source_refs':['policy:L1'],'quotes':{'policy:L1':'期末考核占60%，报告成绩占40%'}}
        self.facts['grading_schemes']=[self.scheme]
        apply_scheme(self.case,self.facts,self.scheme,'user_confirmation')

    def test_selected_component_is_materialized_without_inventing_item_mapping(self):
        rows=assessments(self.facts)
        self.assertEqual([a['name'] for a in rows],['期末考核','报告成绩'])
        self.assertEqual(rows[1]['total_weight'],.4)
        self.assertNotIn('terms',rows[1])
        self.assertTrue(any(a['id']=='OLD' for a in self.facts['assessments']))
        self.assertEqual([a['name'] for a in audit(self.facts)['matrix_only_assessments']],['过程考核'])

    def test_report_tables_and_charts_use_selected_scheme_and_keep_missing_values(self):
        sources=ingest(self.case);grades=parse_grades(self.case,sources,self.facts)
        calc=calculate(self.case,self.facts,grades)
        tables=build(self.facts,calc,load(ROOT/'templates/course/buu_course_r2/template_contract.json'))
        self.assertEqual([r[1] for r in tables['T2-4'][0]['rows'][1:]],['期末考核','报告成绩'])
        self.assertEqual(tables['T2-4'][0]['rows'][-1][3: ],['待核对','—','—'])
        stat=tables['T5-1'][0]
        self.assertNotIn('未采用方案',str(stat))
        self.assertNotIn('过程考核',str(stat))
        self.assertNotIn('OLD',calc['assessments'])
        self.assertFalse(any(j['id']=='assessment_B' for j in jobs(self.facts,calc)))
        for entries in tables.values():
            for table in entries:
                self.assertAlmostEqual(sum(table['widths_mm']),146,delta=.1)
                self.assertTrue(all(len(row)==len(table['widths_mm']) for row in table['rows']))

    def test_total_students_is_not_zero_when_goal_scores_are_missing(self):
        self.facts['goal_rules']=[]
        sources=ingest(self.case);grades=parse_grades(self.case,sources,self.facts)
        calc=calculate(self.case,self.facts,grades)
        tables=build(self.facts,calc,load(ROOT/'templates/course/buu_course_r2/template_contract.json'))
        for row in tables['T5-3'][0]['rows'][1:]:
            self.assertEqual(row[1],str(calc['registered_count']))
            self.assertEqual(row[5:9],['—','—','—','—'])


if __name__=='__main__':unittest.main()
