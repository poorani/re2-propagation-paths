# ===== Placebo control: how often does the method find "spread" in normal time? =====
# For each RE2 case with traces, this cell:
#   1. keeps only the traces from BEFORE the real fault (so nothing is broken),
#   2. pretends a fault happened at a fake time inside that normal period,
#   3. runs the same labeling method (v10, baseline settings),
#   4. records how many abnormal edges and paths it finds.
# Any path found here is a false path, because there was no fault.
#
# Two fake fault times per case: halfway and two-thirds of the way through
# the normal period. Resumable: run the cell again if it stops.
# Needs the reference_path.py function cell to be run first.

import os, glob, json, shutil, time
import pandas as pd
from huggingface_hub import hf_hub_download, snapshot_download

OUT = "/kaggle/working" if os.path.isdir("/kaggle/working") else "."
RESULTS = f"{OUT}/re2_placebo.csv"
ERRORS = f"{OUT}/re2_placebo_errors.txt"
FAKE_POINTS = [0.5, 2 / 3]      # where the fake fault goes, as a share of the normal period

idx = pd.read_parquet(hf_hub_download("phamquiluan/RCAEval", "cases.parquet",
                                      repo_type="dataset"))
todo = idx[idx.dataset.isin(["RE2-OB", "RE2-TT"]) & (idx.has_traces == True)]
todo = todo.sort_values(["dataset", "case"])

done = set()
if os.path.exists(RESULTS):
    done = set(pd.read_csv(RESULTS).case)
print(f"{len(todo)} cases | already done: {len(done)}")


def label_of(paths, root):
    if len(paths) == 0:
        return "no path"
    if paths == [[root]]:
        return "entry only"
    return "path"


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

        # keep only normal time: everything before the real fault
        sec = traces.startTimeMillis / 1000
        normal = traces[sec < inject]
        start = (normal.startTimeMillis / 1000).min()
        kw = {"entry": "frontendservice"} if r.dataset == "RE2-OB" else {}

        rows = []
        for share in FAKE_POINTS:
            fake = int(start + share * (inject - start))
            edges, paths = build_reference_path(normal, fake, r.root_cause_service, **kw)
            rows.append(dict(
                case=r.case, dataset=r.dataset, root=r.root_cause_service, fault=r.fault,
                fake_share=round(share, 2),
                normal_minutes=round((inject - start) / 60, 1),
                n_edges=len(edges),
                n_abnormal=int(edges.abnormal.sum()) if len(edges) else 0,
                n_slower=int((edges.direction == "slower").sum()) if len(edges) else 0,
                n_faster=int((edges.direction == "faster").sum()) if len(edges) else 0,
                n_errors=int((edges.direction == "errors").sum()) if len(edges) else 0,
                label=label_of(paths, r.root_cause_service),
                n_paths=len(paths),
                paths=json.dumps(paths),
            ))
        pd.DataFrame(rows).to_csv(RESULTS, mode="a", index=False,
                                  header=not os.path.exists(RESULTS))
        print(f"[{i}/{len(todo)}] {r.case}: " +
              " | ".join(f"{x['fake_share']}: {x['label']}, {x['n_abnormal']} abnormal"
                         for x in rows) + f" ({round(time.time() - t0, 1)}s)")
    except Exception as ex:
        with open(ERRORS, "a") as f:
            f.write(f"{r.case}\t{type(ex).__name__}: {ex}\n")
        print(f"[{i}/{len(todo)}] {r.case}: ERROR {type(ex).__name__}: {ex}")
    finally:
        for p in glob.glob(f"data/**/{r.case}", recursive=True):
            shutil.rmtree(p, ignore_errors=True)

print(f"\nFinished in {round((time.time() - start_all) / 60, 1)} min.")

# ===== Summary =====
res = pd.read_csv(RESULTS)
print("\nRuns:", len(res), "(cases x fake times)")
print("\nLabels found with NO fault (each should ideally be 'no path'):")
print(res.groupby(["dataset", "label"]).size().unstack(fill_value=0))
print("\nAbnormal edges per run, no fault:")
print(res.groupby("dataset")[["n_abnormal", "n_slower", "n_faster", "n_errors"]]
        .agg(["mean", "max"]).round(2))
print("\nShare of runs with a false path or entry-only label:",
      round((res.label != "no path").mean() * 100, 1), "%")
print("Length of the normal period (minutes):",
      res.normal_minutes.describe()[["min", "50%", "max"]].round(1).to_dict())
