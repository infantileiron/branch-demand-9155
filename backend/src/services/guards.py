from sqlalchemy import text

ACTIVE = "('pending','cleaning','pending_cleanup')"

RUN_GUARD = f"""
CREATE OR REPLACE FUNCTION withdraw_guard_forecast_runs() RETURNS trigger AS $$
BEGIN
  IF NEW.result IS NOT NULL AND NEW.result::text LIKE '%"snapshot"%' THEN
    IF EXISTS (SELECT 1 FROM withdrawals w WHERE w.branch = NEW.branch AND w.status IN {ACTIVE}) THEN
      RAISE EXCEPTION 'branch % has an active withdrawal; forecast data is frozen', NEW.branch USING ERRCODE = '55006';
    END IF;
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS withdraw_guard_forecast_runs ON forecast_runs;
CREATE TRIGGER withdraw_guard_forecast_runs BEFORE INSERT OR UPDATE ON forecast_runs
FOR EACH ROW EXECUTE FUNCTION withdraw_guard_forecast_runs();
"""

VOLUME_GUARD = f"""
CREATE OR REPLACE FUNCTION withdraw_guard_daily_volumes() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM withdrawals w WHERE w.branch = NEW.branch AND w.status IN {ACTIVE}
             AND NEW.day >= w.day_start AND NEW.day <= w.day_end) THEN
    RAISE EXCEPTION 'daily volume % / % is withdrawn and cannot be written back', NEW.branch, NEW.day USING ERRCODE = '55006';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS withdraw_guard_daily_volumes ON daily_volumes;
CREATE TRIGGER withdraw_guard_daily_volumes BEFORE INSERT OR UPDATE ON daily_volumes
FOR EACH ROW EXECUTE FUNCTION withdraw_guard_daily_volumes();
"""


def install(engine):
    """Database-level fence so even a stale worker process cannot write withdrawn data back."""
    if engine.dialect.name != 'postgresql':
        return
    with engine.begin() as c:
        c.execute(text(RUN_GUARD))
        c.execute(text(VOLUME_GUARD))
