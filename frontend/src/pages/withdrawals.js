import {state} from '../stores/session.js';import {esc} from '../utils/html.js';import {table,tableHtml} from '../components/common/table.js';import {field,select} from '../components/common/fields.js';import {badge} from '../components/common/status.js';import {BRANCHES,WITHDRAWAL_STATES,ITEM_KINDS,ITEM_STATES} from '../constants/enums.js';
const key=()=>state.withdrawKey||(state.withdrawKey=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random().toString(16).slice(2)));
const fmtAt=s=>s?esc(s.slice(0,19).replace('T',' ')):'—';
function previewBlock(){
 let p=state.withdrawPreview;if(!p)return '<p class="notice">先选择网点与日期区间，预览将被清除的日量、预测快照、结果文件和排班。撤回只影响该网点该区间，其他网点不受影响。</p>';
 return `<div class="impact"><h3>影响预览 / ${esc(p.branch)} ${esc(p.day_start)} 至 ${esc(p.day_end)}</h3>
 <div class="metrics"><div><strong>${p.volumes.count}</strong>命中日量<p>${esc((p.volumes.days||[]).join('、')||'区间内无日量')}</p></div><div><strong>${p.runs.length}</strong>预测快照<p>含冻结副本，将同时清除计算结果</p></div><div><strong>${p.artifacts.count}</strong>结果文件<p>${esc((p.artifacts.run_ids||[]).map(x=>'#'+x).join(' ')||'共享卷上无文件')}</p></div><div><strong>${p.staffings.count}</strong>排班建议<p>待签核 ${p.staffings.draft.length} · 已签核 ${p.staffings.approved.length}</p></div></div>
 ${p.runs.length?`<h4>命中的预测</h4>${table(['版本','名称','状态','命中日期','结果文件'],p.runs.map(r=>['#'+r.id,r.name,r.state,(r.matched_days||[]).join('、')||'—',r.artifact_exists?'有（将删除）':'无']))}`:''}
 <p class="notice">未签核排班将作废；已签核排班仅保留日期与人数，并标明“依据撤回”。撤回受理后并发预测与旧计算进程不能再写回被撤数据。</p>
 ${p.active_withdrawal?`<p class="error">该网点已有进行中的撤回 #${p.active_withdrawal.id}（${esc(WITHDRAWAL_STATES[p.active_withdrawal.status]||p.active_withdrawal.status)}），需先完成。</p>`:''}</div>`;
}
function detailBlock(){
 let d=state.withdrawDetail;if(!d)return '';
 let items=d.items||[],residue=d.residue||{};
 let residueCount=Object.values(residue).reduce((n,a)=>n+(a||[]).length,0);
 return `<div class="impact"><h3>撤回 #${d.id} ${badge(WITHDRAWAL_STATES[d.status]||d.status)}</h3>
 <p>${esc(d.branch)} · ${esc(d.day_start)} 至 ${esc(d.day_end)} · 发起人 ${esc(d.actor)} · ${fmtAt(d.created)}</p><p>${esc(d.reason)}</p>
 <p class="${residueCount?'error':'notice'}">${d.status==='completed'?'逐项对账确认：日量、预测快照、计算结果、结果文件与排班均无残留。':residueCount?'仍有残留，保持待清理状态，可重试。':'清理进行中，请刷新。'}</p>
 ${residueCount?`<h4>残留明细</h4><ul>${Object.entries(residue).filter(([,v])=>v&&v.length).map(([k,v])=>`<li>${esc(ITEM_KINDS[k.replace(/s$/,'')]||k)}：${esc(v.join('、'))}</li>`).join('')}</ul>`:''}
 <h4>逐项对账</h4>${tableHtml(['类别','引用','状态','说明'],items.map(i=>[esc(ITEM_KINDS[i.kind]||i.kind),'#'+esc(i.ref_id),esc(ITEM_STATES[i.state]||i.state),esc(i.detail||'—')]))}
 <h4>撤回处理轨迹</h4>${table(['状态','时间','说明'],(d.history||[]).map(h=>[WITHDRAWAL_STATES[h.status]||h.status,fmtAt(h.at),h.note||'—']))}
 ${d.status==='pending_cleanup'?`<button class="primary" data-retry="${d.id}">重试清理（不重复创建）</button>`:''}
 <button data-residue="${d.id}">刷新对账</button></div>`;
}
export function render(){return `<div class="split"><section class="panel"><h2>按网点与日期区间撤回</h2><p>用于演练日结误入正式预测：清除命中日量，以及引用它的预测快照、计算结果和结果文件，并逐项对账。撤回只影响所选网点与日期区间；其他网点不受影响。撤回处理中该网点不能补录日量、提交预测或生成/签核排班。</p><h3>撤回记录</h3>${state.withdrawals.length?tableHtml(['编号','网点','日期区间','状态','对账','发起','操作'],state.withdrawals.map(w=>[esc('#'+w.id),esc(w.branch),esc(w.day_start+' ~ '+w.day_end),esc(WITHDRAWAL_STATES[w.status]||w.status),esc((w.stats&&w.stats.reconciled)?'无残留':'有残留/处理中'),fmtAt(w.created),`<button data-open="${w.id}">查看</button>`])):'<p>暂无撤回记录。</p>'}${detailBlock()}</section><aside class="side"><h2>发起撤回</h2><form id="withdraw-preview">${select('网点','branch',BRANCHES.map(b=>[b,b]))}${field('开始日期','day_start','date','required value="2026-11-10"')}${field('结束日期','day_end','date','required value="2026-11-14"')}<button>预览影响</button></form>${previewBlock()}${state.withdrawPreview?`<form id="withdraw-confirm">${field('撤回原因（至少4字）','reason','text','required minlength=4 value="演练日结误入正式预测"')}<button class="primary">确认撤回</button></form>`:''}</aside></div>`}
export {key};
