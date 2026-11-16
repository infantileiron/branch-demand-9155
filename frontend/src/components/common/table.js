import {esc} from '../../utils/html.js';
export function table(headers,rows){return tableHtml(headers,rows.map(r=>r.map(v=>esc(v))))}
export function tableHtml(headers,rows){return `<div class=scroll><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')||'<tr><td>暂无记录。请在右侧录入首条数据。</td></tr>'}</tbody></table></div>`}
