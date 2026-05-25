# Summit Hospitality — hotel decline (ECOA Reg B test)

A hotel bridge ask that fails multiple rule thresholds. Designed to
exercise the decline path: the decision agent should pick "decline",
generate an adverse-action letter citing the specific rule ids, and
the underwriter should send the letter through the preview-and-edit
modal (with the Reg B reason-citation lint visible).

## Headline numbers

| Field | Value | Threshold | Outcome |
|---|---|---|---|
| Loan amount | $5,000,000 | — | |
| Appraised value | $7,000,000 | — | |
| LTV | **71.4%** | ≤ 65% (hotel cap) | **BLOCK** |
| Annual NOI | $543,400 | — | TTM |
| DSCR (6.5% IO est.) | **1.67** | ≥ 1.45 (hotel floor) | Clears narrowly |
| Debt yield | **10.9%** | ≥ 10.0% (hotel floor) | Clears narrowly |
| Occupancy TTM | 51.2% | — | Material weakness narrative |
| Documents present | 4 of 4 | All required | Clears |

The headline failure is the **LTV cap** for hotels. Note that the
underwriting summary will also call out occupancy + RevPAR weakness
as a contextual concern even though the asset-type ratios narrowly
clear — that's the narrative-vs-rules split working as designed.

## Expected agent behaviour

- **Intake** — clean extraction, no missing fields.
- **Underwriting** — risk band `high`, recommendation `decline`,
  citations on `appraised_value`, `loan_amount`, `annual_noi`.
- **Decision** — path `decline`, generates an adverse-action letter
  with `principal_reasons` including `ltv_under_cap`.

## Test points

When you reach the decision panel:
1. Click **Review & send adverse action letter**.
2. The preview modal shows the body + a pill row of principal reasons.
3. Edit the body to *remove* the rule id reference — watch the pill
   flip to "missing" + the footer warning appear.
4. Restore the citation, click **Send adverse action letter**. The
   loan transitions to `declined`.

## Files in this folder

- `loan_application.txt`
- `appraisal_report.txt`
- `rent_roll.txt` (occupancy report — hotel surrogate)
- `personal_financial_statement.txt`
