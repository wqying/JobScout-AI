# Evaluation workspace

Milestone 3 starts the discovery evaluation set in
`datasets/industry_discovery_v1.jsonl`. It contains 20 clear and ambiguous industry queries with
human-review targets for intended segments and representative employers.

The credential-free test suite validates the dataset contract and runs the gaming discovery fixture
through the real validation, persistence, and deterministic scoring code with a fake AI client. It
checks source coverage, invalid-candidate isolation, ordering, cost traces, and cache behavior.

A live model evaluation report is intentionally not generated in CI because CI never calls OpenAI.
Before a V1 release, run the same dataset with the configured live model and record company
precision, official careers URL accuracy, unsupported claims, citation coverage, cost, and latency
against the gates in `DESIGN_DOC.md`.
