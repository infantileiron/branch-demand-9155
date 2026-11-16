import {esc} from '../../utils/html.js';export const badge=s=>`<span class=tag>${esc(s)}</span>`;export const stateBadge=(map,s)=>badge(map[s]||s);
