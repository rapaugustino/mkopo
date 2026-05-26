"""ORM models. Importing this module registers all models with Base.metadata."""

from mkopo.models.annotation import (
    Annotation,
    AnnotationTargetKind,
    AnnotationVerdict,
)
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
from mkopo.models.errors import InfrastructureError
from mkopo.models.eval import LLMCall, TaskRun, ToolUse
from mkopo.models.injection import (
    InjectionDecision,
    InjectionDetection,
    InjectionSeverity,
    InjectionSourceKind,
)
from mkopo.models.institution import SINGLETON_ID as INSTITUTION_SINGLETON_ID
from mkopo.models.institution import InstitutionSettings
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
from mkopo.models.prompt import Prompt
from mkopo.models.user import User

__all__ = [
    "ActorType",
    "AgentRun",
    "AgentStep",
    "Annotation",
    "AnnotationTargetKind",
    "AnnotationVerdict",
    "AuditEvent",
    "AutonomyLevel",
    "Base",
    "Condition",
    "ConditionStatus",
    "Document",
    "DocumentChunk",
    "DocumentType",
    "Extraction",
    "ExtractionStatus",
    "INSTITUTION_SINGLETON_ID",
    "InfrastructureError",
    "InjectionDecision",
    "InjectionDetection",
    "InjectionSeverity",
    "InjectionSourceKind",
    "InstitutionSettings",
    "LLMCall",
    "Loan",
    "LoanClass",
    "LoanParty",
    "LoanStage",
    "LoanType",
    "MagicLink",
    "Message",
    "MessageDirection",
    "Party",
    "PartyRole",
    "PartyType",
    "Prompt",
    "ReviewTask",
    "TaskRun",
    "ToolUse",
    "User",
    "VALID_TRANSITIONS",
]
