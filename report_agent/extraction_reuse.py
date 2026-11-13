"""跨代码修复复用：只迁移身份、请求及schema匹配且通过当前校验的抽取/辅助记录。"""
import copy
from pathlib import Path
from .common import load, save, digest, signature


class ExtractionReuse:
    def __init__(self, source, manifest, out):
        self.source=Path(source).resolve();self.out=Path(out)
        if self.source==self.out.resolve():raise ValueError('复用源与新输出目录必须不同')
        old=load(self.source/'run_manifest.json')
        keys=('case_id','group_id','inputs','template_id','template_hashes','model','reference_library','runtime_config')
        for key in keys:
            if old.get(key)!=manifest.get(key):raise ValueError('旧抽取不可复用：'+key+'不一致')
        if old.get('offline') is not False or manifest.get('offline') is not False:
            raise ValueError('抽取复用仅支持真实在线任务')
        if not old.get('inputs') or not old.get('model') or not old.get('template_hashes'):
            raise ValueError('旧任务缺少输入/模型/模板指纹，不能验证复用来源')
        self.rows={};self.audit={'source_run':str(self.source),'reused':[],'rejected':[]}
        audit_file=self.out/'extraction_reuse.json'
        if audit_file.is_file():
            previous=load(audit_file)
            if previous.get('source_run')==str(self.source):self.audit=previous
        for file in sorted((self.source/'records').glob('*.json')):
            row=load(file)
            if row.get('task_kind') not in ('extract','write_section','teaching_design','evaluation_design','improvement_design') or row.get('ok') is not True:continue
            if not row.get('messages') or not row.get('output_schema') or not row.get('attempts'):continue
            expected=signature({'messages':row['messages'],'output_schema':row['output_schema']})
            if expected!=row.get('request_hash'):continue
            if not any(a.get('raw') and a.get('error') is None for a in row['attempts']):continue
            self.rows.setdefault(expected,[]).append((file,row))
        self._save()

    def _save(self):save(self.out/'extraction_reuse.json',self.audit)

    def take(self, request_hash, metadata, validate, target):
        from .structured import validate_schema
        for file,original in self.rows.get(request_hash,[]):
            if any(original.get(k)!=metadata.get(k) for k in ('task_kind','case_id','group_id','template_id')):continue
            row=copy.deepcopy(original)
            try:
                validate_schema(row['answer'],metadata['output_schema'])
                validate(row['answer'])
            except (ValueError,TypeError,KeyError) as exc:
                self.audit['rejected'].append({'record':file.name,'reason':str(exc)});self._save();continue
            row['review']={'approved':False,'reviewer':'','answer_isolation_checked':False}
            row['reused_from']={'record':str(file),'sha256':digest(file),'validation':'current_schema_and_content'}
            # 保留原始消息、实际请求和生成记录，不伪装为本次推理。
            save(target,row)
            self.audit['reused'].append({'record':Path(target).name,'source':file.name,'task_kind':row['task_kind']})
            self._save()
            return row['answer']
        return None
