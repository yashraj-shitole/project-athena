"""Project Athena's LLM evaluation suite.

Layout:
    llm_evals/
    ├── eval/        The framework itself (scenario, runners, matchers, judges, ...)
    ├── datasets/    JSONL scenarios (general_qa.jsonl, refusal.jsonl, ...)
    ├── prompts/     Versioned system + judge prompts
    ├── expected/    Expected outputs + baseline JSON
    ├── scenarios/   Pytest scenario files (one per dataset)
    ├── reports/     Generated JSONL + HTML/JSON/MD/CSV reports
    ├── metrics/     Cached metric snapshots
    └── runners/     CLI entry points

See `eval/README.md` for the framework's design.
"""
