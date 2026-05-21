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
]


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
    loan = Loan(
        loan_type=fixture.loan_type,
        amount=fixture.amount_usd,
        owner_user_id=davis.id,
        stage=fixture.starting_stage,
        meta={"borrower_email": fixture.borrower_email},
    )
    session.add(loan)
    await session.flush()
    await session.refresh(loan)

    for sp in fixture.parties:
        party = await _upsert_party(session, sp)
        session.add(LoanParty(loan_id=loan.id, party_id=party.id, role=sp.role))

    for doc in fixture.documents:
        d = Document(
            loan_id=loan.id,
            filename=doc.filename,
            doc_type=doc.doc_type,
            storage_uri=f"seed://{doc.filename}",
            content_type="text/plain",
            size_bytes=len(doc.text),
            meta={"text_content": doc.text},
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

    Also clears ``task_runs`` / ``llm_calls`` so the eval dashboard
    starts from a clean baseline matching the new seed.
    """
    # CASCADE on parties → loan_parties (already gone via loans), and
    # also takes care of any non-loan FK references that future
    # features might add.
    await session.execute(text("TRUNCATE TABLE loans CASCADE"))
    await session.execute(text("TRUNCATE TABLE parties CASCADE"))
    await session.execute(text("TRUNCATE TABLE task_runs"))
    await session.execute(text("TRUNCATE TABLE llm_calls"))
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
