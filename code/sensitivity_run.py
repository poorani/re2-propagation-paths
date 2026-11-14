# ===== Sensitivity check: how much do the labels change with the settings? =====
# Runs every RE2 case with traces under 6 settings (minimum slowdown x
# significance level). Each case is downloaded once and analysed 6 times.
# Resumable: results are saved after every case; re-run the cell to continue.
# Needs the reference_path.py (v10) function cell to be run first.

import os, glob, json, shutil, time
import pandas as pd
from huggingface_hub import hf_hub_download, snapshot_download

SETTINGS = [(1.1, 0.01), (1.5, 0.01), (2.0, 0.01),     # (min_x, alpha)
            (1.1, 0.001), (1.5, 0.001), (2.0, 0.001)]   # first one = baseline

OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
SENS = f"{OUT}/re2_sensitivity.csv"
ERRORS = f"{OUT}/re2_sensitivity_errors.txt"

idx = pd.read_parquet(hf_hub_download("phamquiluan/RCAEval", "cases.parquet",
                                      repo_type="dataset"))
todo = idx[idx.dataset.isin(["RE2-OB", "RE2-TT"]) & (idx.has_traces == True)]
todo = todo.sort_values(["dataset", "case"])

done = set(pd.read_csv(SENS).case) if os.path.exists(SENS) else set()
print(f"{len(todo)} cases x {len(SETTINGS)} settings | already done: {len(done)} cases")

start_all = time.time()
for i, r in enumerate(todo.itertuples(), 1):
    if r.case in done:
        continue
    t0 = time.time()
    try:
        snapshot_download(repo_id="phamquiluan/RCAEval", repo_type="dataset",
                          allow_patterns=f"*{r.case}*", local_dir="data")
        folder = [p for p in glob.glob(f"data/**/{r.case}", recursive=True)
                  if os.path.isdir(p)][0]
        traces = pd.read_parquet(f"{folder}/traces.parquet")
        inject = int(open(f"{folder}/inject_time.txt").read().strip())
        kw = {"entry": "frontendservice"} if r.dataset == "RE2-OB" else {}
        rows = []
        for min_x, alpha in SETTINGS:
            edges, paths = build_reference_path(traces, inject, r.root_cause_service,
                                                min_x=min_x, alpha=alpha, **kw)
            graph = sorted({(p[k + 1], p[k]) for p in paths for k in range(len(p) - 1)})
            label = ("no path" if not paths else
                     "entry only" if paths == [[r.root_cause_service]] else "path")
            rows.append(dict(case=r.case, dataset=r.dataset, root=r.root_cause_service,
                             fault=r.fault, min_x=min_x, alpha=alpha, label=label,
                             n_paths=len(paths),
                             max_depth=max((len(p) for p in paths), default=0),
                             n_abnormal=int(edges.abnormal.sum()) if len(edges) else 0,
                             graph=json.dumps([list(g) for g in graph])))
        pd.DataFrame(rows).to_csv(SENS, mode="a", index=False,
                                  header=not os.path.exists(SENS))
        print(f"[{i}/{len(todo)}] {r.case}: " +
              " | ".join(f"{x['min_x']}/{x['alpha']}: {x['label']}" for x in rows) +
              f"  ({round(time.time() - t0, 1)}s)")
        del traces
    except Exception as ex:
        with open(ERRORS, "a") as f:
            f.write(f"{r.case}\t{type(ex).__name__}: {ex}\n")
        print(f"[{i}/{len(todo)}] {r.case}: ERROR {type(ex).__name__}: {ex}")
    finally:
        for p in glob.glob(f"data/**/{r.case}", recursive=True):
            shutil.rmtree(p, ignore_errors=True)

print(f"\nFinished in {round((time.time() - start_all) / 60, 1)} min. File: {SENS}")
