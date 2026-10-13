"""Execute only pure analysis/decision builders from app.py; no app, DB or worker."""
import ast
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

source = ast.parse(open(sys.argv[1], encoding="utf-8").read())
names = {"percentile", "local_day_type", "wilson", "analysis_counts", "analysis_buckets", "build_decision", "heldout_sampling_diagnostic"}
module = ast.Module(body=[n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
local = timezone(timedelta(hours=7))
env = {"math": math, "datetime": datetime, "timedelta": timedelta, "LOCAL": local,
       "ACTIVE_STATES": {"pushing", "ambiguous", "submitted", "queued", "running", "timed_out", "collecting"},
       "iso": lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")}
exec(compile(module, sys.argv[1], "exec"), env)
begin = datetime(2026, 10, 1, 8, tzinfo=local).timestamp()
def run(at, account="a", state="success", latency=100):
    return {"account": account, "due": at - 7200, "submit_begin": at, "state": state,
            "t4x2": True, "start_latency_seconds": latency, "start_latency_upper": latency}
runs = [run(begin + i % 7 * 86400) for i in range(100)]
runs += [run(begin + i % 7 * 86400, "b", "failed") for i in range(20)]
now = begin + 30 * 86400
weighted = env["analysis_buckets"](runs, {"a": None, "b": None}, now)[4]
missing = env["analysis_buckets"](runs, {"a": None, "b": None, "c": None}, now)[4]
null_point = env["analysis_buckets"](runs[:100], {"a": None, "c": None}, now)[4]
sparse = env["analysis_buckets"]([run(begin) for _ in range(20)] + [run(begin + i * 86400, state="queued") for i in range(7)], {"a": None}, now)[4]
decision_now = datetime(2026, 10, 16, 23, 30, tzinfo=local).timestamp()
history = []
for hour, kind, latency in [(22, "weekday", 100), (0, "weekend", 50), (2, "weekend", 60)]:
    date = datetime(2026, 8, 1, hour, tzinfo=local)
    dates = []
    while len(dates) < 7:
        if env["local_day_type"](date) == kind:
            dates.append(date.timestamp())
        date += timedelta(days=1)
    history += [run(dates[i % 7], latency=latency) for i in range(21)]
collector = SimpleNamespace(accounts={"a": None})
overlap = env["build_decision"](collector, history, "probe", "a", decision_now, horizon_hours=3)
# Lower current median but higher current P90 than the future candidate.
tradeoff_history = []
for r in history:
    copy = dict(r)
    if datetime.fromtimestamp(r["submit_begin"], local).hour == 22:
        index = len(tradeoff_history)
        latency = 100 if index < 11 else 5000
        copy.update(start_latency_seconds=latency, start_latency_upper=latency)
    tradeoff_history.append(copy)
tradeoff = env["build_decision"](collector, tradeoff_history, "probe", "a", decision_now, horizon_hours=0.5)
# Identical successful distributions, with only ten seconds of future waiting.
equivalent_history = [{**r, "start_latency_seconds": 100, "start_latency_upper": 100} for r in history]
equivalent_now = datetime(2026, 10, 16, 23, 59, 50, tzinfo=local).timestamp()
equivalent = env["build_decision"](collector, equivalent_history, "probe", "a", equivalent_now, horizon_hours=0.1)
missing_baseline = env["build_decision"](collector, [r for r in history if datetime.fromtimestamp(r["submit_begin"], local).hour != 22], "probe", "a", decision_now, horizon_hours=0.5)
insufficient = env["build_decision"](collector, [r for r in history if datetime.fromtimestamp(r["submit_begin"], local).hour == 22], "probe", "a", decision_now, horizon_hours=0.5)
# One current candidate: the real builder emits tentative/recommended_at.
tentative = env["build_decision"](collector, history, "probe", "a", decision_now, horizon_hours=0.1)
schedule = env["build_decision"](collector, history, "probe", "a", decision_now, mode="schedule", horizon_hours=0.5, day_type="weekend")
heldout_runs = []
heldout_begin = datetime(2026, 9, 1, 8, tzinfo=local).timestamp()
for day in range(14):
    for name in ("a", "b"):
        for offset in (0, 7200):
            fast = (day < 7 and offset == 0) or (day >= 7 and offset == 7200)
            heldout_runs += [run(heldout_begin + day * 86400 + offset, name, latency=100 if fast else 700) for _ in range(3)]
heldout = env["heldout_sampling_diagnostic"](heldout_runs, {"a": None, "b": None}, now)
print(json.dumps({"weighted": weighted, "missing": missing, "null_point": null_point, "sparse": sparse,
                  "overlap": overlap, "tentative": tentative, "schedule": schedule,
                  "tradeoff": tradeoff, "equivalent": equivalent,
                  "missing_baseline": missing_baseline, "insufficient": insufficient, "heldout": heldout}))
