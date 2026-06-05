"""Personal-loan fixture.

Lives outside the commercial real-estate cluster because it exercises
an entirely different rule pack (DTI / FICO / LTI / employment
tenure) and a different required-doc set (tax return + bank
statements + PFS + application — no appraisal or rent roll).

Demo value: lets you click into intake/underwriting for a consumer
loan and see the personal flow end-to-end — class-aware extraction,
the correct missing-docs message, the personal rule pack with real
values to evaluate. Without a seeded personal loan, every demo
question about "does this work for personal too?" requires a fresh
borrower signup.
"""

from __future__ import annotations

from decimal import Decimal

from mkopo.models import (
    DocumentType,
    LoanClass,
    LoanStage,
    LoanType,
    PartyRole,
    PartyType,
)
from scripts.seed_fixtures._types import SeedDoc, SeedLoan, SeedParty

KAYA = SeedLoan(
    loan_type=LoanType.PERMANENT,  # closest mapping for an unsecured term loan
    loan_class=LoanClass.PERSONAL,
    amount_usd=Decimal("28000.00"),
    borrower_email="kaya.morales@example.com",
    # Sits in intake by default — the personal flow's "happy path" demo
    # is to click "Run intake", see the extractor pull income / credit
    # score from the PFS, then transition to underwriting and watch the
    # personal rule pack score it.
    starting_stage=LoanStage.INTAKE,
    # Match the rule-eval thresholds: $72k annual income, $1,800/mo
    # debt → 30% DTI (under 40% cap), FICO 712 (very-good, clears 660
    # floor), 4 years tenure (clears 1y minimum), LTI 28k/72k ≈ 39%
    # (just under the 40% ceiling — deliberately close so the rule
    # outcome surfaces a meaningful headroom message).
    meta_extra={
        "annual_income": "72000",
        "monthly_debt_payments": "1800",
        "credit_score": 712,
        "years_employment": "4.0",
        "loan_purpose": "Debt consolidation",
    },
    parties=[
        # Personal loans have a single applicant party; no entity, no
        # guarantor. The borrower portal mirrors this shape.
        SeedParty(
            name="Kaya Morales",
            party_type=PartyType.PERSON,
            role=PartyRole.BORROWER,
            email="kaya.morales@example.com",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — UNSECURED PERSONAL LOAN
Submitted: May 04, 2026

1. APPLICANT INFORMATION
Full Legal Name: Kaya Morales
Date of Birth: March 18, 1991
SSN (last 4): ****-**-4419
Current Address: 2210 Beacon Hill Ave, Apt 12, Seattle, WA 98144
Phone: (206) 555-0184
Email: kaya.morales@example.com

2. EMPLOYMENT
Employer: Cascade Health Cooperative
Position: Senior Product Designer
Tenure: 4 years, 2 months (since February 2022)
Annual Gross Income: $72,000 (W-2)

3. LOAN REQUEST
Amount: $28,000
Purpose: Debt consolidation (3 credit card balances, ~22% blended APR)
Requested Term: 60 months, fixed APR
""",
        ),
        SeedDoc(
            filename="tax_return_2024.txt",
            doc_type=DocumentType.TAX_RETURN,
            text="""\
FORM 1040 — U.S. INDIVIDUAL INCOME TAX RETURN
Tax Year: 2024

Taxpayer: Kaya Morales
Filing Status: Single
SSN: ***-**-4419

INCOME
1a   Total wages, salaries (Form W-2, Box 1):      $71,840.00
1z   Total wage income:                            $71,840.00
2b   Taxable interest:                                $128.00
9    Total income:                                 $71,968.00
11   Adjusted gross income (AGI):                  $71,968.00

DEDUCTIONS
12   Standard deduction:                           $14,600.00
15   Taxable income:                               $57,368.00

TAX AND PAYMENTS
16   Tax (per tax tables):                          $6,571.00
25a  Federal income tax withheld (W-2):             $7,840.00
33   Total payments:                                $7,840.00
34   Refund:                                        $1,269.00

W-2 attached: Cascade Health Cooperative, EIN 91-1842066
""",
        ),
        SeedDoc(
            filename="bank_statement_april_2026.txt",
            doc_type=DocumentType.BANK_STATEMENT,
            text="""\
PACIFIC FIRST CREDIT UNION — Monthly Statement
Account Holder: Kaya Morales
Account: Checking ****6201
Statement Period: April 01, 2026 — April 30, 2026

Opening balance:                                   $4,182.40
Total deposits / credits:                          $6,012.00
  - Direct deposit (Cascade Health):  $2,756.00 (2 entries)
  - Refund (state tax):                  $500.00
Total withdrawals / debits:                        $5,891.20
  - Rent (Beacon Hill Mgmt):           $1,650.00
  - Credit card payments (3 banks):    $1,800.00
  - Auto loan (Pacific First):           $412.00
  - Utilities, groceries, misc:        $2,029.20
Closing balance:                                   $4,303.20

Average daily balance, last 90 days: $3,964.00
""",
        ),
        SeedDoc(
            filename="personal_financial_statement.txt",
            doc_type=DocumentType.PERSONAL_FINANCIAL_STATEMENT,
            text="""\
PERSONAL FINANCIAL STATEMENT
Applicant: Kaya Morales
As of: May 01, 2026

ASSETS
Checking (Pacific First):                          $4,303
Savings (Pacific First):                          $12,800
401(k) vested balance:                            $48,600
Auto (2021 Subaru Outback, KBB):                  $19,400
Personal property (est.):                         $11,000
TOTAL ASSETS:                                     $96,103

LIABILITIES
Credit card balances (3 accounts, blended 22% APR):
  - Chase Sapphire:                                $7,840
  - Discover It:                                   $6,210
  - Capital One Venture:                           $5,180
Credit cards total:                               $19,230
Auto loan (Pacific First, $412/mo, 18mo remaining): $7,420
TOTAL LIABILITIES:                                $26,650

NET WORTH:                                        $69,453

MONTHLY OBLIGATIONS
Rent:                                              $1,650
Credit card minimums:                              $1,800
Auto loan:                                           $412
Other (utilities, insurance, sub):                   $385
TOTAL MONTHLY OBLIGATIONS:                         $4,247

CREDIT
FICO Score (Experian, pulled April 28, 2026):         712
""",
        ),
    ],
)
