"""ORM models. Importing this module registers all models with Base.metadata."""

from mkopo.models.audit import (
    ActorType,
    AuditEvent,
    Condition,
    ConditionStatus,
    Message,
    MessageDirection,
)
from mkopo.models.base import Base
from mkopo.models.document import (
    Document,
    DocumentType,
    Extraction,
    ExtractionStatus,
    ReviewTask,
)
from mkopo.models.embedding import DocumentChunk
from mkopo.models.eval import LLMCall, TaskRun
from mkopo.models.loan import (
    VALID_TRANSITIONS,
    AgentRun,
    AgentStep,
    AutonomyLevel,
    Loan,
    LoanClass,
    LoanStage,
    LoanType,
)
from mkopo.models.magic_link import MagicLink
from mkopo.models.party import LoanParty, Party, PartyRole, PartyType
from mkopo.models.user import User

__all__ = [
    "ActorType",
    "AgentRun",
    "AgentStep",
    "AuditEvent",
    "Base",
    "Condition",
    "ConditionStatus",
    "Document",
    "DocumentChunk",
    "DocumentType",
    "Extraction",
    "ExtractionStatus",
    "LLMCall",
    "Loan",
    "LoanParty",
    "LoanStage",
    "LoanType",
    "MagicLink",
    "Message",
    "MessageDirection",
    "Party",
    "PartyRole",
    "PartyType",
    "ReviewTask",
    "TaskRun",
    "User",
    "VALID_TRANSITIONS",
]
