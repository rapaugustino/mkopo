# Indigo Capital Partners — partial packet (drives the intake email)

A loan with only the loan application uploaded. Tests the
intake-agent draft-document-request email path + the HITL approval
gate (`IntakeApprovalModal`).

## Expected agent behaviour

- **Intake `extract_all_documents`** — runs on the single document.
  Most required fields (`appraised_value`, `annual_noi`, `appraisal_date`,
  `guarantor_list`) come back missing.
- **`identify_missing`** — produces a non-empty missing-fields list.
- **`draft_doc_request`** — the LLM drafts a polite email asking for
  the missing items. Class-aware: this is a commercial loan so it
  asks for the **business** doc set (appraisal, rent roll, PFS).
- **`request_human_approval`** — the graph **interrupts**; the SSE
  stream emits an `interrupt` event with `{type: "approve_email",
  draft: {subject, body_text}, missing_fields}`.
- The frontend opens **`IntakeApprovalModal`** with the draft loaded.
  The underwriter can edit subject + body, click **Send** (or Cancel).
- On Send, the `resume_intake` endpoint receives the edited text and
  the graph's `send_email` node ships it via Resend.

## Test points

1. After running intake, the modal should open with editable
   subject + body fields.
2. Edit the body; click Send. Verify `audit_events` shows
   `action="send_email"` with the **edited** body, not the original.
3. Try cancelling instead — the graph should reach `cancelled` and
   no `send_email` audit event is written.

## Files in this folder

- `loan_application.txt` (the only document — that's the point)
