# ===== Full run: reference paths for all RE2 cases with traces =====
# Resumable: results are saved after every case. If the run stops,
# just run this cell again and it continues where it left off.
# Needs the reference_path.py function cell to be run first.

import os, glob, json, shutil, time
import pandas as pd
from huggingface_hub import hf_hub_download, snapshot_download

OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
LABELS = f"{OUT}/re2_reference_paths.jsonl"   # one line per case: the labels
SUMMARY = f"{OUT}/re2_summary.csv"             # one row per case: the numbers
ERRORS = f"{OUT}/re2_errors.txt"

idx = pd.read_parquet(hf_hub_download("phamquiluan/RCAEval", "cases.parquet",
                                      repo_type="dataset"))
todo = idx[idx.dataset.isin(["RE2-OB", "RE2-TT"]) & (idx.has_traces == True)]
todo = todo.sort_values(["dataset", "case"])   # Online Boutique first (smaller)

done = set()
if os.path.exists(SUMMARY):
    done = set(pd.read_csv(SUMMARY).case)
print(f"{len(todo)} cases with traces | already done: {len(done)} | "
      f"left: {len(todo) - len(done & set(todo.case))}")


def summarise(r, edges, paths, seconds):
    ent = edges.attrs.get("entry", pd.DataFrame())
    root_row = ent[ent.service == r.root_cause_service] if len(ent) else ent
    graph = sorted({(p[i + 1], p[i]) for p in paths for i in range(len(p) - 1)})
    return dict(
        case=r.case, dataset=r.dataset, root=r.root_cause_service, fault=r.fault,
        n_edges=len(edges),
        n_abnormal=int(edges.abnormal.sum()) if len(edges) else 0,
        root_is_entry=bool(len(root_row)),
        root_entry_slowdown=(float(root_row.slowdown_x.iloc[0]) if len(root_row) else None),
        n_paths=len(paths),
        max_depth=max((len(p) for p in paths), default=0),   # services in longest path
        entry_only=(paths == [[r.root_cause_service]]),
        no_path=(len(paths) == 0),
        graph_edges=len(graph),
        seconds=round(seconds, 1),
    ), graph


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
        # Online Boutique: only the frontend is reachable from outside, and
        # its traces are split per service, so set the entry point explicitly.
        # Train Ticket: entry points are detected from the traces.
        kw = {"entry": "frontendservice"} if r.dataset == "RE2-OB" else {}
        edges, paths = run_case(folder, r.root_cause_service, **kw)
        row, graph = summarise(r, edges, paths, time.time() - t0)

        with open(LABELS, "a") as f:
            f.write(json.dumps({"case": r.case, "dataset": r.dataset,
                                "root": r.root_cause_service, "fault": r.fault,
                                "paths": paths,
                                "graph": [list(e) for e in graph]}) + "\n")
        pd.DataFrame([row]).to_csv(SUMMARY, mode="a", index=False,
                                   header=not os.path.exists(SUMMARY))
        print(f"[{i}/{len(todo)}] {r.case}: {len(paths)} path(s), "
              f"depth {row['max_depth']}, {row['seconds']}s")
    except Exception as ex:
        with open(ERRORS, "a") as f:
            f.write(f"{r.case}\t{type(ex).__name__}: {ex}\n")
        print(f"[{i}/{len(todo)}] {r.case}: ERROR {type(ex).__name__}: {ex}")
    finally:
        for p in glob.glob(f"data/**/{r.case}", recursive=True):
            shutil.rmtree(p, ignore_errors=True)   # free disk space

print(f"\nFinished in {round((time.time() - start_all) / 60, 1)} min. "
      f"Files: {SUMMARY}, {LABELS}")
