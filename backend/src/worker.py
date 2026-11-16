import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session, engine, Base
from src.models.entities import ForecastRun
from src.services.analysis import forecast
from src.services import withdrawals as wd
from src.services.guards import install
r=redis.Redis.from_url(os.environ['REDIS_URL'])

def _branch_lock(s, branch):
    wd.lock_branch(s, branch)

def _cancelled(row):
    return row is None or row.state=='cancelled' or (isinstance(row.result,dict) and bool(row.result.get('withdrawn')))

def process(run_id):
    with Session() as s:
        row=s.get(ForecastRun,run_id)
        if not row or row.state!='queued':return
        _branch_lock(s,row.branch)
        s.refresh(row)
        if _cancelled(row) or row.state!='queued':return
        conflict=wd.run_conflict(s,row)
        if conflict:
            row.state='cancelled';row.result={'withdrawn':True,'withdrawal_id':conflict.id}
            row.error=f'受理日量撤回 #{conflict.id} 时该预测尚未计算，已取消'
            s.commit();return
        snapshot=row.result['snapshot'];branch=row.branch;backtest=row.backtest_days;alpha=row.alpha;beta=row.beta
        row.state='running';s.commit()
    try:
        result=forecast(snapshot,backtest,alpha,beta)
    except Exception as e:
        with Session() as s:
            row=s.get(ForecastRun,run_id)
            if not row:return
            _branch_lock(s,branch)
            s.refresh(row)
            if _cancelled(row):return
            row.state='failed';row.error=str(e);s.commit()
        return
    # 结果写回、结果文件写入与撤回受理共用网点锁：受理后旧进程不能写回快照、结果或文件
    with Session() as s:
        row=s.get(ForecastRun,run_id)
        if not row:return
        _branch_lock(s,branch)
        s.refresh(row)
        if _cancelled(row):return
        conflict=wd.run_conflict(s,row)
        if conflict:
            row.state='cancelled';row.result={'withdrawn':True,'withdrawal_id':conflict.id}
            row.error=f'计算完成前日量被撤回 #{conflict.id}，结果不保存'
            s.commit();return
        row.result={**result,'snapshot':snapshot};row.state='done'
        path=Path(os.getenv('ARTIFACTS_DIR','/artifacts')).joinpath(f'{run_id}.json')
        try:
            path.write_text(json.dumps(row.result,ensure_ascii=False))
        except OSError:
            logging.exception('artifact write failed %s',run_id)
        s.commit()
        # 文件在持锁事务内写完再提交：撤回清理拿不到锁，不会与本文件的生成交错

install(engine)
Base.metadata.create_all(engine)
with Session() as recovery:
 for pending in recovery.query(ForecastRun).filter(ForecastRun.state.in_(['queued','running'])).all():
  pending.state='queued';r.rpush('demand:runs',pending.id)
 recovery.commit()
try:
 wd.recover_interrupted()
except Exception:logging.exception('withdrawal recovery')
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=r.blpop('demand:runs',timeout=3)
  if not item:
   try:wd.recover_stale()
   except Exception:logging.exception('stale withdrawal sweep')
   continue
  process(int(item[1]))
 except Exception:logging.exception('worker');time.sleep(2)
