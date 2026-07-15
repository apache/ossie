"""Bidirectional converter between OSI semantic models and Omni semantic model
files (model.yaml / relationships.yaml / views/*.view.yaml / topics/*.topic.yaml).
Pure offline transforms: OSI YAML string <-> {relative filename: YAML string}.

    from osi_omni import convert_osi_to_omni, convert_omni_to_osi
"""

from ._common import ConversionError
from .omni_to_osi import convert_omni_to_osi
from .osi_to_omni import convert_osi_to_omni

__all__ = [
    "ConversionError",
    "convert_omni_to_osi",
    "convert_osi_to_omni",
]
