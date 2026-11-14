import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

NOISE = {"health", "traceservice", ""}
RENAME = {"frontend": "frontendservice"}


def _shift(bef, aft, min_x, alpha, margin, fast_x=0.67, cap=5000, seed=0):
    """Did durations reliably change after the fault, by more than they drift
    on their own during normal time? Both directions count: slower (overload,
    delay) and much faster (fail-fast: a crashing or out-of-memory service
    rejects calls quickly), as in PAVE's "deviates from baseline" check.

    bef, aft : DataFrames with 'duration' and 'sec' columns
    returns  : (ratio x, p-value, normal drift x, abnormal?, direction)
    """
    rng = np.random.default_rng(seed)

    def sample(s):
        s = s.to_numpy()
        return rng.choice(s, cap, replace=False) if len(s) > cap else s

    med_b, med_a = bef.duration.median(), aft.duration.median()
    x = med_a / med_b if med_b > 0 else np.nan
    a_s, b_s = sample(aft.duration), sample(bef.duration)
    p_up = mannwhitneyu(a_s, b_s, alternative="greater").pvalue
    p_down = mannwhitneyu(a_s, b_s, alternative="less").pvalue
    # noise check: second half of normal time vs first half, measured in the
    # SAME direction as the change being tested (normal speed-ups must not
    # block a real slowdown, and vice versa)
    mid = bef.sec.median()
    b1, b2 = bef[bef.sec < mid], bef[bef.sec >= mid]
    drift_up = drift_down = 1.0
    if len(b1) >= 5 and len(b2) >= 5 and b1.duration.median() > 0 \
            and b2.duration.median() > 0:
        r = b2.duration.median() / b1.duration.median()
        drift_up, drift_down = max(1.0, r), max(1.0, 1 / r)
    if p_up < alpha and x >= min_x and x >= drift_up * margin:
        return x, p_up, drift_up, True, "slower"
    if x > 0 and p_down < alpha and x <= fast_x and (1 / x) >= drift_down * margin:
        return x, p_down, drift_down, True, "faster"
    return x, min(p_up, p_down), (drift_up if x >= 1 else drift_down), False, ""


def build_reference_path(traces, inject, root, min_x=1.1, alpha=0.01,
                         margin=1.2, fast_x=0.67, err_jump=0.05, bin_s=5, entry=None,
                         max_depth=8, max_paths=50):
    """Trace-only reference propagation path for one RCAEval case.

    A simplified version of PAVE (OpenRCA 2.0): candidate edges come from
    the trace call graph; an edge counts as abnormal if it got reliably
    slower (Mann-Whitney test p < alpha, at least min_x, and more than its own normal
    drift) or started erroring after the fault; a path is kept only if each
    hop's problem starts no earlier than the hop before it.

    traces : DataFrame from RCAEval read_traces
    inject : fault injection time in seconds (inject_time.txt)
    root   : root_cause_service from the case index
    entry  : known entry service(s), a name or a list (default: detected
             automatically as services that start multi-span traces with
             no traced caller). For Online Boutique use "frontendservice".
    """
    cols = ["spanID", "parentSpanID", "serviceName",
            "startTimeMillis", "duration", "statusCode"]
    for extra in ("operationName", "traceID", "startTime"):
        if extra in traces.columns:
            cols.append(extra)
    t = traces[cols].copy()
    t["serviceName"] = t["serviceName"].map(lambda s: RENAME.get(s, s))
    known = set(t.serviceName.unique())

    # 1a. caller -> callee from parent/child spans (works for any system)
    parent = t[["spanID", "serviceName"]].rename(
        columns={"spanID": "parentSpanID", "serviceName": "caller"})
    e1 = t.merge(parent, on="parentSpanID", how="inner")
    e1 = e1.rename(columns={"serviceName": "callee"})
    e1 = e1[(e1.caller != e1.callee) & ~e1.callee.isin(NOISE) & ~e1.caller.isin(NOISE)]
    e1 = e1.assign(source="parent")

    # 1b. caller -> callee named in operationName, for services that do not
    #     record their own spans (e.g. "hipstershop.CartService/GetCart").
    #     Only used for pairs that 1a did not find, so no call is counted twice.
    parts = [e1]
    if "operationName" in t.columns:
        def callee_from_op(op):
            name = str(op).lstrip("/").split("/")[0].split(".")[-1].lower()
            return RENAME.get(name, name)
        e2 = t.assign(caller=t.serviceName,
                      callee=t.operationName.map(callee_from_op))
        e2 = e2[(e2.caller != e2.callee) & ~e2.callee.isin(NOISE)
                & ~e2.caller.isin(NOISE)]
        e2 = e2[e2.callee.str.endswith("service") | e2.callee.isin(known)]
        found = set(zip(e1.caller, e1.callee))
        e2 = e2[[p not in found for p in zip(e2.caller, e2.callee)]]
        parts.append(e2.assign(source="operation"))
    e = pd.concat(parts, ignore_index=True)

    e["sec"] = e.startTimeMillis / 1000
    e["after"] = e.sec >= inject
    # errors: gRPC code 0 and HTTP 1xx-3xx are OK; empty codes count as
    # unknown (RE2 Train Ticket traces have no status codes at all)
    code = pd.to_numeric(e.statusCode, errors="coerce")
    e["err"] = code.notna() & (code != 0) & ~code.between(100, 399)

    # 2. per-edge before/after comparison
    rows = []
    for (a, b), g in e.groupby(["caller", "callee"]):
        bef, aft = g[~g.after], g[g.after]
        if len(bef) < 5 or len(aft) < 5:
            continue
        med_b, med_a = bef.duration.median(), aft.duration.median()
        x, p, drift, slow, direction = _shift(bef, aft, min_x, alpha, margin, fast_x)
        ej = aft.err.mean() - bef.err.mean()
        abnormal = slow or (ej >= err_jump)

        # 3. onset: first time bin after injection where the median is past
        #    halfway between the normal level and the after-fault level
        onset = np.nan
        if abnormal:
            half = med_b + (med_a - med_b) / 2
            aft = aft.assign(bin=((aft.sec - inject) // bin_s).astype(int))
            for bn, gb in aft.groupby("bin"):
                m = gb.duration.median()
                if ((direction == "slower" and m >= half) or
                        (direction == "faster" and m <= half) or
                        gb.err.mean() - bef.err.mean() >= err_jump):
                    onset = inject + bn * bin_s
                    break
        rows.append(dict(caller=a, callee=b, calls=len(g),
                         source=g.source.iloc[0],
                         before_med=med_b, after_med=med_a,
                         slowdown_x=round(x, 2), drift_x=round(drift, 2),
                         p=float(f"{p:.1e}"), err_jump=round(ej, 3),
                         direction=direction or ("errors" if abnormal else ""),
                         abnormal=abnormal, onset=onset))
    edges = pd.DataFrame(rows)

    # 2b. entry points: where requests come in from outside (e.g. the load
    #     generator), i.e. where users would see the symptom. A span counts
    #     only if it has no traced caller AND it is the earliest span of its
    #     trace AND that trace has at least 2 spans. Orphan spans whose parent
    #     record is missing mid-request, and isolated single-span traces
    #     (common in Online Boutique, where trace context is not propagated
    #     for every service), are not entry points.
    ent = t[~t.parentSpanID.isin(t.spanID) & ~t.serviceName.isin(NOISE)].copy()
    if "traceID" in t.columns:
        tcol = "startTime" if "startTime" in t.columns else "startTimeMillis"
        first = t.loc[t.groupby("traceID")[tcol].idxmin(), "spanID"]
        multi = t.groupby("traceID").spanID.transform("size") >= 2
        ent = ent[ent.spanID.isin(set(first)) & ent.index.isin(t.index[multi])]
    ent["sec"] = ent.startTimeMillis / 1000
    ent["after"] = ent.sec >= inject
    erows = []
    for s, g in ent.groupby("serviceName"):
        bef, aft = g[~g.after], g[g.after]
        if len(bef) < 5 or len(aft) < 5:
            continue
        x, p, drift, slow, direction = _shift(bef, aft, min_x, alpha, margin, fast_x)
        erows.append(dict(service=s, spans=len(g),
                          before_med=bef.duration.median(),
                          after_med=aft.duration.median(),
                          slowdown_x=round(x, 2), drift_x=round(drift, 2),
                          p=float(f"{p:.1e}"), direction=direction,
                          abnormal=slow))
    entry_df = pd.DataFrame(erows)
    if entry is not None:
        # known entry points from the system's architecture (e.g. Online
        # Boutique: only frontendservice is reachable from outside)
        entry_services = {entry} if isinstance(entry, str) else set(entry)
        if len(entry_df):
            entry_df = entry_df[entry_df.service.isin(entry_services)]
    else:
        entry_services = set(entry_df.service) if len(entry_df) else set()
    root_entry_abnormal = bool(len(entry_df) and
                               entry_df[(entry_df.service == root) & entry_df.abnormal].shape[0])

    if edges.empty:
        paths = [[root]] if root_entry_abnormal else []
        edges.attrs["entry"] = entry_df
        return edges, paths

    # 4. walk upstream from the root along abnormal edges, timing rule enforced
    bad = edges[edges.abnormal]
    callers_of = {}
    for r in bad.itertuples():
        callers_of.setdefault(r.callee, []).append((r.caller, r.onset))

    paths = []

    def walk(node, path, last_onset):
        if len(paths) >= max_paths:
            return
        nxt = [(c, o) for c, o in callers_of.get(node, [])
               if c not in path and (np.isnan(last_onset) or o >= last_onset)]
        if not nxt or len(path) > max_depth:
            if len(path) > 1:
                paths.append(path)
            return
        for c, o in nxt:
            walk(c, path + [c], o)

    walk(root, [root], np.nan)
    # prefer paths that end where users see the symptom (an entry point)
    reached = [p for p in paths if p[-1] in entry_services]
    paths = reached or paths
    # entry-point fault: the root itself is slower for outside requests,
    # so root and symptom are the same place (path of length 1)
    if root_entry_abnormal:
        paths = [[root]] + paths
    edges.attrs["entry"] = entry_df
    return edges, paths


def run_case(path, root, **kw):
    """Convenience wrapper for a downloaded RCAEval case folder.
    Reads the parquet file directly, so RCAEval does not need to be installed."""
    traces = pd.read_parquet(f"{path}/traces.parquet")
    inject = int(open(f"{path}/inject_time.txt").read().strip())
    return build_reference_path(traces, inject, root, **kw)
