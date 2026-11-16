import os,json
import redis
from fastapi import APIRouter,BackgroundTasks,Depends,HTTPException,Response
from src.config.database import db
from src.models.entities import User,DailyVolume,ForecastRun,Staffing,Audit
from src.schemas.business import Login,DailyInput,ForecastInput,StaffingInput,WithdrawalPreview,WithdrawalInput
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.volumes import save_volume
from src.services.staffing import draft
from src.services import withdrawals as wd
from src.controllers.business import ensure_run
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/volumes')
def volumes(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(DailyVolume).order_by(DailyVolume.day,DailyVolume.branch).all()]
@router.post('/volumes')
def create(data:DailyInput,u=Depends(editor),s=Depends(db)):
 wd.lock_branch(s,data.branch);wd.ensure_branch_writable(s,data.branch,'补录或修改日量')
 r=save_volume(s,data.model_dump(mode='json'));audit(s,u,'录入日量 '+str(r.id));return dump(r)
@router.put('/volumes/{i}')
def edit(i:int,data:DailyInput,u=Depends(editor),s=Depends(db)):
 wd.lock_branch(s,data.branch);wd.ensure_branch_writable(s,data.branch,'补录或修改日量')
 r=save_volume(s,data.model_dump(mode='json'),i);audit(s,u,'修订日量 '+str(r.id));return dump(r)
@router.get('/runs')
def runs(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(ForecastRun).order_by(ForecastRun.id.desc()).all()]
@router.post('/runs')
def run(data:ForecastInput,u=Depends(editor),s=Depends(db)):
 wd.lock_branch(s,data.branch);wd.ensure_branch_writable(s,data.branch,'提交预测')
 rows=[dump(r) for r in s.query(DailyVolume).filter_by(branch=data.branch).order_by(DailyVolume.day).all()]
 r=ForecastRun(**data.model_dump(),result={'snapshot':rows},submitted_at=wd.now());s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('demand:runs',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请恢复后重试';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交预测 '+str(r.id));return dump(r)
@router.get('/runs/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_run(s,i))
@router.get('/runs/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_run(s,i)
 wd.ensure_run_usable(s,r,'导出')
 if r.state!='done':raise HTTPException(409,'仅成功预测可导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="branch-demand-{i}.json"'})
@router.get('/staffings')
def staffings(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Staffing).order_by(Staffing.id.desc()).all()]
@router.post('/staffings')
def staffing(data:StaffingInput,u=Depends(editor),s=Depends(db)):
 run=ensure_run(s,data.run_id)
 wd.lock_branch(s,run.branch);wd.ensure_branch_writable(s,run.branch,'生成排班建议');wd.ensure_run_usable(s,run,'生成排班建议')
 r=draft(s,run,data);audit(s,u,'起草排班 '+str(r.id));return dump(r)
@router.post('/staffings/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=s.get(Staffing,i)
 if not r:raise HTTPException(404,'排班建议不存在')
 s.refresh(r)
 if r.withdrawal_id is not None or r.status in ('withdrawn','voided'):
  raise HTTPException(409,'该排班依据的预测已被撤回，不能签核')
 if r.status!='draft':raise HTTPException(409,'不可重复签核')
 run=s.get(ForecastRun,r.run_id)
 if not run:raise HTTPException(409,'排班引用的预测已不存在，不能签核')
 wd.lock_branch(s,run.branch)
 s.refresh(r)
 if r.status!='draft' or r.withdrawal_id is not None:raise HTTPException(409,'不可重复签核')
 wd.ensure_branch_writable(s,run.branch,'签核排班');wd.ensure_run_usable(s,run,'签核排班')
 r.status='approved';s.commit();audit(s,u,'签核排班 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]
@router.post('/withdrawals/preview')
def preview(data:WithdrawalPreview,u=Depends(editor),s=Depends(db)):
 return wd.preview(s,data.branch,data.day_start.isoformat(),data.day_end.isoformat())
@router.get('/withdrawals')
def withdrawal_list(u=Depends(current),s=Depends(db)):
 return [wd.dump_row(r) for r in s.query(wd.Withdrawal).order_by(wd.Withdrawal.id.desc()).limit(100)]
@router.post('/withdrawals',status_code=201)
def withdrawal_create(data:WithdrawalInput,background:BackgroundTasks,response:Response,u=Depends(editor),s=Depends(db)):
 w,created=wd.create_withdrawal(s,u,data)
 if created:background.add_task(wd.run_cleanup,w.id)
 else:response.status_code=200
 detail=wd._detail(s,s.get(wd.Withdrawal,w.id))
 detail['created']=created
 return detail
@router.get('/withdrawals/{i}')
def withdrawal_detail(i:int,u=Depends(current),s=Depends(db)):
 w=s.get(wd.Withdrawal,i)
 if not w:raise HTTPException(404,'撤回记录不存在')
 s.refresh(w)
 return wd._detail(s,w)
@router.get('/withdrawals/{i}/residue')
def withdrawal_residue(i:int,u=Depends(current),s=Depends(db)):
 d=wd.read_reconcile(i)
 if not d:raise HTTPException(404,'撤回记录不存在')
 return d
@router.post('/withdrawals/{i}/retry',status_code=202)
def withdrawal_retry(i:int,background:BackgroundTasks,u=Depends(editor),s=Depends(db)):
 w=s.get(wd.Withdrawal,i)
 if not w:raise HTTPException(404,'撤回记录不存在')
 s.refresh(w)
 if w.status=='completed':raise HTTPException(409,'该撤回已清理完成，无需重试')
 background.add_task(wd.retry_cleanup,i)
 return wd._detail(s,w)
