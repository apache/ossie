from dataclasses import dataclass
from enum import Enum
from typing import Generic, List, TypeVar


class ConverterIssueType(Enum):
    """Identifies the kind of information loss that occurred during conversion."""

    CONVERSION_METRIC_DROPPED = "CONVERSION_METRIC_DROPPED"
    PRIVATE_METRIC_DROPPED = "PRIVATE_METRIC_DROPPED"
    NATURAL_ENTITY_DROPPED = "NATURAL_ENTITY_DROPPED"
    CUMULATIVE_SEMANTICS_LOSS = "CUMULATIVE_SEMANTICS_LOSS"


@dataclass(frozen=True)
class ConverterIssue:
    """Records a single instance of information loss during conversion."""

    issue_type: ConverterIssueType
    element_name: str


T = TypeVar("T")


@dataclass(frozen=True)
class ConverterResult(Generic[T]):
    """Return value of a converter's convert() method, pairing the output with any conversion issues."""

    output: T
    issues: List[ConverterIssue]
