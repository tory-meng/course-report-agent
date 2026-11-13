"""明确区分用户逐条审核与用户授权的代理逐条核对；原审批记录保持原样。"""
from pathlib import Path
from report_agent.common import digest,load,signature

AGENT_REVIEW='user_authorized_agent_review'

def payload_fingerprint(row):
    return signature({k:v for k,v in row.items() if k!='review'})

def validate_agent_review(row):
    review=row['review']
    if review.get('kind')!=AGENT_REVIEW:return
    for prefix in ('authorization','audit'):
        file=review.get(prefix+'_file','')
        if not file or not Path(file).is_absolute() or not Path(file).is_file():
            raise ValueError(row['id']+'缺少代理核对的'+prefix+'记录')
        if digest(file)!=review.get(prefix+'_sha256'):
            raise ValueError(row['id']+'代理核对记录哈希已改变：'+prefix)
    authorization=load(review['authorization_file']);audit=load(review['audit_file'])
    if authorization.get('kind')!='explicit_user_authorization_for_agent_review_and_training':
        raise ValueError('代理核对缺少本次用户授权类型')
    if not authorization.get('user_request') or not {'sample_review','finetune'}<=set(authorization.get('actions',[])):
        raise ValueError('代理核对授权范围不包括样本核对和微调')
    if audit.get('authorization_sha256')!=review['authorization_sha256']:
        raise ValueError('核对结论与授权记录不一致')
    if audit.get('preparation_fingerprint')!=authorization.get('preparation_fingerprint'):
        raise ValueError('核对结论与授权的数据版本不一致')
    matches=[r for r in audit.get('rows',[]) if r.get('id')==row['id']]
    if len(matches)!=1 or matches[0].get('verdict')!='accepted_for_this_training':
        raise ValueError(row['id']+'没有逐条核对通过的结论')
    entry=matches[0]
    if entry.get('payload_sha256')!=payload_fingerprint(row):raise ValueError(row['id']+'核对后输入或答案已经改变')
    if not entry.get('source_review_reports') or not entry.get('source_checked') or not entry.get('answer_isolation_checked'):
        raise ValueError(row['id']+'缺少来源核对或答案隔离结论')
    for report in entry['source_review_reports']:
        if not Path(report.get('file','')).is_file() or digest(report['file'])!=report.get('sha256'):
            raise ValueError(row['id']+'独立来源审阅记录已变化')
    if review.get('reviewer')!=entry.get('reviewer'):raise ValueError('核对者标识不一致')
