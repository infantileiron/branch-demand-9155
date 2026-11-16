export const BRANCHES=['川北','川南','东河','西浦','新城','临港','山前','湖滨'];export const MODELS={seasonal_naive:'周季节朴素',holt:'Holt线性趋势'};export const STATES={queued:'排队中',running:'计算中',done:'已完成',failed:'失败',cancelled:'已撤回'};export const ROLES=['admin','analyst','viewer'];
export const WITHDRAWAL_STATES={pending:'已受理·待清理',cleaning:'清理中',pending_cleanup:'待清理（有残留）',completed:'清理完成'};
export const STAFFING_STATES={draft:'待签核',approved:'已签核',voided:'已作废',withdrawn:'依据撤回'};
export const ITEM_KINDS={volume:'日量',run:'预测快照',artifact:'结果文件',staffing:'排班'};
export const ITEM_STATES={pending:'待处理',deleted:'已清除',missing:'已不存在',voided:'已作废',reduced:'已收缩',failed:'仍残留'};
