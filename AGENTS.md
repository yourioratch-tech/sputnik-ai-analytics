# Repository instructions

- This project is read-only market research. Never add broker credentials,
  account controls, or order placement.
- Preserve evidence provenance: observed webhook, historical, model-derived, or
  unconfirmed.
- Completed bars are immutable. Corrections require an explicit audited path.
- Backtests use next-bar information boundaries, costs, a benchmark, dataset
  fingerprints, and limitations.
- Never describe a historical positive rate as a live probability forecast.
- Runtime secrets, databases, raw events, private data, and reports stay out of
  Git.
- Broker exports are local-only evidence. Do not persist account identifiers,
  HINs, contract-note numbers, or raw documents in the repository.
