# Fern Industrial Partners — conditional approve

A solid industrial deal but the upload packet is missing the rent
roll. The numeric rules clear, but `rule_doc_completeness` fires as
a warn → the decision agent should pick **conditional** and write
specific conditions tied to the missing material.

## Headline numbers

| Field | Value | Threshold | Outcome |
|---|---|---|---|
| Loan amount | $3,200,000 | — | |
| Appraised value | $5,800,000 | — | |
| LTV | 55.2% | ≤ 70% (industrial) | Clears |
| Annual NOI | $425,420 | — | |
| DSCR (6.5% IO est.) | ~2.04 | ≥ 1.25 (industrial) | Clears |
| Debt yield | 13.3% | ≥ 8.5% (industrial) | Clears |
| Appraisal age | ~16 days | ≤ 180 | Clears |
| **Documents present** | **3 of 4** | All required | **WARN — missing rent roll** |

## Expected agent behaviour

- **Intake** — extracts most fields; may flag missing tenant detail
  given there's no rent roll.
- **Underwriting** — risk band `low`–`medium`, `rule_doc_completeness`
  fires `passed=False severity=warn`, summary explicitly notes the
  missing rent roll.
- **Decision** — path `conditional`. The term sheet is generated, and
  the conditions list contains at least one specific, verifiable
  condition like "Provide a current rent roll naming the tenant and
  showing lease terms" with a `due_within_days`.

## Test points

When you reach the decision panel:
1. The selected path defaults to `conditional`.
2. Conditions render below the term sheet.
3. Click **Review & send to borrower** — preview modal shows the
   composed message including the conditions list. Edit a condition
   in the modal, confirm, and verify the edited text lands on the
   `/apply/[id]` borrower page.
4. Stage moves to `conditions`. From there, mark conditions
   satisfied to transition to `closing`.

## Files in this folder

- `loan_application.txt`
- `appraisal_report.txt`
- `personal_financial_statement.txt`
- (intentionally NO rent_roll.txt — that's what triggers the conditional path)
