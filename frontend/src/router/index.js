export const routes=['日量导入','回测设置','预测对比','排班建议','撤回与对账'];export const page=()=>{const n=Number(location.hash.slice(1)||0);return Number.isInteger(n)&&n>=0&&n<routes.length?n:0};
