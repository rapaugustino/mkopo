"""Seed the database with synthetic loan data for development.

Run:
    uv run python scripts/seed.py            # append 10 loans
    uv run python scripts/seed.py --reset    # truncate first, then seed

Idempotent on the underwriter user; loan rows are added each run (so the
reference number increments and you can see the LN-YYYY-NNNN sequence
working). Documents are chunked + embedded so the comparable-loans /
"Ask the file" features have a corpus to work with.

The ``--reset`` flag TRUNCATEs loans (CASCADE to documents, parties,
extractions, audit_events, ...) so a fresh demo starts with exactly the
10 fixtures defined here and the pipeline view isn't cluttered with
appended re-runs.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import select, text

from mkopo.db import get_session
from mkopo.models import (
    Document,
    DocumentType,
    Loan,
    LoanClass,
    LoanParty,
    LoanStage,
    LoanType,
    Party,
    PartyRole,
    PartyType,
    User,
)
from mkopo.services.audit import Actor, record
from mkopo.services.ingest import documents_for_loan, embed_document
from mkopo.services.pdf_render import filename_for_pdf, render_text_to_pdf
from mkopo.services.storage import get_storage

logger = structlog.get_logger()


@dataclass(frozen=True)
class SeedDoc:
    filename: str
    doc_type: DocumentType
    text: str


@dataclass(frozen=True)
class SeedParty:
    name: str
    party_type: PartyType
    role: PartyRole
    email: str | None = None


@dataclass(frozen=True)
class SeedLoan:
    loan_type: LoanType
    amount_usd: Decimal
    borrower_email: str
    parties: list[SeedParty]
    documents: list[SeedDoc]
    # Default new loans land in INTAKE (the natural lifecycle entry
    # point). Some fixtures override this to UNDERWRITING so a fresh
    # `seed` gives the pipeline a realistic mix of stages without the
    # demo'er having to manually advance each one.
    starting_stage: LoanStage = LoanStage.INTAKE
    # Top-level product class. Most fixtures are commercial real-estate
    # deals (BUSINESS) — the personal-loan fixture overrides this and
    # also supplies the income / DTI / FICO / tenure meta so the
    # personal rule pack has values to evaluate against. None means
    # "fall through to the Loan model's default", which is BUSINESS.
    loan_class: LoanClass | None = None
    # Free-form loan metadata. The borrower portal writes
    # ``annual_income`` / ``monthly_debt_payments`` / ``credit_score`` /
    # ``years_employment`` here for personal loans (see
    # services/rules_eval.py); seed fixtures mirror that so the
    # rules engine can run against seeded data without a borrower
    # round-trip. ``borrower_email`` is always written; this dict is
    # merged on top.
    meta_extra: dict | None = None


# -----------------------------------------------------------------------------
# Synthetic loan fixtures. Mixed property types + geographies so comparable-
# loans search has variety. Each loan has just enough realistic detail (NOI,
# guarantors, appraisal date) for intake/underwriting to demo end-to-end.
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# Phase-2 fixtures: span the asset-type policy table (hotel, mixed-use,
# self-storage → "other"), the loan-type enum (construction), the stage
# lifecycle (decision / conditions / closing / declined), and the
# guarantor concentration story. Matthew Chen guarantees ATLAS + CEDAR;
# adding him to MERIDIAN pushes total exposure over the $8M cap so the
# concentration rule fires on the underwriting workspace and entity
# inspector.
# -----------------------------------------------------------------------------

MERIDIAN = SeedLoan(
    loan_type=LoanType.BRIDGE,
    amount_usd=Decimal("4200000.00"),
    borrower_email="cfo@meridianhospitality.example",
    # Sits at DECISION so the credit-decision panel has a live target.
    starting_stage=LoanStage.DECISION,
    parties=[
        SeedParty(
            name="Meridian Hospitality LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="cfo@meridianhospitality.example",
        ),
        # Reuse Matthew Chen so he ends up as a guarantor on three
        # active loans — pushes his exposure to $8.45M, just past the
        # $8M policy cap, triggering rule_guarantor_concentration.
        SeedParty(
            name="Matthew Chen",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="matthew@atlasholdings.example",
        ),
        SeedParty(
            name="Priya Iyer",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="priya@meridianhospitality.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — COMMERCIAL BRIDGE FACILITY
Submitted: February 06, 2026

1. BORROWER INFORMATION
Legal Name: Meridian Hospitality, LLC
State of Formation: Arizona
Business Address: 2440 East Camelback Rd, Phoenix, AZ 85016

2. GUARANTORS
- Matthew Chen
- Priya Iyer

3. LOAN REQUEST
Amount: $4,200,000
Purpose: PIP (property improvement plan) capex + reflag
Loan Type: Bridge, 30 months interest-only

4. ASSET
110-key limited-service hotel, currently flagged as a national midscale brand.
Borrower plans to convert to upscale-select-service flag after PIP.
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE — HOSPITALITY ASSET

Subject is a 110-key limited-service hotel located at 2440 East Camelback Rd,
Phoenix, Arizona. Constructed 2003, last renovated 2014. STR competitive set
RevPAR $94.20 TTM; subject RevPAR $86.10 TTM (indexed 91.4%).

TTM operating performance:
Rooms revenue:                  $3,455,000
Other income (F&B, parking):      $182,400
Total revenue:                  $3,637,400
Less: departmental expenses:    $1,164,800
Less: undistributed expenses:     $691,200
Less: fixed charges:              $327,400
NET OPERATING INCOME (NOI):     $1,454,000

Stabilized projection assumes RevPAR returns to competitive parity post-PIP:
underwritten stabilized NOI of $1,620,000.

Appraisal Date: January 22, 2026
Appraised Value: $6,470,000 (8.0% cap rate on stabilized NOI)
""",
        ),
        SeedDoc(
            filename="pip_scope.txt",
            doc_type=DocumentType.OTHER,
            text="""\
PROPERTY IMPROVEMENT PLAN (PIP) — SUMMARY

Total PIP budget: $2,800,000
Reflag timeline: 18 months from closing
Key components: guestroom soft-goods refresh (110 keys × $9,800), lobby renovation,
porte-cochère replacement, pool deck rebuild, exterior signage, life-safety upgrades.

Brand standard compliance letter from upscale-select-service flag dated Jan 12, 2026
attached as Exhibit C.
""",
        ),
    ],
)


PORTSIDE = SeedLoan(
    loan_type=LoanType.CONSTRUCTION,
    amount_usd=Decimal("7500000.00"),
    borrower_email="dev@portsidelogistics.example",
    starting_stage=LoanStage.UNDERWRITING,
    parties=[
        SeedParty(
            name="Portside Logistics Partners LP",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="dev@portsidelogistics.example",
        ),
        SeedParty(
            name="Carlos Mendoza",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="carlos@portsidelogistics.example",
        ),
        SeedParty(
            name="Lin Wei",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="lin@portsidelogistics.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — CONSTRUCTION FACILITY
Submitted: April 22, 2026

1. BORROWER INFORMATION
Legal Name: Portside Logistics Partners, LP
State of Formation: California
Business Address: 1100 East Wardlow Rd, Long Beach, CA 90807

2. GUARANTORS
- Carlos Mendoza (general partner, 51%)
- Lin Wei (limited partner with completion guaranty, 49%)

3. LOAN REQUEST
Amount: $7,500,000
Purpose: Ground-up construction financing
Loan Type: Construction, 24-month draw + 6-month mini-perm option

4. PROJECT
98,000 SF Class-A distribution facility, port-proximate, near
Long Beach Container Terminal. Pre-leased 65% (2 tenants).
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE — INDUSTRIAL (AS-COMPLETE)

Subject is a planned 98,000-SF Class-A industrial distribution building
at 1100 East Wardlow Rd, Long Beach, California. 32' clear height,
8 dock-high doors per 10,000 SF, ESFR sprinkler.

Stabilized projection (year 2):
Total rental income:            $1,372,000  ($14.00/SF NNN)
Less: operating expenses:          $98,000  (reimbursed via NNN)
Net operating income (NOI):     $1,274,000

LBC port-proximate industrial market vacancy 3.1% (CBRE Q1 2026).
Comparable Class-A leases: $13.50-$15.20/SF NNN, 5-7 year term.

Appraisal Date: April 09, 2026
Appraised Value (as-complete): $11,580,000 (11.0% cap)
""",
        ),
        SeedDoc(
            filename="leasing_status.txt",
            doc_type=DocumentType.OTHER,
            text="""\
PRE-LEASING STATUS

Tenant 1: Coastal Freight Forwarding (signed LOI, 38,200 SF, 7-year)
Tenant 2: Pacific Cold Logistics (executed lease, 25,500 SF, 10-year)
Total pre-leased: 63,700 SF / 98,000 SF (65.0%)
Remaining: 34,300 SF marketed at $14.50/SF NNN.
""",
        ),
    ],
)


LAKESIDE = SeedLoan(
    loan_type=LoanType.PERMANENT,
    amount_usd=Decimal("6500000.00"),
    borrower_email="ir@lakesidetowers.example",
    # Closing stage so the case file shows a deal in document
    # finalization — pairs nicely with MERIDIAN at decision and
    # HALCYON post-approval.
    starting_stage=LoanStage.CLOSING,
    parties=[
        SeedParty(
            name="Lakeside Towers REIT",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="ir@lakesidetowers.example",
        ),
        SeedParty(
            name="Ana Reyes",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="ana@lakesidetowers.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — PERMANENT MORTGAGE
Submitted: February 24, 2026

1. BORROWER INFORMATION
Legal Name: Lakeside Towers REIT
State of Formation: Maryland
Business Address: 1480 Cherry Creek Drive S, Denver, CO 80246

2. GUARANTORS
- Ana Reyes (REIT principal, recourse guaranty)

3. LOAN REQUEST
Amount: $6,500,000
Purpose: Permanent take-out of construction loan
Loan Type: Permanent, 10-year fixed, 30-year amortization
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE — MIXED-USE

Subject is an 88-unit residential building with 8,400 SF of ground-floor
retail at 1480 Cherry Creek Drive S, Denver, Colorado. Completed 2024,
stabilized at 94% residential occupancy and 100% retail.

Residential annual income:        $2,142,000  (88 units × ~$2,028/mo avg)
Retail annual income:               $268,800  (8,400 SF × $32 NNN avg)
Total annual income:              $2,410,800
Less: operating expenses:           $662,800
Less: vacancy / collection loss:    $124,800
Net operating income (NOI):       $1,623,200

Capitol Hill / Cherry Creek market vacancy 4.8% (residential), 6.2% (retail).

Appraisal Date: February 14, 2026
Appraised Value: $9,548,000 (17.0% mixed-use blended cap rate weighting)
""",
        ),
        SeedDoc(
            filename="rent_roll.txt",
            doc_type=DocumentType.RENT_ROLL,
            text="""\
RENT ROLL — Lakeside Towers (88 units + 4 retail bays)
As of: April 30, 2026

Studio units (12):   avg $1,640/mo, 100% occupied
1BR units (44):      avg $1,920/mo, 95.5% occupied (2 vacant)
2BR units (32):      avg $2,420/mo, 93.8% occupied (2 vacant)
Retail bay 1 (2,100 SF): Caribou Coffee, $34/SF NNN, lease through 2029
Retail bay 2 (2,400 SF): Hapa Sushi, $30/SF NNN, lease through 2031
Retail bay 3 (1,800 SF): Stem & Bloom Florist, $32/SF NNN, lease through 2027
Retail bay 4 (2,100 SF): Cherry Creek Cleaners, $30/SF NNN, lease through 2028
""",
        ),
    ],
)


SUNRISE = SeedLoan(
    loan_type=LoanType.REFINANCE,
    amount_usd=Decimal("2800000.00"),
    borrower_email="ops@sunrisestorage.example",
    # Conditions stage — the underwriter has issued conditional approval
    # and is awaiting borrower delivery of the remaining items.
    starting_stage=LoanStage.CONDITIONS,
    parties=[
        SeedParty(
            name="Sunrise Self-Storage Holdings LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="ops@sunrisestorage.example",
        ),
        SeedParty(
            name="Diego Marquez",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="diego@sunrisestorage.example",
        ),
        SeedParty(
            name="Beatriz Marquez",
            party_type=PartyType.PERSON,
            role=PartyRole.GUARANTOR,
            email="beatriz@sunrisestorage.example",
        ),
    ],
    documents=[
        SeedDoc(
            filename="loan_application.txt",
            doc_type=DocumentType.LOAN_APPLICATION,
            text="""\
LOAN APPLICATION — REFINANCE
Submitted: January 30, 2026

1. BORROWER INFORMATION
Legal Name: Sunrise Self-Storage Holdings, LLC
State of Formation: Arizona
Business Address: 6740 West Cactus Rd, Glendale, AZ 85304

2. GUARANTORS
- Diego Marquez (50%)
- Beatriz Marquez (50%)

3. LOAN REQUEST
Amount: $2,800,000
Purpose: Refinance maturing construction-to-perm loan
Loan Type: Permanent refinance, 7-year fixed, 25-year amortization
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE — SELF-STORAGE

Subject is a 540-unit self-storage facility (97,200 net rentable SF) at
6740 West Cactus Rd, Glendale, Arizona. Constructed 2017, climate-controlled
85% of units. Current physical occupancy 91%, economic occupancy 88%.

Total annual rental income:       $478,400  ($4.92/SF blended)
Late fees + retail (locks, etc.):  $34,200
Less: operating expenses:         $128,400
Net operating income (NOI):       $384,200

Glendale self-storage submarket: 9.4% vacancy (Inside Self-Storage Q1 2026);
subject outperforms market by 320 bps.

Appraisal Date: December 18, 2025
Appraised Value: $4,517,600 (8.5% cap)
""",
        ),
    ],
)


HIGHLAND = SeedLoan(
    loan_type=LoanType.BRIDGE,
    amount_usd=Decimal("2200000.00"),
    borrower_email="manager@highlandcenters.example",
    # Declined stage — debt yield 6.6% fails the 8.0% retail-bridge
    # floor, which we want the eval narrative + adverse-action letter
    # demo to be able to point at.
    starting_stage=LoanStage.DECLINED,
    parties=[
        SeedParty(
            name="Highland Centers LLC",
            party_type=PartyType.ENTITY,
            role=PartyRole.BORROWER,
            email="manager@highlandcenters.example",
        ),
        # Jane Park already guarantees ATLAS + HALCYON. Adding her to
        # HIGHLAND gives the entity inspector a "guarantor on a declined
        # deal" data point — useful for showing how the relationship
        # graph spans the lifecycle, not just live exposure.
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
Submitted: November 14, 2025

1. BORROWER INFORMATION
Legal Name: Highland Centers, LLC
State of Formation: Nevada
Business Address: 8970 West Tropicana Ave, Las Vegas, NV 89147

2. GUARANTORS
- Jane Park

3. LOAN REQUEST
Amount: $2,200,000
Purpose: Working capital + tenant improvement allowance
Loan Type: Bridge, 24 months interest-only
""",
        ),
        SeedDoc(
            filename="appraisal_report.txt",
            doc_type=DocumentType.APPRAISAL,
            text="""\
INCOME APPROACH TO VALUE

Subject is a 22,400-SF unanchored retail strip at 8970 West Tropicana Ave,
Las Vegas, Nevada. Constructed 1994. Current occupancy 64%; trailing 24-month
average occupancy 67%. Two leases roll within the next 9 months.

Total annual rental income:        $214,800
Less: operating expenses:           $69,200
Net operating income (NOI):        $145,600

West Las Vegas unanchored retail submarket: 12.8% vacancy (CoStar Q4 2025).
Submarket trending negative on absorption for six consecutive quarters.

Appraisal Date: October 30, 2025
Appraised Value: $3,028,000 (4.8% cap reflecting market risk premium)
""",
        ),
        SeedDoc(
            filename="adverse_action_summary.txt",
            doc_type=DocumentType.OTHER,
            text="""\
ADVERSE ACTION SUMMARY — internal note

Decision: DECLINE
Principal reasons (ECOA Reg B 12 CFR §1002.9(b)(2)):
1. Debt yield 6.6% — below 8.0% policy floor for retail bridge facilities.
2. Property occupancy 64% with negative submarket absorption trend.
3. DSCR uncertainty: forward rents subject to two near-term lease rollovers.

Adverse action letter sent: November 22, 2025.
""",
        ),
    ],
)


# -----------------------------------------------------------------------------
# Personal-loan fixture. Lives outside the commercial real-estate cluster
# above because it exercises an entirely different rule pack (DTI / FICO /
# LTI / employment tenure) and a different required-doc set (tax return +
# bank statements + PFS + application — no appraisal or rent roll).
#
# Demo value: lets you click into intake/underwriting for a consumer loan
# and see the personal flow end-to-end — class-aware extraction, the
# correct missing-docs message, the personal rule pack with real values
# to evaluate. Without a seeded personal loan, every demo question about
# "does this work for personal too?" requires a fresh borrower signup.
# -----------------------------------------------------------------------------

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


LOANS_TO_SEED = [
    ATLAS,
    CEDAR,
    BAYLINE,
    NORTHGATE,
    HALCYON,
    MERIDIAN,
    PORTSIDE,
    LAKESIDE,
    SUNRISE,
    HIGHLAND,
    KAYA,
]


def _doc_title_for(doc: SeedDoc) -> str:
    """PDF title block for a seeded document.

    Falls through the registered doc-type names ("Loan Application",
    "Appraisal Report", …) with a fallback to a title-cased
    filename for one-off fixtures (``pip_scope.txt`` →
    ``"Pip Scope"``). The result lands as both the visible header
    on every PDF page and the embedded PDF title metadata.
    """
    by_type = {
        DocumentType.LOAN_APPLICATION: "Loan Application",
        DocumentType.APPRAISAL: "Appraisal Report",
        DocumentType.RENT_ROLL: "Rent Roll",
        DocumentType.PERSONAL_FINANCIAL_STATEMENT: "Personal Financial Statement",
        DocumentType.BANK_STATEMENT: "Bank Statement",
        DocumentType.TAX_RETURN: "Tax Return",
        DocumentType.INSURANCE: "Certificate of Insurance",
        DocumentType.TITLE_REPORT: "Title Report",
    }
    if doc.doc_type in by_type:
        return by_type[doc.doc_type]
    stem = doc.filename.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
    return stem.title()


async def _upsert_party(session, sp: SeedParty) -> Party:
    """Find-or-create a Party by (name + role-driven type)."""
    existing = await session.execute(select(Party).where(Party.name == sp.name))
    p = existing.scalars().first()
    if p:
        return p
    p = Party(name=sp.name, party_type=sp.party_type, email=sp.email)
    session.add(p)
    await session.flush()
    return p


async def _create_loan(session, davis: User, fixture: SeedLoan) -> Loan:
    meta: dict = {"borrower_email": fixture.borrower_email}
    if fixture.meta_extra:
        # Personal-loan fixtures land their DTI / FICO / income fields
        # here so the rules engine sees the same shape the borrower
        # portal would write. String values match how the portal posts
        # them (form-encoded → all strings) so rules_eval's _to_decimal
        # / _to_int parse paths get exercised by seeded data too.
        meta.update(fixture.meta_extra)
    loan_kwargs: dict = {
        "loan_type": fixture.loan_type,
        "amount": fixture.amount_usd,
        "owner_user_id": davis.id,
        "stage": fixture.starting_stage,
        "meta": meta,
    }
    if fixture.loan_class is not None:
        loan_kwargs["loan_class"] = fixture.loan_class
    loan = Loan(**loan_kwargs)
    session.add(loan)
    await session.flush()
    await session.refresh(loan)

    for sp in fixture.parties:
        party = await _upsert_party(session, sp)
        session.add(LoanParty(loan_id=loan.id, party_id=party.id, role=sp.role))

    # Render each seeded document into a real PDF, push it through
    # the configured storage backend (local FS or S3 depending on
    # env), and store the storage URI on the Document row.
    #
    # Why a PDF and not the raw text fixture: the intake agent's
    # extractor runs pypdf on real bytes; reading a "text/plain"
    # Document with text in ``meta.text_content`` shortcuts that
    # entire code path and means a fresh seed never actually
    # exercises the PDF extraction the production flow depends on.
    # Rendering at seed time means an underwriter clicking "Extract
    # documents" on a seeded loan runs the same pypdf round-trip
    # they'd run on a borrower upload.
    #
    # The materials hash also wants stable content_hash values per
    # document; sha256(pdf_bytes) is what we want, so we compute it
    # alongside the upload.
    import hashlib

    storage = get_storage()
    for doc in fixture.documents:
        pdf_filename = filename_for_pdf(doc.filename)
        # Title is the human-friendly doc-type name + the borrower
        # reference; gives the in-app PDF viewer a useful tab name.
        title = _doc_title_for(doc)
        body_text = doc.text
        pdf_bytes = render_text_to_pdf(
            title=title,
            body=body_text,
            footer_ref=f"{loan.reference} · {doc.doc_type.value}",
        )
        uri = await storage.put_object(
            loan_id=loan.id,
            filename=pdf_filename,
            body=pdf_bytes,
            content_type="application/pdf",
        )
        # Keep ``text_content`` in meta so the agents' fast path
        # (read text without re-running pypdf) still works for the
        # already-extracted intake summary. Production uploads go
        # through ``routers/documents.py`` which extracts via pypdf
        # and writes the same field; the seed pre-populates so
        # ad-hoc Q&A / RAG queries work immediately on seeded loans.
        d = Document(
            loan_id=loan.id,
            filename=pdf_filename,
            doc_type=doc.doc_type,
            storage_uri=uri,
            content_type="application/pdf",
            size_bytes=len(pdf_bytes),
            content_hash=hashlib.sha256(pdf_bytes).hexdigest(),
            meta={
                "text_content": body_text,
                "extract": {
                    "char_count": len(body_text),
                    "method": "seed_text_to_pdf",
                },
            },
        )
        session.add(d)

    await session.flush()

    # Chunk + embed every seeded document so "Ask the file" has corpus.
    for d in await documents_for_loan(session, loan.id):
        await embed_document(session, d)

    await record(
        session,
        loan_id=loan.id,
        actor=Actor.system(),
        action="seed_loan_created",
        payload={"source": "seed_script"},
    )

    # If the fixture asks for a non-default starting stage, write a
    # stage_changed audit event so the case-file timeline shows the
    # transition. We don't replay each intermediate stage — one event
    # from intake → starting_stage is a fine abbreviation for seed data.
    if fixture.starting_stage != LoanStage.INTAKE:
        await record(
            session,
            loan_id=loan.id,
            actor=Actor.system(),
            action="stage_changed",
            payload={
                "from": LoanStage.INTAKE.value,
                "to": fixture.starting_stage.value,
                "source": "seed_script",
            },
        )
    return loan


async def _reset_demo_data(session) -> None:
    """Wipe loan-scoped demo data + reset the LN-YYYY-NNNN sequence.

    Loans cascade to documents → extractions → review_tasks → audit
    events → conditions → messages → loan_parties. We also truncate
    ``parties`` since older seed runs created duplicates by name
    (before ``_upsert_party`` enforced uniqueness) and those would
    confuse the entity inspector.

    ``users`` is intentionally NOT truncated — the upserter handles
    re-use of Jordan Davis, and a real install might have other
    underwriter users we don't want to nuke.

    Also clears ``task_runs`` / ``llm_calls`` / ``annotations`` so
    the eval dashboard starts from a clean baseline matching the new
    seed. ``CASCADE`` on llm_calls is required because ``tool_uses``
    (0014) and ``llm_calls.parent_step_id`` (0018) FK-reference it;
    plain ``TRUNCATE`` fails with "cannot truncate a table referenced
    in a foreign key constraint" otherwise.

    ``annotations`` is independent of loans (polymorphic pointers
    rather than FK), so it doesn't get cascaded by the loans wipe —
    truncate it directly so leftover verdicts don't dangle pointing
    at stale agent_run / llm_call ids.
    """
    # CASCADE on loans → documents/extractions/review_tasks/audit_events/
    # conditions/messages/loan_parties/agent_runs/agent_steps.
    await session.execute(text("TRUNCATE TABLE loans CASCADE"))
    await session.execute(text("TRUNCATE TABLE parties CASCADE"))
    await session.execute(text("TRUNCATE TABLE task_runs"))
    # CASCADE here — tool_uses (0014) and llm_calls.parent_step_id
    # FK-reference llm_calls, and annotations.target_id may point at
    # rows we're about to wipe. CASCADE clears the dependents in one
    # statement instead of forcing the order.
    await session.execute(text("TRUNCATE TABLE llm_calls CASCADE"))
    # Annotations are polymorphic (target_kind + target_id, not FK)
    # so the cascade above doesn't reach them. Clear separately to
    # avoid orphan verdicts pointing at now-gone agent_runs.
    await session.execute(text("TRUNCATE TABLE annotations"))
    # `loans.reference` uses a standalone sequence, so TRUNCATE on the
    # owning table doesn't reset it. Restart explicitly so a fresh
    # seed always begins at LN-YYYY-1001.
    await session.execute(text("ALTER SEQUENCE loan_reference_seq RESTART WITH 1001"))


async def seed(reset: bool = False) -> None:
    async with get_session() as session:
        if reset:
            print("⚠️  Truncating loans + task_runs + llm_calls (CASCADE)…")
            await _reset_demo_data(session)
            print("   …done. LN-YYYY-NNNN sequence reset to 1001.\n")

        existing = await session.execute(select(User).where(User.email == "j.davis@mkopo.dev"))
        davis = existing.scalar_one_or_none()
        if davis is None:
            davis = User(name="Jordan Davis", email="j.davis@mkopo.dev", role="underwriter")
            session.add(davis)
            await session.flush()

        created: list[Loan] = []
        for fixture in LOANS_TO_SEED:
            loan = await _create_loan(session, davis, fixture)
            created.append(loan)
            print(
                f"   {loan.reference}  {fixture.parties[0].name:33s}"
                f"  ${float(loan.amount):>12,.0f}  {fixture.loan_type.value:13s}"
                f"  stage={loan.stage.value}"
            )

    logger.info(
        "seed_complete",
        n_loans=len(created),
        references=[loan.reference for loan in created],
    )
    print(f"\n✅ Seeded {len(created)} loan(s); owner: {davis.name}")
    print(
        "   Documents auto-chunked + embedded so comparable-loans search has corpus.\n"
        "   Run the intake + underwriting agents on each loan to populate "
        "loans.embedding for kNN.\n"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Mkopo demo data.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Truncate loans / task_runs / llm_calls and reset the "
            "LN-YYYY-NNNN sequence before seeding. Destructive but "
            "gives a clean, deterministic demo state."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(seed(reset=args.reset))
