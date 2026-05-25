# Riverbend Holdings — clean commercial approve

A well-underwritten Capitol Hill multifamily bridge loan. Every rule
clears, no warnings.

## Headline numbers

| Field | Value | Threshold | Notes |
|---|---|---|---|
| Loan amount | $2,000,000 | — | |
| Appraised value | $3,000,000 | — | |
| LTV | 66.7% | ≤ 75% (multifamily cap) | Clears |
| Annual NOI | $224,889 | — | Stabilised, occupied 100% |
| DSCR (6.5% IO, est.) | ~1.73 | ≥ 1.20 (multifamily floor) | Clears |
| Debt yield | 11.2% | ≥ 8.0% (multifamily floor) | Clears |
| Appraisal age | ~16 days | ≤ 180 days | Clears |
| Guarantor exposure | $480k existing + $2M | ≤ $8M | Clears |
| Documents present | 4 of 4 | All required | Clears |

## Expected agent behaviour

- **Intake** — extracts all 8 required fields with high confidence,
  no missing fields, no draft email required → graph reaches `complete`.
- **Underwriting** — produces a 3–5 section summary with citations,
  recommends `proceed_to_decision`, risk band `low`.
- **Decision** — picks path `approve`, generates a clean term sheet
  (principal $2M, rate ~SOFR+350, 24 mo IO), no conditions.

## Borrower

`elena@riverbendholdings.example` — **not yet seeded**. Sign up with
this email at `/signup` (pick any 8+ char password), then create
the loan via the `/apply` wizard. Documents go on the status page
**after** submit, not in the wizard. The wizard is 5 steps: class →
business → loan → guarantor → review. See TESTING_GUIDE Flow B1
for the exact step-by-step.

## Files in this folder

- `loan_application.txt`
- `appraisal_report.txt`
- `rent_roll.txt`
- `personal_financial_statement.txt`
