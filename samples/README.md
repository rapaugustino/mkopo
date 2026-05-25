# Sample loan packets

Six self-contained packets of realistic loan documents. Each scenario
hits a different combination of rule outcomes so a single demo run
can exercise every decision path and every HITL gate.

> All applicants, properties, addresses, and SSNs in these files are
> synthetic. None of the example.com email addresses are real
> mailboxes. Don't send mail to them outside the demo environment.

## Index

| # | Scenario | Loan class | Path | Hits |
|---|---|---|---|---|
| 01 | [Riverbend Holdings](./01-riverbend-commercial-approve/) | business | **approve** | Clean. Every rule passes. |
| 02 | [Maria Aguilar](./02-maria-personal-approve/) | personal | **approve** | Clean personal loan, prime FICO. |
| 03 | [Summit Hospitality](./03-summit-hotel-decline/) | business | **decline** | LTV cap breach (hotel). Adverse-action letter. |
| 04 | [Fern Industrial](./04-fern-conditional/) | business | **conditional** | Missing rent roll → conditions list. |
| 05 | [Indigo Capital (partial)](./05-partial-intake-only/) | business | _intake interrupt_ | Drives the doc-request email + HITL approval. |
| 06 | [Trevor Stratus](./06-stratus-personal-decline/) | personal | **decline** | Subprime FICO + DTI breach. Personal adverse-action. |

## How to use

These packets are plain-text files. Two ingestion paths:

### Path A — borrower portal (recommended for end-to-end test)

1. Sign up at `/signup` with the email listed in the scenario's README.
2. Start a new application via the wizard at `/apply`.
3. On the "Upload documents" step, upload the `.txt` files for the
   scenario you're testing.

### Path B — staff "New loan" modal (recommended for testing the
manual-invite flow)

1. Log in as staff (the seeded `j.davis@mkopo.dev`).
2. Click "+ New loan" on the pipeline view.
3. Enter the borrower email and basic loan details.
4. Mkopo emails the borrower a magic-link upload URL.
5. The borrower (you) opens the link and uploads the `.txt` files.

## Coverage matrix

| Feature | Scenario(s) |
|---|---|
| Class-aware required fields | 01, 02, 06 |
| Class-aware required docs | 01, 02, 04 (missing) |
| Intake doc-request email + HITL approval | 05 |
| Underwriting commercial-pack rules | 01, 03, 04 |
| Underwriting personal-pack rules | 02, 06 |
| DSCR / LTV / debt-yield rule breaches | 03 |
| FICO / DTI / LTI rule breaches | 06 |
| Doc completeness warn | 04 |
| Conditional approve + conditions tracking | 04 |
| Adverse-action letter (commercial) | 03 |
| Adverse-action letter (personal) | 06 |
| Preview-and-edit modal (decision sends) | 01, 03, 04, 06 |
| Reg B principal-reason citation lint | 03, 06 |

See [`../TESTING_GUIDE.md`](../TESTING_GUIDE.md) for the full
playbook including agent runs, the staff chat copilot, prompt
management, eval annotations, replay, and regression diff.
