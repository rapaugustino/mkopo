# Aspen Grove — real-email magic-link test

A clean single-tenant industrial NNN deal designed for one specific
purpose: **testing the staff-invite → real-email → magic-link →
upload chain end-to-end with an email that actually lands in your
inbox.**

The numbers clear every rule, so once you've finished the
borrower-side test you can run intake → underwriting → decision
through to **approve** as a bonus.

## Borrower email

`rapaugustino@gmail.com` ← use this in the staff "+ New loan" modal.

## Headline numbers

| Field | Value | Threshold | Notes |
|---|---|---|---|
| Loan amount | $2,400,000 | — | |
| Appraised value | $4,400,000 | — | |
| LTV | 54.5% | ≤ 70% (industrial) | Clears |
| Annual NOI | $307,176 | — | |
| DSCR (6.5% est., 25-yr amort) | ~1.58 | ≥ 1.25 (industrial) | Clears |
| Debt yield | 12.8% | ≥ 8.5% (industrial) | Clears |
| Appraisal age | ~16 days | ≤ 180 | Clears |
| Documents present | 4 of 4 | All required | Clears |

## Prerequisites

For a real email to actually be delivered, your API needs Resend
credentials configured:

```bash
# api/.env or however you load secrets
RESEND_API_KEY=re_<your-key>
RESEND_FROM_ADDRESS=mkopo@ubunifutech.com   # or any verified-in-Resend domain
RESEND_FROM_NAME=Mkopo
FRONTEND_URL=http://localhost:3000           # so magic links point at your dev frontend
```

Restart the API after editing.

**If you don't have Resend configured**, the email won't send — but
the magic-link URL gets logged to the API console. You can copy it
from there and follow steps 4–7 the same way; you just skip the
inbox step.

## How to test (this is essentially Flow S1 in the testing guide)

1. **Staff side.** Open `localhost:3000` (you're auto-signed-in as
   Jordan Davis). Click **+ New loan** on the pipeline.

2. Fill in the modal:
   - Loan class: **Business / commercial real estate**
   - Loan type: **Permanent**
   - Amount: `2400000`
   - Borrower name: `Aspen Grove Industrial Partners, LLC`
   - Borrower email: `rapaugustino@gmail.com`
   - Guarantor name: `Richard Pallangyo`
   - Guarantor email: `rapaugustino@gmail.com`

3. **Submit.**

   **Expected.**
   - Toast: "Loan created. Invite emailed to rapaugustino@gmail.com."
   - API log shows a `loan_created` audit event with
     `invite_sent_to=rapaugustino@gmail.com`.
   - If Resend is configured: an email lands in your inbox within
     a few seconds, "Your loan officer has started a loan
     application for you…" with a click-through button.
   - If Resend is NOT configured: the magic-link URL is printed to
     the API console — copy it.

4. **Borrower side — open a private browser window.**
   - If you got the email, click the button. The URL looks like
     `http://localhost:3000/auth/verify?purpose=loan_invite&token=…&loan_id=…`.
   - If you copied from the console, paste the URL there.

5. **Expected after clicking the link.**
   - Toast: "Welcome — opening your application."
   - You're auto-signed-in as the borrower (NO password prompt — the
     email receipt is the proof of ownership).
   - You land on `/apply/[loan_id]`.
   - The page shows the loan reference, current stage `intake`, and
     a "Required documents" checklist with 4 unchecked items.

6. **Upload the four documents** from this folder (drag-and-drop or
   click). Each one ticks its matching checklist box as the heuristic
   filename match resolves.

7. **Expected after upload.**
   - All four boxes ticked.
   - Documents visible in the case file (`/loans/[id]` on staff side).
   - Switch back to the staff window — running intake on this loan
     will now extract every required field with high confidence.

8. **Optional — finish the demo end-to-end.**
   - As staff, run intake → accept extractions in `/review-queue` →
     transition to underwriting → run underwriting → run decision.
   - Path defaults to **approve**.
   - Click **Review & send to borrower** to test the editable-draft
     modal one more time. The message lands on your borrower-window
     `/apply/[loan_id]` status page.

## Files in this folder

- `loan_application.txt`
- `appraisal_report.txt`
- `rent_roll.txt`
- `personal_financial_statement.txt`
