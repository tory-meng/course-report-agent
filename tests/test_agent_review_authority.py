import copy
from pathlib import Path
import tempfile
import unittest
from report_agent.common import save,digest,signature
from training.data import validate_row
from training.review_authority import payload_fingerprint,AGENT_REVIEW


class AgentReviewAuthorityTests(unittest.TestCase):
    def fixture(self,p):
        row={'id':'test','group_id':'course','messages':[{'role':'user','content':'核对原文后输出字段。'},
            {'role':'assistant','content':'{"value":"来源字段"}'}]}
        report=p/'independent.json';save(report,{'rows':[{'id':'test','verdict':'pass'}]})
        authorization=p/'authorization.json';save(authorization,{
            'kind':'explicit_user_authorization_for_agent_review_and_training','user_request':'核对样本，并微调',
            'actions':['sample_review','finetune'],'preparation_fingerprint':'fixture-version'})
        audit=p/'audit.json';save(audit,{'authorization_sha256':digest(authorization),'preparation_fingerprint':'fixture-version',
            'rows':[{'id':'test','verdict':'accepted_for_this_training','payload_sha256':payload_fingerprint(row),
                'reviewer':'codex-agent','source_checked':True,'answer_isolation_checked':True,
                'source_review_reports':[{'file':str(report),'sha256':digest(report)}]}]})
        row['review']={'approved':True,'reviewer':'codex-agent','kind':AGENT_REVIEW,'answer_isolation_checked':True,
            'authorization_file':str(authorization),'authorization_sha256':digest(authorization),
            'audit_file':str(audit),'audit_sha256':digest(audit)}
        return row

    def test_explicit_agent_review_keeps_its_type_and_bindings(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.fixture(Path(d));self.assertEqual(validate_row(row)['review']['kind'],AGENT_REVIEW)

    def test_mutation_after_review_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.fixture(Path(d));row['messages'][-1]['content']='{"value":"改动后字段"}'
            with self.assertRaisesRegex(ValueError,'已经改变'):validate_row(row)

    def test_changed_or_missing_authorization_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.fixture(Path(d));Path(row['review']['authorization_file']).unlink()
            with self.assertRaisesRegex(ValueError,'authorization'):validate_row(row)

    def test_agent_review_cannot_replace_existing_human_review_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.fixture(Path(d));row['review']['kind']='unknown'
            with self.assertRaisesRegex(ValueError,'未知'):validate_row(row)
            row['review']={'approved':False,'reviewer':'','answer_isolation_checked':False}
            with self.assertRaisesRegex(ValueError,'未经完整人工审核'):validate_row(row)

    def test_changed_independent_review_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.fixture(Path(d));(Path(d)/'independent.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'独立来源'):validate_row(row)


if __name__=='__main__':unittest.main()
