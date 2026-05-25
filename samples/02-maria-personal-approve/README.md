# Maria Aguilar — personal-loan clean approve

A prime unsecured personal loan applicant. Every rule in the personal
pack clears; the run exercises class-aware extraction + intake +
underwriting + decision end-to-end.

## Headline numbers

| Field | Value | Threshold | Notes |
|---|---|---|---|
| Loan amount | $25,000 | — | |
| Annual income | $108,400 | — | |
| Monthly debt payments | $3,406 | — | Pre-new-loan |
| DTI | 37.7% | ≤ 40% | Clears (close to ceiling — good narrative test) |
| LTI | 23.1% | ≤ 40% | Clears |
| FICO score | 745 | ≥ 660 | Clears (very-good band) |
| Years employment | 6.4 | ≥ 1.0 | Clears |
| Documents present | 4 of 4 | All required personal docs | Clears |

## Expected agent behaviour

- **Intake** — extracts borrower name, SSN-last-4, employer, annual
  income, outstanding debt, credit score, loan purpose, loan amount.
  Personal-class required-fields list is used.
- **Underwriting** — personal rule pack runs (DTI / LTI / FICO / tenure).
  Summary highlights the DTI headroom and FICO band.
- **Decision** — path `approve`, term sheet (principal $25k, 48 mo).

## Borrower

`maria.aguilar@example.com` — **not yet seeded**. Sign up with this
email at `/signup` (pick any 8+ char password), then create the loan
via the `/apply` wizard. The personal-loan wizard is 5 steps: class →
about you → loan → finances → review. Documents (the four `.txt` files
in this folder) go on the status page **after** submit, not in the
wizard. See TESTING_GUIDE Flow B2.

## Files in this folder

- `loan_application.txt`
- `tax_return_2024.txt`
- `bank_statement_april_2026.txt`
- `personal_financial_statement.txt`
