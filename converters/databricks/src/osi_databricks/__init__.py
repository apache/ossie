"""Bidirectional converter between OSI semantic models and Databricks Unity Catalog
Metric Views (YAML v1.1). Pure offline string-in / string-out transforms.

    from osi_databricks import convert_osi_to_metric_view, convert_metric_view_to_osi
"""

from ._common import ConversionError
from .metric_view_to_osi import convert_metric_view_to_osi
from .osi_to_metric_view import convert_osi_to_metric_view

__all__ = [
    "ConversionError",
    "convert_metric_view_to_osi",
    "convert_osi_to_metric_view",
]
