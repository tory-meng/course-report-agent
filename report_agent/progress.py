"""基于已完成阶段/任务数的进度；不是耗时预测。"""
from datetime import datetime, timezone
import time
from .common import save

class Progress:
    def __init__(self, out):
        self.out = out; self.started = time.monotonic()
        self.state = {'status':'running', 'stage':0, 'stage_total':9, 'stage_name':'初始化', 'completed':0, 'total':0}
    def note(self, message):
        self.state.update(message=message, updated_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.monotonic()-self.started,1))
        save(self.out/'progress.json', self.state)
        line = f"[{self.state['updated_at'][11:19]}] {message}"
        print(line, flush=True)
        with (self.out/'progress.log').open('a',encoding='utf-8') as f: f.write(line+'\n')
    def stage(self, number, name):
        self.state.update(stage=number, stage_name=name, completed=0, total=0, task_id=None)
        self.note(f'阶段 {number}/9：{name}')
    def units(self, done, total, label):
        self.state.update(completed=done, total=total)
        filled = int(20*done/total) if total else 0
        self.note('['+'█'*filled+'░'*(20-filled)+f'] {done}/{total} '+label)
    def finish(self, status, message):
        self.state['status'] = status; self.note(message)
