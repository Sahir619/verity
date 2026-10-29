"""Evals CLI: python -m verity.evals --suite <name> --tasks all --provider fake|nebius --repeats N.

Modules:
  __main__        argument parsing and the suite runner
  record          EvalRecord (one JSONL line per task per repeat, ARCHITECTURE 14.4) and the result file
  harness         EvalEnv (settings, provider, tools, store) and run_pipeline (one full orchestrator run)
  injectors       bug injectors for the repair suite (wrong width, off-by-one, missing reset, swapped operands)
  suite_*         one module per suite family
  report          evals/report.md generator
"""
