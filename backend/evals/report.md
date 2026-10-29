# Verity evals report

Generated 2026-10-06 01:13 UTC from 66 rows in 12 eval run(s).

Honesty rules: gains are relative to our own v1 only. Each figure states how many rows it comes from (n). Nothing here is compared to published numbers.

## Provider: fake

_Rows from the fake provider test the pipeline, not model quality. Do not quote them as results._

### Headline figures

| Figure | Value |
|---|---|
| pass@1 (functional, no repair) | 60% (n=5) |
| pass after repair (repair suite, detected bugs only) | 100% (n=7) |
| repair success rate | 100% (n=7) |
| median area gain vs v1 (optimization) | 0.00 % (n=3) |
| median clock period gain vs v1 (optimization) | 0.00 % (n=3) |
| share of runs that improved on v1 | 0% (n=3) |
| share of improved winners proven equivalent | n/a (n=0) |
| median cost per run (USD, estimated) | 0.01 (n=2) |
| median runtime per run (s) | 29.69 (n=2) |

### Functional by task

| Task | pass@1 | median USD |
|---|---|---|
| add8 | 100% (n=1) | 0.0036 |
| alu8 | 50% (n=2) | 0.0053 |
| mac4 | 50% (n=2) | 0.0053 |

### Repair by bug type

| Bug | repair success | median attempts |
|---|---|---|
| missing_reset | 100% (n=2) | 1.0 |
| off_by_one | 100% (n=2) | 1.0 |
| swapped_operands | 100% (n=1) | 1.0 |
| wrong_width | 100% (n=2) | 1.0 |

### Optimization by task (gains are relative to our own v1)

| Task | runs | area gain % | clock gain % | improved | best fitness |
|---|---|---|---|---|---|
| alu8 | 1 | 0.0 | 0.0 | 0% (n=1) | 0.00 |
| mac4 | 2 | 0.0 | 0.0 | 0% (n=2) | 0.00 |

### Regression (winner vs v1)

- checks passed: 100% (n=4)
- equivalence of winners: not_run: 4

### Performance

- median LLM calls per run: 7.0
- median input tokens: 10877, output tokens: 3250

| Stage | median seconds | runs |
|---|---|---|
| evolve | 17.0 | 2 |
| fix | 4.0 | 2 |
| lint | 7.5 | 2 |
| lock | 0.0 | 2 |
| plan | 0.0 | 2 |
| rtl | 0.0 | 2 |
| sim | 0.2 | 2 |
| synth | 4.7 | 2 |

### Pack self-tests

| Task | result | detail |
|---|---|---|
| add8 | pass |  |
| add8 | pass |  |
| add8 | pass |  |
| add8 | pass |  |
| add8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| cnt8 | pass |  |
| cnt8 | pass |  |
| cnt8 | pass |  |
| cnt8 | pass |  |
| cnt8 | pass |  |
| fsm_seq | pass |  |
| fsm_seq | pass |  |
| fsm_seq | pass |  |
| fsm_seq | pass |  |
| fsm_seq | pass |  |
| mac4 | pass |  |
| mac4 | pass |  |
| mac4 | pass |  |
| mac4 | pass |  |
| mac4 | pass |  |
| matmul4 | pass |  |
| matmul4 | pass |  |
| matmul4 | pass |  |
| matmul4 | pass |  |
| mm4_int4 | pass |  |
| mm4_int4 | pass |  |
| mm4_int4 | pass |  |
| mm4_int4 | pass |  |

### Isolation checks

| Task | result | detail |
|---|---|---|
| add8 | pass |  |
| alu8 | pass |  |
| alu8 | pass |  |
| cnt8 | pass |  |
| fsm_seq | pass |  |
| mac4 | pass |  |
| mac4 | pass |  |
| matmul4 | pass |  |
| mm4_int4 | pass |  |

### Comparison across git_sha and prompt versions

| git_sha | prompt versions | pass@1 | repair success | median best fitness |
|---|---|---|---|---|
| abb996f | none recorded | n/a (n=0) | n/a (n=0) | n/a (n=0) |
| abb996f | optimizer=optimize.v1.md@5ea2baf1408f, planner=planner.v1.md@4fa6293a5e7f, repair=repair.v | n/a (n=0) | n/a (n=0) | 0.00 (n=3) |
| abb996f | planner=planner.v1.md@4fa6293a5e7f, rtl_gen=rtl_gen.v1.md@05b5986e20c4 | 60% (n=5) | n/a (n=0) | n/a (n=0) |
| abb996f | repair=repair.v1.md@ab7c381d3d26 | n/a (n=0) | 100% (n=7) | n/a (n=0) |

## Provider: nebius

### Headline figures

| Figure | Value |
|---|---|
| pass@1 (functional, no repair) | 100% (n=1) |
| pass after repair (repair suite, detected bugs only) | n/a (n=0) |
| repair success rate | n/a (n=0) |
| median area gain vs v1 (optimization) | n/a % (n=0) |
| median clock period gain vs v1 (optimization) | n/a % (n=0) |
| share of runs that improved on v1 | n/a (n=0) |
| share of improved winners proven equivalent | n/a (n=0) |
| median cost per run (USD, estimated) | n/a (n=0) |
| median runtime per run (s) | n/a (n=0) |

### Functional by task

| Task | pass@1 | median USD |
|---|---|---|
| add8 | 100% (n=1) | 0.0107 |

### Comparison across git_sha and prompt versions

| git_sha | prompt versions | pass@1 | repair success | median best fitness |
|---|---|---|---|---|
| abb996f | planner=planner.v1.md@4fa6293a5e7f, rtl_gen=rtl_gen.v1.md@05b5986e20c4 | 100% (n=1) | n/a (n=0) | n/a (n=0) |
