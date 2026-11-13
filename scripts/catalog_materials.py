"""增量清点混合课程资料，输出成绩前置核验、课程清单与批次队列。"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.material_catalog import scan

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True);p.add_argument('--out',required=True);p.add_argument('--previous');p.add_argument('--cache');p.add_argument('--no-archives',action='store_true');a=p.parse_args()
    result=scan(a.root,a.out,a.previous,a.cache,not a.no_archives)
    print({'packages':result['package_count'],'files':result['file_count'],'status_counts':result['status_counts']})

if __name__=='__main__':main()
