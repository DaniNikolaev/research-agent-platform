# Agent 1B — independent verification workflow

Agent 1B verifies the Agent 1A card **against the original article**, independently. It
is not a second opinion of the same prompt: it reads the source itself and produces its
own evidence.

Iteration 0 delivers only the interface:

| Contract | Module |
|---|---|
| Input package v1 | `contracts/input_package.py` |
| Verification report v1 | `contracts/verification_report.py` |

Hard rules encoded in the contracts:

1. the received card is **read-only** — `MutationPolicy.card_mutation_allowed` is
   `Literal[False]`, so a report cannot even be constructed "for" a mutating workflow;
2. a package without a reference to the immutable original is rejected — an independent
   check is impossible without the source;
3. an unsupported Agent 1A contract version is rejected with a diagnostic naming the
   supported versions;
4. corrections are a separate procedure producing a **new** package version; the previous
   card and the previous report are preserved;
5. findings use the closed status set `verified`, `partially_supported`, `unsupported`,
   `contradicted`, `not_verifiable`, `not_checked` — and a report cannot claim
   `scientific_review_status: verified` while it contains a non-verified finding.

Not implemented here, by scope: the verification graph, the re-reading of the PDF, the
numeric cross-check, the human review UI, and the correction procedure.
