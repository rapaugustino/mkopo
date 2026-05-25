# Trevor Stratus — personal-loan decline (subprime FICO + DTI breach)

A personal-loan applicant who hits two blocking rules at once.
Designed to verify the personal-loan decline path + adverse-action
letter generation cites the right rule ids.

## Headline numbers

| Field | Value | Threshold | Outcome |
|---|---|---|---|
| Loan amount | $22,000 | — | |
| Annual income | $48,600 | — | |
| Monthly debt payments | $3,245 | — | Includes rent + minimums |
| **DTI** | **80.1%** | ≤ 40% | **BLOCK** |
| LTI | 45.3% | ≤ 40% | Block (over warn-then-block ceiling) |
| **FICO score** | **612** | ≥ 660 | **BLOCK (subprime)** |
| Years employment | 0.7 | ≥ 1.0 | Warn (tenure short) |
| Documents present | 4 of 4 | All required personal docs | Clears |

Two blocking rules guarantees the decision agent **must** pick
`decline` (engine-policy: blocking failures cannot be overridden).

## Expected agent behaviour

- **Intake** — class-aware extraction on the personal pack pulls
  income, FICO, DTI inputs from the PFS + bank statement.
- **Underwriting** — risk band `high`, recommendation `decline`,
  rationale references `credit_score_floor` and `dti_under_cap`.
- **Decision** — path `decline`. The adverse-action letter's
  `principal_reasons` array must contain both rule ids.

## Test points

1. The adverse-action letter body must reference both reasons in
   plain language (FICO + DTI). Both pills should be **success**
   coloured in the preview modal.
2. Try editing the letter to remove the FICO reference — watch the
   `credit_score_floor` pill flip to **missing** + the footer warn.
3. Send the letter, verify the loan transitions to `declined` and
   the `borrower_reply` audit event carries the edited body.

## Files in this folder

- `loan_application.txt`
- `tax_return_2024.txt`
- `bank_statement_april_2026.txt`
- `personal_financial_statement.txt`
