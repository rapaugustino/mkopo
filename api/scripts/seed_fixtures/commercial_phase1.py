"""Phase-1 commercial real-estate fixtures (CRE bridge / permanent).

Five loans (ATLAS, CEDAR, BAYLINE, NORTHGATE, HALCYON) seeding the
core CRE flow: bridge + permanent, in various stages of the
lifecycle (intake through approved). Together with ``commercial_phase2.py``
they give the entity inspector its guarantor-graph + concentration
story.
"""

from __future__ import annotations

from decimal import Decimal

from mkopo.models import (
    DocumentType,
    LoanStage,
    LoanType,
    PartyRole,
    PartyType,
)

from scripts.seed_fixtures._types import SeedDoc, SeedLoan, SeedParty


ATLAS = SeedLoan(
    loan_type=LoanType.BRIDGE,
    amount_usd=Decimal("2400000.00"),
    borrower_email="matthew@atlasholdings.example",
    # Seed this one straight into underwriting so the pipeline view
    # doesn't show all five loans bunched in intake. ATLAS is the
    # canonical demo loan in the case-file mockup, so it's the
    # natural one to be "further along."
    starting_stage=LoanStage.UNDERWRITING,
    parties=[
        SeedParty(
            name="Atlas Holdings LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="contact@atlasholdings.example",
        ),
        SeedParty(
            name="Matthew Chen",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="matthew@atlasholdings.example",
        ),
        SeedParty(
            name="Jane Park",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="jane@example.com",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — COMMERCIAL BRIDGE FACILITY
Submitted: March 14, 2026

1. BORROWER INFORMATION
Legal Name: Atlas Holdings, LLC
State of Formation: Delaware
Business Address: 1842 South Tacoma Way, Tacoma, WA 98409

2. GUARANTORS
- Matthew Chen
- Jane Park

3. LOAN REQUEST
Amount: $2,400,000
Purpose: Acquisition financing
Loan Type: Bridge, 24 months interest-only
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject property is a 12-unit multifamily building located at 1842 South Tacoma Way,
Tacoma, Washington. Property is garden-style apartment, constructed in 1986,
with current occupancy of 100% as of inspection.

Total annual rental income:     $367,400
Less: operating expenses:       $83,200
Net operating income (NOI):     $284,200

The subject benefits from strong submarket fundamentals, with vacancy in the
South Tacoma submarket reported at 3.2% per Q4 2025 CoStar data.

Appraisal Date: June 15, 2025
Appraised Value: $3,529,400
""",
        ),
    ],
)


CEDAR = SeedLoan(
    loan_type=LoanType.BRIDGE,
    amount_usd=Decimal("1850000.00"),
    borrower_email="ops@cedarridge.example",
    # Also start in underwriting — pairs with ATLAS in the pipeline so
    # there are two loans an underwriter is actively working.
    starting_stage=LoanStage.UNDERWRITING,
    parties=[
        SeedParty(
            name="Cedar Ridge Partners LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="ops@cedarridge.example",
        ),
        SeedParty(
            name="Matthew Chen",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="matthew@atlasholdings.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — COMMERCIAL BRIDGE FACILITY
Submitted: April 02, 2026

1. BORROWER INFORMATION
Legal Name: Cedar Ridge Partners, LLC
State of Formation: Washington
Business Address: 1140 East Madison St, Seattle, WA 98122

2. GUARANTORS
- Matthew Chen

3. LOAN REQUEST
Amount: $1,850,000
Purpose: Refinance + capex reserve
Loan Type: Bridge, 18 months interest-only
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject property is a 14-unit multifamily building at 1140 East Madison St,
Seattle, Washington (Capitol Hill submarket). Built 1972, fully renovated 2021,
current occupancy 92%.

Total annual rental income:     $322,800
Less: operating expenses:       $74,400
Net operating income (NOI):     $248,400

Comparable Capitol Hill multifamily traded at $185k-$200k/unit YTD.

Appraisal Date: March 20, 2026
Appraised Value: $2,720,000
""",
        ),
    ],
)


BAYLINE = SeedLoan(
    loan_type=LoanType.PERMANENT,
    amount_usd=Decimal("3200000.00"),
    borrower_email="finance@bayline.example",
    parties=[
        SeedParty(
            name="Bayline Investments LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="finance@bayline.example",
        ),
        SeedParty(
            name="Sarah Okafor",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="sarah@bayline.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — PERMANENT COMMERCIAL MORTGAGE
Submitted: April 11, 2026

1. BORROWER INFORMATION
Legal Name: Bayline Investments, LLC
State of Formation: Oregon
Business Address: 800 NW Couch St, Portland, OR 97209

2. GUARANTORS
- Sarah Okafor

3. LOAN REQUEST
Amount: $3,200,000
Purpose: Acquisition
Loan Type: Permanent, 10-year fixed
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject property is a Class B suburban office building at 800 NW Couch St,
Portland, Oregon. 22,400 leasable square feet across three floors. Multi-tenant,
weighted average lease term 4.1 years.

Total annual rental income:     $612,800
Less: operating expenses:       $190,400
Net operating income (NOI):     $422,400

Submarket vacancy: 14.2% per Q1 2026 CBRE data. Subject occupancy 88%.

Appraisal Date: February 28, 2026
Appraised Value: $4,571,400
""",
        ),
    ],
)


NORTHGATE = SeedLoan(
    loan_type=LoanType.BRIDGE,
    amount_usd=Decimal("4100000.00"),
    borrower_email="admin@northgatere.example",
    parties=[
        SeedParty(
            name="Northgate RE Group, LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="admin@northgatere.example",
        ),
        SeedParty(
            name="David Liang",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="david@northgatere.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — COMMERCIAL BRIDGE FACILITY
Submitted: March 28, 2026

1. BORROWER INFORMATION
Legal Name: Northgate RE Group, LLC
State of Formation: Washington
Business Address: 16140 Aurora Ave N, Shoreline, WA 98133

2. GUARANTORS
- David Liang

3. LOAN REQUEST
Amount: $4,100,000
Purpose: Repositioning capex + lease-up
Loan Type: Bridge, 36 months interest-only
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject property is a 38,000-square-foot retail strip center at 16140 Aurora Ave N,
Shoreline, Washington. Built 1998, current occupancy 71% (recently lost anchor tenant).

Total annual rental income:     $584,000
Less: operating expenses:       $172,000
Net operating income (NOI):     $412,000

Lease-up assumption: returns to 90% occupancy within 18 months. Stabilized
underwritten NOI: $612,000.

Appraisal Date: January 12, 2026
Appraised Value: $5,856,000
""",
        ),
    ],
)


HALCYON = SeedLoan(
    loan_type=LoanType.PERMANENT,
    amount_usd=Decimal("1200000.00"),
    borrower_email="ops@halcyonproperty.example",
    # Strong-profile industrial NNN deal — seeds at APPROVED so the
    # pipeline view has a closed-deal column populated and the demo
    # can show the post-decision case file (term sheet + conditions).
    starting_stage=LoanStage.APPROVED,
    parties=[
        SeedParty(
            name="Halcyon Property Co.",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="ops@halcyonproperty.example",
        ),
        SeedParty(
            name="Jane Park",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="jane@example.com",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — PERMANENT COMMERCIAL MORTGAGE
Submitted: April 18, 2026

1. BORROWER INFORMATION
Legal Name: Halcyon Property Co.
State of Formation: Idaho
Business Address: 1418 W State St, Boise, ID 83702

2. GUARANTORS
- Jane Park

3. LOAN REQUEST
Amount: $1,200,000
Purpose: Refinance maturing loan
Loan Type: Permanent, 7-year fixed
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject property is a 28,500-square-foot industrial warehouse at 1418 W State St,
Boise, Idaho. Built 2008, single-tenant on a triple-net lease through 2031.

Total annual rental income:     $204,000
Less: operating expenses:       $18,400
Net operating income (NOI):     $185,600

Tenant: regional 3PL operator with 14-year occupancy history.

Appraisal Date: April 02, 2026
Appraised Value: $1,920,000
""",
        ),
    ],
)
