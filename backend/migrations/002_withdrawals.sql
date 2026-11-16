--
-- 002: 日量撤回（撤回单 / 逐项对账清单 / 排班撤回标记 / 数据库写回防护）
-- ORM 会自动建表；本文件保留与 entities.py 等价的 DDL，供手工迁移与审计。
--

CREATE TABLE IF NOT EXISTS public.withdrawals (
    id integer NOT NULL,
    branch character varying(30) NOT NULL,
    day_start character varying(10) NOT NULL,
    day_end character varying(10) NOT NULL,
    status character varying(20),
    reason text,
    actor character varying(100),
    request_key character varying(64),
    stats json,
    last_error text,
    created character varying(40),
    updated character varying(40),
    completed_at character varying(40)
);
CREATE SEQUENCE IF NOT EXISTS public.withdrawals_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.withdrawals_id_seq OWNED BY public.withdrawals.id;
ALTER TABLE ONLY public.withdrawals ALTER COLUMN id SET DEFAULT nextval('public.withdrawals_id_seq'::regclass);
ALTER TABLE ONLY public.withdrawals ADD CONSTRAINT withdrawals_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.withdrawals ADD CONSTRAINT withdrawals_request_key_key UNIQUE (request_key);

CREATE TABLE IF NOT EXISTS public.withdrawal_items (
    id integer NOT NULL,
    withdrawal_id integer NOT NULL,
    kind character varying(20) NOT NULL,
    ref_id character varying(64) NOT NULL,
    branch character varying(30),
    day character varying(10),
    state character varying(20),
    detail text
);
CREATE SEQUENCE IF NOT EXISTS public.withdrawal_items_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE public.withdrawal_items_id_seq OWNED BY public.withdrawal_items.id;
ALTER TABLE ONLY public.withdrawal_items ALTER COLUMN id SET DEFAULT nextval('public.withdrawal_items_id_seq'::regclass);
ALTER TABLE ONLY public.withdrawal_items ADD CONSTRAINT withdrawal_items_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.withdrawal_items ADD CONSTRAINT uq_withdrawal_item UNIQUE (withdrawal_id, kind, ref_id);
ALTER TABLE ONLY public.withdrawal_items ADD CONSTRAINT withdrawal_items_withdrawal_id_fkey FOREIGN KEY (withdrawal_id) REFERENCES public.withdrawals(id);

ALTER TABLE public.staffings ADD COLUMN IF NOT EXISTS branch character varying(30);
ALTER TABLE public.staffings ADD COLUMN IF NOT EXISTS withdrawal_id integer;
ALTER TABLE public.staffings ADD COLUMN IF NOT EXISTS withdrawn_run_id integer;
ALTER TABLE public.staffings ADD COLUMN IF NOT EXISTS withdrawn_at character varying(40);
ALTER TABLE public.forecast_runs ADD COLUMN IF NOT EXISTS submitted_at character varying(40);

-- 受理后（pending/cleaning/pending_cleanup）数据库层拒绝旧进程写回快照与日量。
CREATE OR REPLACE FUNCTION withdraw_guard_forecast_runs() RETURNS trigger AS $$
BEGIN
  IF NEW.result IS NOT NULL AND NEW.result::text LIKE '%"snapshot"%' THEN
    IF EXISTS (SELECT 1 FROM withdrawals w WHERE w.branch = NEW.branch AND w.status IN ('pending','cleaning','pending_cleanup')) THEN
      RAISE EXCEPTION 'branch % has an active withdrawal; forecast data is frozen', NEW.branch USING ERRCODE = '55006';
    END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS withdraw_guard_forecast_runs ON forecast_runs;
CREATE TRIGGER withdraw_guard_forecast_runs BEFORE INSERT OR UPDATE ON forecast_runs
FOR EACH ROW EXECUTE FUNCTION withdraw_guard_forecast_runs();

CREATE OR REPLACE FUNCTION withdraw_guard_daily_volumes() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM withdrawals w WHERE w.branch = NEW.branch AND w.status IN ('pending','cleaning','pending_cleanup')
             AND NEW.day >= w.day_start AND NEW.day <= w.day_end) THEN
    RAISE EXCEPTION 'daily volume % / % is withdrawn and cannot be written back', NEW.branch, NEW.day USING ERRCODE = '55006';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS withdraw_guard_daily_volumes ON daily_volumes;
CREATE TRIGGER withdraw_guard_daily_volumes BEFORE INSERT OR UPDATE ON daily_volumes
FOR EACH ROW EXECUTE FUNCTION withdraw_guard_daily_volumes();
