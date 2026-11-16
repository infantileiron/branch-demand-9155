/** @typedef {{id:number,branch:string,day:string,volume:number,note:string}} DailyVolume */
/** @typedef {{id:number,name:string,branch:string,backtest_days:number,alpha:number,beta:number,state:string,result:object,error:string}} ForecastRun */
/** @typedef {{id:number,run_id:number|null,capacity:number,model:string,schedule:object[],note:string,status:'draft'|'approved'|'voided'|'withdrawn',branch:string,withdrawal_id:number|null,withdrawn_run_id:number|null}} Staffing */
/** @typedef {{id:number,branch:string,day_start:string,day_end:string,status:'pending'|'cleaning'|'pending_cleanup'|'completed',reason:string,actor:string,last_error:string,stats:object}} Withdrawal */
/** @typedef {{id:number,withdrawal_id:number,kind:'volume'|'run'|'artifact'|'staffing',ref_id:string,branch:string,day:string|null,state:string,detail:string}} WithdrawalItem */
