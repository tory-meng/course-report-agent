"""离线定位模型原始JSON错误；不调用模型、不修改记录。"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.client import parse_answer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',required=True)
    args=parser.parse_args()
    record=json.loads(Path(args.record).read_text(encoding='utf-8-sig'))
    print('任务：',args.record,'；已通过：',record.get('ok',False))
    for i,attempt in enumerate(record.get('attempts',[]),1):
        raw=attempt.get('raw') or {};text=raw.get('text','')
        print(f'\n尝试 {i}：回答 {len(text)} 字符，output_tokens={raw.get("output_tokens","未知")}，limit_reached={raw.get("limit_reached","未知")}')
        print('原错误：',attempt.get('error'))
        try:
            _,changes=parse_answer(text)
            print('当前解析器：JSON语法通过；格式清理=',changes,'（尚未重验内容和证据）')
        except json.JSONDecodeError as exc:
            print('当前解析器：',exc)
            print('错误附近（已去除外层围栏等格式）：',repr(exc.doc[max(0,exc.pos-100):exc.pos+100]))
        except ValueError as exc:print('当前解析器：',exc)

if __name__=='__main__':main()
