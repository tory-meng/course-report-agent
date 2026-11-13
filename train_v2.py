#!/usr/bin/env python3
"""审核、导出、4-bit QLoRA新建/继续训练及同输入生成评测；不含从零预训练。"""
import argparse
from report_agent.common import path

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('approve');q.add_argument('--record',required=True);q.add_argument('--answer-file');q.add_argument('--reviewer',required=True);q.add_argument('--answer-isolation-checked',action='store_true')
    q=sub.add_parser('export');q.add_argument('--runs',nargs='+',required=True);q.add_argument('--out',required=True);q.add_argument('--replay');q.add_argument('--seed',type=int,default=42)
    q.add_argument('--validation-group',action='append',help='固定留出课程group_id，可重复；不会把新课程随机分进训练集')
    q=sub.add_parser('review');q.add_argument('--run',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('prepare',help='整理来源绑定候选和课程划分、检查长度；不批准、不训练');q.add_argument('--config',required=True);q.add_argument('--out',required=True)
    q.add_argument('--tokenizer-python',help='在独立训练环境执行长度检查，避免混用两个环境依赖')
    q=sub.add_parser('export-prepared',help='逐条审核后导出准备候选；审批/哈希/来源/划分均核验');q.add_argument('--preparation',required=True);q.add_argument('--decisions',required=True);q.add_argument('--out',required=True)
    q=sub.add_parser('train');q.add_argument('--config',default='configs/training_continue.json');q.add_argument('--dataset',required=True);q.add_argument('--out',required=True)
    q.add_argument('--stage',choices=['smoke','formal'],required=True);q.add_argument('--prepare-only',action='store_true');q.add_argument('--resume')
    q=sub.add_parser('evaluate');q.add_argument('--runtime',default='configs/runtime.json');q.add_argument('--data',required=True);q.add_argument('--out',required=True)
    q.add_argument('--task-id',action='append',help='只评测指定ID；可重复，供训练前固定小基线')
    a=p.parse_args()
    if a.command=='export-prepared':
        from training.preparation import export_prepared
        print(export_prepared(a.preparation,a.decisions,a.out))
    elif a.command=='prepare':
        from training.preparation import prepare
        result=prepare(a.config,a.out,tokenizer_python=a.tokenizer_python)
        print({k:result[k] for k in ('status','candidate_rows','train_candidate_rows','validation_candidate_rows','max_tokens','training_started')})
    elif a.command=='review':
        from training.data import review_queue
        result=review_queue(path(a.run),path(a.out));print('待审核记录：',result['count'],'；清单：',path(a.out)/'review_queue.json')
    elif a.command=='approve':
        from training.data import approve
        approve(path(a.record),path(a.answer_file) if a.answer_file else None,a.reviewer,a.answer_isolation_checked)
    elif a.command=='export':
        from training.data import export
        print(export([path(r) for r in a.runs],path(a.out),path(a.replay) if a.replay else None,a.seed,a.validation_group))
    elif a.command=='train':
        from training.train import train
        train(a.config,a.dataset,a.out,a.stage,a.prepare_only,a.resume)
    else:
        from training.evaluate import evaluate
        print(evaluate(a.runtime,a.data,a.out,a.task_id))

if __name__=='__main__':main()
