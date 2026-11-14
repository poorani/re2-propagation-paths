# RE2 Propagation Paths

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24211480.svg)](https://doi.org/10.5281/zenodo.24211480)

Reference **failure propagation paths** for the 180 RE2 cases with traces in the
[RCAEval](https://arxiv.org/abs/2412.17015) benchmark (Online Boutique and Train Ticket),
plus the code that builds them from distributed traces.

Root cause analysis (RCA) benchmarks usually label only the **root cause** of each fault.
They do not say **how the failure spread** from the root cause to the services where users
see the symptom. Without that, an RCA method can name the right root cause and still give an
explanation of the failure that is not supported by the data. These labels make it possible to
check that explanation, not just the final answer.

## What is in this repository

| Path | Contents |
|---|---|
| `labels/re2_propagation_labels.jsonl` | One line per case: label, confidence, paths and propagation graph |
| `labels/re2_propagation_summary.csv` | The same cases without paths, for quick filtering |
| `code/reference_path.py` | The labeling method (version v10) |
| `code/full_run.py` | Runs the method on every RE2 case with traces (Kaggle-friendly, resumable) |
| `code/sensitivity_run.py` | Re-runs every case under six threshold settings |
| `tests/test_reference_path.py` | Synthetic test cases for each design decision |
| `results/` | Sensitivity analysis output |

## Summary

| System | Propagation path | Entry point only | No detectable path | Total |
|---|---|---|---|---|
| Online Boutique (RE2-OB) | 40 | 0 | 50 | 90 |
| Train Ticket (RE2-TT) | 51 | 17 | 22 | 90 |
| **Total** | **91 (51%)** | **17 (9%)** | **72 (40%)** | **180** |

- Mean path depth (services on the longest path): 2.15 in Online Boutique (max 3), 3.14 in Train Ticket (max 5).
- About half of the RE2 cases have a failure spread that can be recovered from traces. Path-level
  evaluation of RCA methods only applies to that subset.

## Label format

Each line of `re2_propagation_labels.jsonl` is one case:

```json
{
  "case": "re2tt_ts-train-service_mem_1",
  "dataset": "RE2-TT",
  "system": "Train Ticket",
  "root": "ts-train-service",
  "fault": "mem",
  "label": "path",
  "confidence": "low",
  "low_coverage": false,
  "n_paths": 3,
  "max_depth": 3,
  "paths": [["ts-train-service", "ts-travel-service", "ts-preserve-service"], "..."],
  "graph": [["ts-travel-service", "ts-train-service"], "..."],
  "labeling_version": "v10",
  "settings": {"min_slowdown": 1.1, "alpha": 0.01, "...": "..."}
}
```

| Field | Meaning |
|---|---|
| `label` | `path`: the failure spread from the root cause to at least one caller. `entry only`: the root cause is itself where outside requests arrive, so root and symptom are the same service. `no path`: no spread is visible in the traces. |
| `paths` | Each path starts at the root cause and walks **upstream** to the services that call it, ending where possible at an entry point (where users see the symptom). |
| `graph` | The union of all paths as directed edges `[caller, callee]`, the reference propagation graph. |
| `confidence` | The strictest setting at which the label survives: `high` (2.0x minimum slowdown), `medium` (1.5x), `low` (1.1x only), `none` for no-path cases. |
| `low_coverage` | `true` if the case has fewer than 90% of its system's usual call edges (15 Train Ticket cases), meaning the traces are incomplete. |
| `n_call_edges`, `n_abnormal_edges` | Size of the call graph found in the traces, and how many of its edges changed after the fault. |

## How the labels are built

The method is a simplified, trace-only version of the PAVE protocol from
[OpenRCA 2.0](https://arxiv.org/abs/2606.27154). Because the injected fault and its time are known,
it checks forward, from cause to effect:

1. **Call graph.** Caller-to-callee edges are built from parent and child spans, plus the callee
   named in `operationName` for services that do not record their own spans.
2. **Abnormal edges.** For each edge, call durations after the fault are compared with before it.
   An edge is abnormal if it changed reliably (Mann-Whitney test, p < 0.01) **and** by a meaningful
   amount: at least 1.1x slower, or at least 1.5x faster (fail-fast, such as an out-of-memory service
   rejecting calls quickly), **and** by more than it drifts on its own during normal time
   (second half of the normal period vs the first half, in the same direction, with a 1.2x margin).
   An edge is also abnormal if its error rate rises by 5 points or more.
3. **Onset.** For each abnormal edge, the first 5-second window after the fault in which it passes
   halfway between its normal and after-fault level.
4. **Entry points.** Services that receive requests from outside the traces: the first span of a
   multi-span trace with no traced caller. For Online Boutique, only `frontendservice` (its one
   externally reachable service), because its traces are split per service.
5. **Paths.** Starting at the root cause, walk upstream along abnormal edges, keeping a hop only if
   it started no earlier than the hop before it. Paths ending at an entry point are preferred. If
   outside requests to the root cause itself got slower, the case is `entry only`.

## Sensitivity

All 180 cases were re-run with minimum slowdowns of 1.1x, 1.5x and 2.0x and p-value cutoffs of
0.01 and 0.001 (`results/`).

| Setting | Path | Entry only | No path | Same label as baseline |
|---|---|---|---|---|
| 1.1x (baseline) | 91 | 17 | 72 | 100% |
| 1.5x | 70 | 15 | 95 | 87% |
| 2.0x | 65 | 15 | 100 | 84% |

- The p-value cutoff makes no difference to any label.
- Stricter slowdown thresholds only ever remove paths; they never add or change them.
- 152 of 180 cases (84%) have the same label under all six settings.
- 65 path cases survive even at 2.0x (`confidence: high`), a high-confidence core.

## Limitations

- **Traces only.** Metrics and logs are not used. Failures that do not show up as latency or errors
  in traces are labeled `no path`.
- **No status codes in Train Ticket.** RE2-TT traces carry no status codes, so errors are invisible
  there and only latency changes count. Loss and disk faults rarely produce a path in Train Ticket.
- **Split traces in Online Boutique.** Trace context is not propagated for every service, so the
  entry point is set from the system's architecture rather than detected.
- **Incomplete traces.** 15 Train Ticket cases have partial call graphs (`low_coverage: true`), and
  one (`re2tt_ts-train-service_socket_1`) has no call edges at all.
- **Thresholds.** Labels depend on the minimum slowdown; see Sensitivity. Some real but small
  (often amplified) slowdowns are only caught at 1.1x.
- **Symptoms without a visible path.** Some cases show slowdowns elsewhere that the traces cannot
  connect to the root cause (for example `re2ob_currencyservice_cpu_1`). These are labeled `no path`,
  not guessed.

## Reproducing

The data comes from the RCAEval dataset on Hugging Face (`phamquiluan/RCAEval`). Each case is
about 20 to 30 MB.

1. Create a notebook (for example on Kaggle) with internet access and `pandas`, `numpy`, `scipy`
   and `huggingface_hub` available.
2. Run `code/reference_path.py`, then `code/full_run.py` in the same session. It downloads each case,
   labels it, writes the results and deletes the case, and it resumes if interrupted.
   The full run takes about 15 to 20 minutes.
3. For the sensitivity analysis, run `code/reference_path.py` then `code/sensitivity_run.py`
   (about 1 to 2 hours).

Run the tests with:

```bash
python tests/test_reference_path.py code
```

## Citation

If you use these labels or the code, please cite this repository (see `CITATION.cff`) and the
RCAEval benchmark:

> Luan Pham, Hongyu Zhang, Huong Ha, Flora Salim, Xiuzhen Zhang. RCAEval: A Benchmark for Root
> Cause Analysis of Microservice Systems with Telemetry Data. arXiv:2412.17015, 2024.

## License

- **Code:** MIT License (`LICENSE`).
- **Labels:** derived from the RCAEval dataset (CC BY 4.0) and released under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Please credit both this repository and RCAEval.
