import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.config.database import Session, engine
from src.models.entities import Audit, DailyVolume, ForecastRun, Staffing, Withdrawal, WithdrawalItem

ACTIVE_STATUSES = ('pending', 'cleaning', 'pending_cleanup')
ITEM_KINDS = ('volume', 'run', 'artifact', 'staffing')
RESIDUE_KEYS = ('volumes', 'runs', 'artifacts', 'staffings')


def empty_residue():
    return {k: [] for k in RESIDUE_KEYS}


def now():
    return datetime.now(timezone.utc).isoformat()


def dump_row(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def _is_pg():
    return engine.dialect.name == 'postgresql'


def lock_branch(s, branch):
    """Serialize per-branch writes: withdrawal acceptance/cleanup, forecast submit, volume edit, approve."""
    if _is_pg():
        s.execute(text('SELECT pg_advisory_xact_lock(hashtext(:b))'), {'b': 'branch-demand:' + branch})


def active_withdrawals(s, branch):
    return s.query(Withdrawal).filter(Withdrawal.branch == branch, Withdrawal.status.in_(ACTIVE_STATUSES)).order_by(Withdrawal.id).all()


def ensure_branch_writable(s, branch, action='写入'):
    active = active_withdrawals(s, branch)
    if active:
        w = active[0]
        raise HTTPException(409, f'网点{branch}存在进行中的日量撤回（#{w.id}，{w.status}），暂不能{action}')


def snapshot_of(run):
    result = run.result if isinstance(run.result, dict) else None
    if not result:
        return None
    snap = result.get('snapshot')
    return snap if isinstance(snap, list) else None


def run_leaks(run):
    """True when the run still holds exportable forecast data (frozen snapshot or computed results)."""
    result = run.result if isinstance(run.result, dict) else None
    if not result:
        return False
    if result.get('withdrawn') is True:
        return bool(set(result) - {'withdrawn', 'withdrawal_id'})
    return bool(snapshot_of(run)) or any(k in result for k in ('metrics', 'future', 'backtest', 'history', 'n'))


def _snapshot_matches(run, branch, lo, hi):
    snap = snapshot_of(run)
    if snap is None:
        return run.branch == branch  # 冻结副本不可读时按同网点保守命中，避免漏清
    return any(isinstance(row, dict) and (row.get('branch') or run.branch) == branch and lo <= str(row.get('day') or '') <= hi for row in snap)


def matched_days(run, branch, lo, hi):
    days = []
    for row in snapshot_of(run) or []:
        if not isinstance(row, dict):
            continue
        if (row.get('branch') or run.branch) == branch and lo <= str(row.get('day') or '') <= hi:
            days.append(str(row.get('day')))
    return sorted(set(days))


def run_matches(run, branch, lo, hi, withdrawal_id=None):
    """True when the run still holds withdrawn days. A run already cleared by this same withdrawal
    also matches so cleanup can re-verify it."""
    result = run.result if isinstance(run.result, dict) else None
    if result and result.get('withdrawn') is True:
        if run_leaks(run):
            return run.branch == branch and _snapshot_matches(run, branch, lo, hi)
        return withdrawal_id is not None and result.get('withdrawal_id') == withdrawal_id
    if run.branch != branch:
        return False
    return _snapshot_matches(run, branch, lo, hi)


def run_conflict(s, run):
    """An active withdrawal that this run's frozen snapshot overlaps."""
    for w in active_withdrawals(s, run.branch):
        if run_matches(run, w.branch, w.day_start, w.day_end):
            return w
    return None


def ensure_run_usable(s, run, action='使用'):
    result = run.result if isinstance(run.result, dict) else None
    if result and result.get('withdrawn'):
        raise HTTPException(409, f'该预测依据的日量已被撤回（撤回 #{result.get("withdrawal_id")}），不能{action}')
    conflict = run_conflict(s, run)
    if conflict:
        raise HTTPException(409, f'该预测引用了撤回 #{conflict.id} 的日期区间，处理完成前不能{action}')


def artifacts_dir():
    return Path(os.getenv('ARTIFACTS_DIR', '/artifacts'))


def artifact_path(run_id):
    return artifacts_dir() / f'{run_id}.json'


def artifact_exists(run_id):
    try:
        return artifact_path(int(run_id)).is_file()
    except (OSError, ValueError):
        return False


def remove_artifact(run_id):
    path = artifact_path(run_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return True, ''
    except OSError as exc:
        return False, f'{path}: {exc}'
    try:
        if path.exists():
            return False, f'{path}: 删除后文件仍存在'
    except OSError as exc:
        return False, f'{path}: {exc}'
    return True, ''


def gather_impact(s, branch, lo, hi, withdrawal_id=None):
    volumes = s.query(DailyVolume).filter(DailyVolume.branch == branch, DailyVolume.day >= lo, DailyVolume.day <= hi).order_by(DailyVolume.day, DailyVolume.id).all()
    runs = [r for r in s.query(ForecastRun).order_by(ForecastRun.id).all() if run_matches(r, branch, lo, hi, withdrawal_id)]
    run_ids = [r.id for r in runs]
    staffings = s.query(Staffing).filter(Staffing.run_id.in_(run_ids)).order_by(Staffing.id).all() if run_ids else []
    return {'volumes': volumes, 'runs': runs, 'staffings': staffings}


def impact_summary(impact):
    return {
        'volumes': len(impact['volumes']),
        'volume_days': [v.day for v in impact['volumes']],
        'runs': len(impact['runs']),
        'run_ids': [r.id for r in impact['runs']],
        'artifacts': sum(1 for r in impact['runs'] if artifact_exists(r.id)),
        'staffings_draft': [st.id for st in impact['staffings'] if st.status == 'draft'],
        'staffings_approved': [st.id for st in impact['staffings'] if st.status == 'approved'],
        'staffings_other': [st.id for st in impact['staffings'] if st.status not in ('draft', 'approved')],
    }


def preview(s, branch, lo, hi):
    active = active_withdrawals(s, branch)
    impact = gather_impact(s, branch, lo, hi)
    return {
        'branch': branch,
        'day_start': lo,
        'day_end': hi,
        'volumes': {'count': len(impact['volumes']), 'ids': [v.id for v in impact['volumes']], 'days': [v.day for v in impact['volumes']]},
        'runs': [{'id': r.id, 'name': r.name, 'state': r.state, 'matched_days': matched_days(r, branch, lo, hi), 'artifact_exists': artifact_exists(r.id)} for r in impact['runs']],
        'staffings': {'count': len(impact['staffings']), 'draft': [st.id for st in impact['staffings'] if st.status == 'draft'], 'approved': [st.id for st in impact['staffings'] if st.status == 'approved'], 'other': [st.id for st in impact['staffings'] if st.status not in ('draft', 'approved')]},
        'artifacts': {'count': sum(1 for r in impact['runs'] if artifact_exists(r.id)), 'run_ids': [r.id for r in impact['runs'] if artifact_exists(r.id)]},
        'active_withdrawal': {'id': active[0].id, 'status': active[0].status} if active else None,
        'other_branches': '不受影响：仅清除本网点、本日期区间命中的记录与其引用链',
    }


def _stats(w):
    return dict(w.stats or {})


def _set_stats(w, **changes):
    stats = _stats(w)
    stats.update(changes)
    w.stats = stats
    return stats


def _history(w, status, note=''):
    stats = _stats(w)
    hist = list(stats.get('history') or [])
    hist.append({'status': status, 'at': now(), 'note': note})
    stats['history'] = hist
    w.stats = stats


def _add_item(s, w, kind, ref_id, branch=None, day=None, detail=''):
    ref_id = str(ref_id)
    item = s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind=kind, ref_id=ref_id).first()
    if item is None:
        item = WithdrawalItem(withdrawal_id=w.id, kind=kind, ref_id=ref_id, branch=branch or w.branch, day=day, detail=detail)
        s.add(item)
        s.flush()
    else:
        if branch:
            item.branch = branch
        if day:
            item.day = day
        if detail:
            item.detail = detail
    return item


def _ensure_items(s, w, impact):
    for v in impact['volumes']:
        _add_item(s, w, 'volume', v.id, branch=v.branch, day=v.day)
    for run in impact['runs']:
        days = matched_days(run, w.branch, w.day_start, w.day_end)
        _add_item(s, w, 'run', run.id, day=(days[0] if days else None), detail=f'{run.name}|{run.state}')
        _add_item(s, w, 'artifact', run.id)
    for st in impact['staffings']:
        _add_item(s, w, 'staffing', st.id, detail=st.status)


def create_withdrawal(s, actor, data):
    lo, hi = data.day_start.isoformat(), data.day_end.isoformat()
    existing = s.query(Withdrawal).filter_by(request_key=data.request_key).first()
    if existing:
        if existing.branch != data.branch or existing.day_start != lo or existing.day_end != hi:
            raise HTTPException(409, '该请求标识已用于另一组网点/区间撤回，请重新预览后提交')
        return existing, False
    lock_branch(s, data.branch)
    # 取得网点锁后重新查一次：并发的同一请求应返回已受理的那条，而不是被当作冲突或重复创建
    existing = s.query(Withdrawal).filter_by(request_key=data.request_key).first()
    if existing:
        return existing, False
    active = active_withdrawals(s, data.branch)
    if active:
        raise HTTPException(409, f'该网点已有进行中的撤回 #{active[0].id}（{active[0].status}），请刷新或在待清理时重试')
    w = Withdrawal(branch=data.branch, day_start=lo, day_end=hi, status='pending', reason=data.reason,
                   actor=actor.email, request_key=data.request_key, stats={}, last_error='', created=now(), updated=now())
    s.add(w)
    try:
        s.flush()
    except IntegrityError:
        # 并发重复提交同一请求：以先落库的那条为准，不重复创建
        s.rollback()
        existing = s.query(Withdrawal).filter_by(request_key=data.request_key).first()
        if existing:
            return existing, False
        raise HTTPException(409, '撤回请求冲突，请刷新后重试')
    impact = gather_impact(s, data.branch, lo, hi, w.id)
    _ensure_items(s, w, impact)
    _set_stats(w, impact=impact_summary(impact), residue={}, reconciled=False, items={})
    _history(w, 'pending', '已受理，冻结网点写入')
    for run in impact['runs']:
        # 受理即失效：立刻清掉快照与计算结果，旧进程提交时也会被驳回
        _clear_run(run, w.id)
    s.add(Audit(actor=actor.email, action=f'发起日量撤回 #{w.id} {data.branch} {lo}~{hi}', created=now()))
    s.commit()
    return w, True


def _mark_note(note, wid, label):
    base = (note or '').strip()
    tag = f'[{label} #{wid}]'
    return base if tag in base else (base + ' ' + tag).strip()


def _process_staffing(w, st, item):
    if st.status == 'draft':
        st.status = 'voided'
        st.schedule = []
        st.note = _mark_note(st.note, w.id, '未签核作废')
        st.withdrawal_id = w.id
        st.withdrawn_at = now()
        st.run_id = None
        item.state = 'voided'
        item.detail = '未签核排班已作废'
    elif st.status == 'voided':
        st.schedule = []
        st.withdrawal_id = st.withdrawal_id or w.id
        st.withdrawn_at = st.withdrawn_at or now()
        st.run_id = None
        item.state = 'voided'
        item.detail = '未签核排班已作废'
    elif st.status in ('approved', 'withdrawn'):
        if st.status == 'approved':
            st.withdrawn_run_id = st.run_id
            st.status = 'withdrawn'
            st.schedule = [{'day': p.get('day'), 'staff': p.get('staff')} for p in (st.schedule or []) if isinstance(p, dict)]
            st.note = _mark_note(st.note, w.id, '依据撤回')
        st.withdrawal_id = w.id
        st.withdrawn_at = st.withdrawn_at or now()
        st.run_id = None
        item.state = 'reduced'
        item.detail = '已签核排班仅保留日期与人数，注明依据撤回'
    else:
        item.state = 'failed'
        item.detail = f'未知排班状态 {st.status}'


def _process_staffings(s, w):
    seen = set()
    for item in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind='staffing').all():
        try:
            sid = int(item.ref_id)
        except ValueError:
            item.state = 'failed'
            item.detail = '无效排班引用'
            continue
        seen.add(sid)
        st = s.get(Staffing, sid)
        if not st:
            item.state = 'missing'
            item.detail = '排班记录不存在'
            continue
        _process_staffing(w, st, item)
    run_ids = [int(i.ref_id) for i in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind='run').all() if str(i.ref_id).isdigit()]
    if run_ids:
        for st in s.query(Staffing).filter(Staffing.run_id.in_(run_ids)).all():
            if st.id not in seen:
                item = _add_item(s, w, 'staffing', st.id, detail=st.status)
                _process_staffing(w, st, item)


def _delete_volumes(s, w):
    for item in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind='volume').all():
        try:
            vid = int(item.ref_id)
        except ValueError:
            item.state = 'failed'
            item.detail = '无效日量引用'
            continue
        row = s.get(DailyVolume, vid)
        if not row:
            item.state = 'missing'
            continue
        if row.branch != w.branch or not (w.day_start <= row.day <= w.day_end):
            item.state = 'failed'
            item.detail = '引用已越界，未删除'
            continue
        s.delete(row)
        item.state = 'deleted'
        item.detail = f'{row.branch} {row.day} 到达量 {row.volume}'
    # 兜底：区间内仍在库、但未进清单的日量（例如受理后由旧连接写入）
    for row in s.query(DailyVolume).filter(DailyVolume.branch == w.branch, DailyVolume.day >= w.day_start, DailyVolume.day <= w.day_end).all():
        item = _add_item(s, w, 'volume', row.id, branch=row.branch, day=row.day, detail=f'{row.branch} {row.day} 到达量 {row.volume}')
        item.state = 'deleted'
        s.delete(row)


def _clear_run(run, wid):
    run.state = 'cancelled'
    run.result = {'withdrawn': True, 'withdrawal_id': wid}
    run.error = f'依据的日量已被撤回 #{wid}，预测快照与计算结果已清除，不可导出或用于新排班'


def _clear_runs(s, w):
    """清除快照与计算结果；保留一行撤回标记用于审计，但不再含任何可导出的数据。"""
    for item in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind='run').all():
        try:
            rid = int(item.ref_id)
        except ValueError:
            item.state = 'failed'
            item.detail = '无效预测引用'
            continue
        s.query(Staffing).filter_by(run_id=rid).update({Staffing.run_id: None}, synchronize_session=False)
        row = s.get(ForecastRun, rid)
        if not row:
            item.state = 'missing'
            item.detail = '预测记录不存在'
            continue
        leftover = [k for k in ('snapshot', 'metrics', 'future', 'backtest', 'history') if isinstance(row.result, dict) and k in row.result]
        _clear_run(row, w.id)
        item.state = 'deleted'
        item.detail = ('已清除：' + '、'.join(leftover)) if leftover else '快照与计算结果已清除（保留撤回标记）'


def _artifact_items(s, w):
    return s.query(WithdrawalItem).filter_by(withdrawal_id=w.id, kind='artifact').all()


def _counts(s, w):
    counts = {}
    for item in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id).all():
        counts.setdefault(item.kind, {})
        counts[item.kind][item.state] = counts[item.kind].get(item.state, 0) + 1
    _set_stats(w, items=counts)


def _reconcile(s, w):
    """Re-read storage and decide, item by item, whether anything still leaks."""
    residue = empty_residue()
    items = s.query(WithdrawalItem).filter_by(withdrawal_id=w.id).all()
    run_ids = set()
    for item in items:
        if item.kind == 'volume':
            try:
                row = s.get(DailyVolume, int(item.ref_id))
            except ValueError:
                item.state = 'failed'
                residue['volumes'].append(item.ref_id)
                continue
            if row:
                item.state = 'failed'
                residue['volumes'].append(row.id)
            elif item.state not in ('deleted', 'missing'):
                item.state = 'missing'
        elif item.kind == 'run':
            try:
                rid = int(item.ref_id)
            except ValueError:
                item.state = 'failed'
                residue['runs'].append(item.ref_id)
                continue
            run_ids.add(rid)
            row = s.get(ForecastRun, rid)
            if row:
                result = row.result if isinstance(row.result, dict) else None
                cleared = bool(result) and result.get('withdrawn') is True and result.get('withdrawal_id') == w.id and not run_leaks(row)
                if cleared:
                    if item.state not in ('deleted', 'missing'):
                        item.state = 'deleted'
                else:
                    item.state = 'failed'
                    residue['runs'].append(rid)
                if artifact_exists(rid):
                    residue['artifacts'].append(str(artifact_path(rid)))
            elif item.state not in ('deleted', 'missing'):
                item.state = 'missing'
        elif item.kind == 'artifact':
            if artifact_exists(item.ref_id):
                item.state = 'failed'
                residue['artifacts'].append(str(artifact_path(item.ref_id)))
            elif item.state not in ('deleted', 'missing'):
                item.state = 'missing'
        elif item.kind == 'staffing':
            try:
                st = s.get(Staffing, int(item.ref_id))
            except ValueError:
                item.state = 'failed'
                residue['staffings'].append(item.ref_id)
                continue
            if st is None:
                if item.state not in ('voided', 'reduced', 'missing'):
                    item.state = 'missing'
            elif item.state == 'reduced':
                ok = st.status == 'withdrawn' and all(isinstance(p, dict) and not (set(p.keys()) - {'day', 'staff'}) for p in (st.schedule or []))
                if not ok:
                    item.state = 'failed'
                    residue['staffings'].append(st.id)
            elif item.state == 'voided':
                if st.status != 'voided' or st.schedule:
                    item.state = 'failed'
                    residue['staffings'].append(st.id)
            else:
                item.state = 'failed'
                residue['staffings'].append(st.id)
    # 区间内仍在库的日量（即使不在清单）
    for row in s.query(DailyVolume).filter(DailyVolume.branch == w.branch, DailyVolume.day >= w.day_start, DailyVolume.day <= w.day_end).all():
        if row.id not in residue['volumes']:
            residue['volumes'].append(row.id)
    # 任何仍能泄漏被撤日期的预测（含受理后新出现的），本撤回已清掉的除外
    for run in s.query(ForecastRun).order_by(ForecastRun.id).all():
        if not run_leaks(run):
            continue
        if run_matches(run, w.branch, w.day_start, w.day_end) and run.id not in residue['runs']:
            residue['runs'].append(run.id)
            if artifact_exists(run.id):
                residue['artifacts'].append(str(artifact_path(run.id)))
    # 仍挂在被撤销预测上的排班
    for st in s.query(Staffing).order_by(Staffing.id).all():
        if st.run_id in run_ids and st.id not in residue['staffings']:
            residue['staffings'].append(st.id)
    for key in residue:
        residue[key] = sorted(set(residue[key]), key=lambda x: str(x))
    return residue


def _residue_text(residue, failures=()):
    parts = []
    if residue['volumes']:
        parts.append(f'日量残留{len(residue["volumes"])}条')
    if residue['runs']:
        parts.append(f'预测残留{len(residue["runs"])}条')
    if residue['artifacts']:
        parts.append(f'结果文件残留{len(residue["artifacts"])}个')
    if residue['staffings']:
        parts.append(f'排班残留{len(residue["staffings"])}条')
    if failures:
        parts.append(f'文件清除失败{len(failures)}个')
    return '；'.join(parts)


def _detail(s, w):
    data = dump_row(w)
    stats = _stats(w)
    data['items'] = [dump_row(i) for i in s.query(WithdrawalItem).filter_by(withdrawal_id=w.id).order_by(WithdrawalItem.kind, WithdrawalItem.id).all()]
    data['impact'] = stats.get('impact', {})
    data['residue'] = stats.get('residue') or empty_residue()
    data['reconciled'] = stats.get('reconciled', False)
    data['history'] = stats.get('history', [])
    data['stats'] = stats
    return data


def _cleanup_locked(wid):
    with Session() as s:
        w = s.get(Withdrawal, wid)
        if not w or w.status == 'completed':
            return _detail(s, w) if w else None
        w.status = 'cleaning'
        w.updated = now()
        w.last_error = ''
        _history(w, 'cleaning', '开始清理')
        s.commit()
        branch, lo, hi = w.branch, w.day_start, w.day_end

    # 1) 再冻结一次：受理后新出现的命中预测也立即作废
    with Session() as s:
        lock_branch(s, branch)
        w = s.get(Withdrawal, wid)
        impact = gather_impact(s, branch, lo, hi, wid)
        _ensure_items(s, w, impact)
        for run in impact['runs']:
            if run_leaks(run):
                _clear_run(run, w.id)
        _counts(s, w)
        w.updated = now()
        s.commit()

    # 2) 作废/收缩排班，删除日量与预测（连同快照与计算结果）
    with Session() as s:
        lock_branch(s, branch)
        w = s.get(Withdrawal, wid)
        _process_staffings(s, w)
        _delete_volumes(s, w)
        _clear_runs(s, w)
        _counts(s, w)
        w.updated = now()
        s.commit()

    # 3) 删除共享卷上的结果文件；失败保持待清理
    failures = []
    with Session() as s:
        w = s.get(Withdrawal, wid)
        for item in _artifact_items(s, w):
            ok, err = remove_artifact(item.ref_id)
            if ok:
                item.state = 'deleted'
                item.detail = ''
            else:
                item.state = 'failed'
                item.detail = err
                failures.append(err)
        _counts(s, w)
        w.updated = now()
        s.commit()

    # 4) 逐项对账：任何残留或文件失败都转入待清理
    with Session() as s:
        lock_branch(s, branch)
        w = s.get(Withdrawal, wid)
        residue = _reconcile(s, w)
        clean = not any(residue.values())
        _set_stats(w, residue=residue, reconciled=clean, artifacts_failed=len(failures))
        _counts(s, w)
        w.updated = now()
        if clean:
            w.status = 'completed'
            w.completed_at = w.completed_at or now()
            w.last_error = ''
            _history(w, 'completed', '逐项对账无残留')
            s.add(Audit(actor=w.actor, action=f'日量撤回完成 #{w.id} {w.branch} {w.day_start}~{w.day_end}', created=now()))
        else:
            w.status = 'pending_cleanup'
            w.last_error = _residue_text(residue, failures) or '清理未完成'
            _history(w, 'pending_cleanup', w.last_error)
            s.add(Audit(actor=w.actor, action=f'日量撤回待清理 #{w.id}：{w.last_error}', created=now()))
        s.commit()
        return _detail(s, w)


def run_cleanup(wid):
    """One runner per withdrawal across processes; safe to call repeatedly."""
    if not _is_pg():
        return _cleanup_locked(wid)
    conn = engine.raw_connection()
    try:
        cur = conn.cursor()
        cur.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (f'branch-demand:withdrawal:{wid}',))
        acquired = cur.fetchone()[0]
        cur.close()
        if not acquired:
            conn.rollback()
            with Session() as s:
                w = s.get(Withdrawal, wid)
                return _detail(s, w) if w else None
        conn.commit()
        return _cleanup_locked(wid)
    finally:
        try:
            cur = conn.cursor()
            cur.execute('SELECT pg_advisory_unlock(hashtext(%s))', (f'branch-demand:withdrawal:{wid}',))
            conn.commit()
            cur.close()
        except Exception:
            logging.exception('unlock withdrawal %s', wid)
        conn.close()


def retry_cleanup(wid):
    return run_cleanup(wid)


def recover_interrupted():
    """On startup: resume withdrawals cut off mid-flight; failed file cleanup waits for an explicit retry."""
    with Session() as s:
        ids = [w.id for w in s.query(Withdrawal).filter(Withdrawal.status.in_(['pending', 'cleaning'])).order_by(Withdrawal.id).all()]
    return recover_ids(ids)


def recover_stale(older_than_seconds=90):
    """Periodic safety net: a cleaning run that died without finishing (e.g. API process killed)
    is resumed as the same withdrawal. Fresh in-flight runs are left alone."""
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    with Session() as s:
        ids = []
        for w in s.query(Withdrawal).filter(Withdrawal.status.in_(['pending', 'cleaning'])).all():
            try:
                updated = datetime.fromisoformat(w.updated or w.created or '')
            except ValueError:
                updated = None
            if updated is None or updated.timestamp() <= cutoff:
                ids.append(w.id)
    return recover_ids(ids)


def recover_ids(ids):
    resumed = []
    for wid in ids:
        try:
            detail = run_cleanup(wid)
            if detail:
                resumed.append({'id': wid, 'status': detail['status']})
        except Exception:
            logging.exception('withdrawal recovery failed %s', wid)
    return resumed


def read_reconcile(wid):
    """Read-only residue view."""
    with Session() as s:
        w = s.get(Withdrawal, wid)
        if not w:
            return None
        items = s.query(WithdrawalItem).filter_by(withdrawal_id=wid).all()
        residue = empty_residue()
        run_ids = set()
        for item in items:
            if item.kind == 'volume':
                try:
                    row = s.get(DailyVolume, int(item.ref_id))
                except ValueError:
                    row = None
                if row:
                    residue['volumes'].append(row.id)
            elif item.kind == 'run':
                try:
                    rid = int(item.ref_id)
                except ValueError:
                    continue
                run_ids.add(rid)
                row = s.get(ForecastRun, rid)
                if row and run_leaks(row):
                    residue['runs'].append(rid)
                if artifact_exists(rid):
                    residue['artifacts'].append(str(artifact_path(rid)))
            elif item.kind == 'artifact':
                if artifact_exists(item.ref_id):
                    residue['artifacts'].append(str(artifact_path(item.ref_id)))
            elif item.kind == 'staffing':
                try:
                    st = s.get(Staffing, int(item.ref_id))
                except ValueError:
                    st = None
                if st is None:
                    continue
                if st.run_id is not None:
                    residue['staffings'].append(st.id)
                elif st.status == 'withdrawn':
                    if [p for p in (st.schedule or []) if not isinstance(p, dict) or (set(p.keys()) - {'day', 'staff'})]:
                        residue['staffings'].append(st.id)
                elif st.status != 'voided':
                    residue['staffings'].append(st.id)
        for row in s.query(DailyVolume).filter(DailyVolume.branch == w.branch, DailyVolume.day >= w.day_start, DailyVolume.day <= w.day_end).all():
            residue['volumes'].append(row.id)
        for st in s.query(Staffing).order_by(Staffing.id).all():
            if st.run_id in run_ids:
                residue['staffings'].append(st.id)
        if w.status == 'completed':
            # 完成后允许重新补录正确值、基于新数据建立新预测；这些不是本次撤回的残留
            listed = {i.kind + ':' + str(i.ref_id) for i in items}
            residue['volumes'] = [v for v in residue['volumes'] if 'volume:%d' % v in listed]
            residue['staffings'] = [v for v in residue['staffings'] if 'staffing:%d' % v in listed]
        residue = {k: sorted(set(v), key=lambda x: str(x)) for k, v in residue.items()}
        return {'id': w.id, 'branch': w.branch, 'day_start': w.day_start, 'day_end': w.day_end, 'status': w.status,
                'residue': residue, 'reconciled': not any(residue.values()), 'last_error': w.last_error}
