import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from report_agent.common import signature
from report_agent.extraction_quality import schema_for, direct_matrices
from report_agent.structured import extraction_schema
from report_agent.facts import validate_extracted


def block(ref,text):
    return {'id':ref,'text':text,'role':'current','source_id':'s','focus':'identity',
            '_quality':True,'extract_kinds':['metadata']}


class IdentityGroundingTests(unittest.TestCase):
    def test_probe_records_optional_conflict_but_keeps_critical_conflict_blocking(self):
        from report_agent.facts import extract
        for field,values,expected in [('major',['人工智能','电子信息工程'],'passed'),('course_name',['测试课','其他课'],'needs_review')]:
            bs=[block('s:P'+str(i),'课程名称：'+(value if field=='course_name' else '测试课')+'；专业：'+(value if field=='major' else '人工智能')) for i,value in enumerate(values)]
            class Client:
                config={};identity={};count=0
                def require_structured(self):pass
                def log(self,*args):pass
                def request(inner,messages,**kw):
                    i=inner.count;inner.count+=1
                    answer={'records':[{'kind':'metadata','value':{'course_name':values[i] if field=='course_name' else '测试课',**({'major':values[i]} if field=='major' else {})},'evidence_ids':[bs[i]['id']]}]}
                    return {'text':json.dumps(answer),'schema_enforced':True,'schema_valid':True,'schema_sha256':signature(kw['output_schema'])}
            case={'id':'c','course_name':'测试课','decision_policy':{'mode':'grade_first_minimal_v1','model_review':False}}
            with tempfile.TemporaryDirectory() as tmp,patch('report_agent.facts.plan_chunks',return_value=(1400,[[b] for b in bs])):
                facts=extract(case,[{'id':'s','path':'s.docx','role':'current','sheets':[],'blocks':bs}],Client(),Path(tmp),'T',max_batches=2,defer_validation=True)
                probe=json.loads((Path(tmp)/'probe_validation.json').read_text())
                self.assertEqual(probe['status'],expected)
                if field=='major':
                    self.assertNotIn('major',facts['metadata'])
                    self.assertTrue(facts['data_quality_issues'])
                    self.assertTrue((Path(tmp)/'probe_decision_audit.json').exists())
    def test_grade_calendar_heading_is_explicit_evidence_for_source_priority(self):
        from report_agent.direct_extract import labelled_row
        b=block('s:R1','C1=2023-2024学年第2学期记分册')
        values,covered=labelled_row(b)
        self.assertTrue(covered)
        self.assertEqual(values,{'academic_year':'2023-2024','semester':'2'})
    def test_unmentioned_identity_fields_are_not_offered_in_schema(self):
        bs=[block('s:R1','C1=专   业 | C3=人工智能 | C5=任课教师 | C6=测试龘')]
        schema=schema_for(bs,['metadata'],extraction_schema(bs,['metadata']))
        fields=schema['properties']['records']['items']['anyOf'][0]['properties']['value']['properties']
        self.assertIn('major',fields)
        self.assertIn('teacher',fields)
        for key in ('course_code','offering_code','college','hours','credits','course_nature','total_max'):
            self.assertNotIn(key,fields)

    def test_metadata_numeric_values_need_current_evidence(self):
        b=block('s:P1','总学时：32；学分：2')
        answer={'records':[{'kind':'metadata','value':{'hours':24},'evidence_ids':[b['id']]}]}
        with self.assertRaisesRegex(ValueError,'数值'):
            validate_extracted(answer,[b],{b['id']:b})

    def test_vertical_matrix_cites_parent_name_row(self):
        bs=[block('s:T1R1','考核类型 | 项目 | 课程目标1 | 课程目标2'),
            block('s:T1R2','平时成绩 | 平均分 | 20 | 30'),
            block('s:T1R3',' | 权重 | 0.4 | 0.6')]
        records,_=direct_matrices(bs)
        self.assertEqual(len(records),3)
        for record in records:
            self.assertIn('s:T1R2',record['evidence_ids'])
            self.assertIn('s:T1R3',record['evidence_ids'])
    def test_same_method_name_keeps_distinct_implementation_evidence(self):
        from report_agent.extraction_quality import normalize_entities
        answer={'records':[{'kind':'methods','value':{'id':'M1','name':'课堂讲授','description':text},'evidence_ids':['s:P1']} for text in ('讲授控制器开发流程','讲授通讯原理')]}
        normalize_entities(answer,{})
        self.assertNotEqual(answer['records'][0]['value']['id'],answer['records'][1]['value']['id'])


if __name__=='__main__':unittest.main()
