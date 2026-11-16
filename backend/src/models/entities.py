from sqlalchemy import Column,Integer,String,Float,JSON,Text,ForeignKey,UniqueConstraint
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True);password=Column(String(200));role=Column(String(20))
class DailyVolume(Base):
 __tablename__='daily_volumes';__table_args__=(UniqueConstraint('branch','day',name='uq_branch_day'),)
 id=Column(Integer,primary_key=True);branch=Column(String(30),nullable=False);day=Column(String(10),nullable=False);volume=Column(Integer,nullable=False);note=Column(Text,default='')
class ForecastRun(Base):
 __tablename__='forecast_runs'
 id=Column(Integer,primary_key=True);name=Column(String(100));branch=Column(String(30));backtest_days=Column(Integer);alpha=Column(Float);beta=Column(Float);state=Column(String(20),default='queued');result=Column(JSON);error=Column(Text,default='');submitted_at=Column(String(40))
class Staffing(Base):
 __tablename__='staffings'
 id=Column(Integer,primary_key=True);run_id=Column(Integer,ForeignKey('forecast_runs.id'));capacity=Column(Integer);model=Column(String(30));schedule=Column(JSON);note=Column(Text);status=Column(String(20),default='draft');branch=Column(String(30));withdrawal_id=Column(Integer,ForeignKey('withdrawals.id'));withdrawn_run_id=Column(Integer);withdrawn_at=Column(String(40))
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
class Withdrawal(Base):
 __tablename__='withdrawals'
 id=Column(Integer,primary_key=True);branch=Column(String(30),nullable=False);day_start=Column(String(10),nullable=False);day_end=Column(String(10),nullable=False);status=Column(String(20),default='pending');reason=Column(Text,default='');actor=Column(String(100),default='');request_key=Column(String(64),unique=True);stats=Column(JSON,default=dict);last_error=Column(Text,default='');created=Column(String(40));updated=Column(String(40));completed_at=Column(String(40))
class WithdrawalItem(Base):
 __tablename__='withdrawal_items';__table_args__=(UniqueConstraint('withdrawal_id','kind','ref_id',name='uq_withdrawal_item'),)
 id=Column(Integer,primary_key=True);withdrawal_id=Column(Integer,ForeignKey('withdrawals.id'),nullable=False);kind=Column(String(20),nullable=False);ref_id=Column(String(64),nullable=False);branch=Column(String(30));day=Column(String(10));state=Column(String(20),default='pending');detail=Column(Text,default='')
