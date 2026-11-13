import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from docx import Document
from report_agent.common import load,signature
from report_agent.reference_db import build,search,status_report,reference_identity,attach_resolver,enrich,connect,pack_paragraphs
from report_agent.client import task

def make_doc(file,text):
    file.parent.mkdir(parents=True,exist_ok=True);d=Document();d.add_paragraph(text);d.save(file)

class ReferenceDBTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'references';self.db=self.root/'references.sqlite3'
        make_doc(self.source/'course'/'课程样本.docx','2.6 学生成绩评定方法\n课程性质为选修，考核形式为考查。参考课程名为其他课程。样本权重40%。')
        make_doc(self.source/'graduation'/'毕业设计.docx','毕设参考：成绩评定方法包含毕业设计答辩。')
        build(self.source,self.db,log=lambda s:None)
    def tearDown(self):self.tmp.cleanup()
    def test_type_filter_chinese_query_and_no_hit(self):
        hits=search(self.db,'考核形式 考查','course')
        self.assertTrue(hits);self.assertTrue(all(h['report_type']=='course' and h['reference_only'] for h in hits))
        self.assertEqual(search(self.db,'unfindable_randomtoken_999','all'),[])
        self.assertEqual(search(self.db,'毕业设计答辩','graduation')[0]['report_type'],'graduation')
    def test_incremental_build_skips_and_removed_source_disabled(self):
        with patch('report_agent.reference_db.read_units',side_effect=AssertionError('不应重读')):build(self.source,self.db,log=lambda s:None)
        (self.source/'graduation'/'毕业设计.docx').unlink();build(self.source,self.db,log=lambda s:None)
        self.assertEqual(status_report(self.db)['documents'],1);self.assertEqual(search(self.db,'毕业设计','graduation'),[])
    def test_snapshot_and_source_change_detection(self):
        cfg={'reference_library':{'enabled':True,'db':str(self.db),'source':str(self.source)}}
        identity=reference_identity(cfg)
        class Client:pass
        c=Client();out=self.root/'out';out.mkdir();attach_resolver(c,identity,out,'course')
        result=c.reference_resolver('metadata.course_nature应区分考查',[])
        self.assertTrue(result['hits']);self.assertEqual(result['database_sha256'],identity['sha256'])
        self.assertNotIn('40%',result['hits'][0]['text']);self.assertIn('【参考数值】',result['hits'][0]['text'])
        self.assertIsNone(c.reference_resolver('输出结构不符JSON',[]))
        make_doc(self.source/'course'/'课程样本.docx','改变后的课程资料')
        with self.assertRaisesRegex(ValueError,'不一致'):reference_identity(cfg)
        self.assertTrue(c.reference_resolver('考核形式 考查',[])['hits'])
    def test_retry_uses_references_without_changing_current_evidence(self):
        class Fake:
            calls=0;config={};identity={}
            def log(self,*a):pass
            def request(inner,messages,**kw):
                inner.calls+=1
                if inner.calls==2:
                    self.assertIn('原始当前课程证据',messages[-1]['content']);self.assertIn('参考资料检索结果',messages[-1]['content'])
                return {'text':json.dumps({'ok':inner.calls==2})}
        c=Fake();identity=reference_identity({'reference_library':{'db':str(self.db),'source':str(self.source)}})
        out=self.root/'out';out.mkdir();attach_resolver(c,identity,out,'course')
        def validate(answer):
            if not answer['ok']:raise ValueError('metadata.course_nature与考核形式混淆')
        record=out/'r.json';task(c,[{'role':'user','content':'原始当前课程证据'}],record,validate,{'task_kind':'extract'},retries=1)
        self.assertEqual(c.calls,2);self.assertTrue(load(record)['attempts'][0]['reference_lookup']['hits'])
    def test_enrichment_is_cached_and_quotes_are_grounded(self):
        class Fake:
            config={};identity={'model':'test'};calls=0
            def require_structured(self):pass
            def log(self,*a):pass
            def request(inner,messages,**kw):
                inner.calls+=1;p=json.loads(messages[-1]['content'])
                answer={'chunk_id':p['chunk_id'],'summary':'样本内说明','topics':['考核形式'],'field_notes':[{'field':'考核形式','explanation':'本样本说明','evidence_ids':[p['evidence_fragments'][0]['id']]}]}
                return {'text':json.dumps(answer),'schema_enforced':True,'schema_sha256':signature(kw['output_schema']),'schema_valid':True}
        client=Fake();enrich(self.db,client,self.root/'records',log=lambda s:None)
        count=client.calls;enrich(self.db,client,self.root/'records',log=lambda s:None)
        self.assertEqual(client.calls,count);self.assertEqual(status_report(self.db)['model_enriched'],2)
        with connect(self.db,True) as conn:self.assertIsNotNone(conn.execute('SELECT model_note FROM chunks LIMIT 1').fetchone()[0])
    def test_short_paragraphs_combined_with_original_locators(self):
        units=[{'kind':'paragraph','locator':f'body:{i}','text':s,'section':'1','ocr':False,'layout':{}} for i,s in enumerate(['课程介绍','课程名称和性质'],1)]
        result=pack_paragraphs(units);self.assertEqual(len(result),1)
        self.assertEqual(len(result[0]['layout']['members']),2);self.assertIn('body:2',result[0]['locator'])
    def test_reference_ids_cannot_become_current_course_evidence(self):
        from report_agent.reference_db import query_reference_library
        from report_agent.structured import extraction_schema,validate_schema
        result=query_reference_library('考核形式','course',2,db=self.db)
        self.assertTrue(result['hits'])
        answer={'records':[{'kind':'metadata','value':{'course_name':'其他课程'},'evidence_ids':[result['hits'][0]['reference_id']]}]}
        with self.assertRaises(ValueError):validate_schema(answer,extraction_schema([{'id':'current:1'}],['metadata']))
        with self.assertRaises(ValueError):query_reference_library('考核形式','course',100,db=self.db)

if __name__=='__main__':unittest.main()
