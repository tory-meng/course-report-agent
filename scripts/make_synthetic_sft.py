#!/usr/bin/env python3
"""生成无个人信息的SFT链路夹具；仅供smoke，不能用于正式训练或质量结论。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from report_agent.common import path, require_empty, save, signature, write_rows
from training.data import validate_row


def build(out):
    out = path(out)
    require_empty(out)
    groups = {'train': 'synthetic_course_alpha', 'validation': 'synthetic_course_beta'}
    data = {}
    for split, group in groups.items():
        rows = []
        for i in range(12 if split == 'train' else 4):
            sid = f'{group}:example_{i + 1:02d}'
            goal = f'课程目标{i % 3 + 1}'
            activity = f'合成练习{("甲" if split == "train" else "乙")}{i + 1}'
            prompt = {'task': 'synthetic_fixture_v1', 'instruction': '从给定课程活动中提取名称与关联课程目标；不要补充事实。',
                      'course': group, 'activity': {'name': activity, 'supports': goal}}
            answer = {'activity_name': activity, 'course_goal': goal}
            row = {'id': sid, 'group_id': group, 'case_id': group,
                   'task_kind': 'synthetic_fixture_v1', 'synthetic': True,
                   'messages': [
                       {'role': 'system', 'content': '这是公开代码的纯合成测试夹具。仅返回规定的JSON对象，不添加说明。'},
                       {'role': 'user', 'content': json.dumps(prompt, ensure_ascii=False)},
                       {'role': 'assistant', 'content': json.dumps(answer, ensure_ascii=False)}],
                   'output_schema': {'type': 'object', 'properties': {
                       'activity_name': {'type': 'string'}, 'course_goal': {'type': 'string'}},
                       'required': ['activity_name', 'course_goal'], 'additionalProperties': False},
                   'review': {'approved': True, 'kind': 'synthetic_fixture_review',
                              'reviewer': 'deterministic_synthetic_fixture_validator',
                              'answer_isolation_checked': True,
                              'scope': '机器校验的smoke夹具，不代表人工或用户授权代理审核，不授予正式训练资格'}}
            validate_row(row)
            rows.append(row)
        data[split] = rows
        write_rows(out / f'{split}.jsonl', rows)
    manifest = {'train_rows': len(data['train']), 'validation_rows': len(data['validation']),
                'train_groups': [groups['train']], 'validation_groups': [groups['validation']],
                'synthetic_only': True, 'seed': 42,
                'fingerprint': signature(data),
                'purpose': 'public_synthetic_smoke_fixture_not_real_course_evaluation',
                'formal_training_allowed': False}
    save(out / 'dataset_manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default='data/synthetic_smoke')
    a = p.parse_args()
    build(a.out)


if __name__ == '__main__':
    main()
