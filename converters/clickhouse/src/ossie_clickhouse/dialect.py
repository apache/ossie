class ClickHouseDialectMapper:
    """ClickHouse specific dialect mapper for Ossie semantic metrics."""

    def map_aggregate(self, metric_type: str, column: str, level: float = 0.5) -> str:
        """Map generic aggregate metrics to native ClickHouse combinator functions."""
        aggregates = {
            "count_distinct": f"uniqExact({column})",
            "approx_distinct": f"uniqCombined64({column})",
            "median": f"quantileExactInclusive(0.5)({column})",
            "percentile": f"quantileExactInclusive({level})({column})",
            "sum": f"sum({column})",
            "avg": f"avg({column})",
            "min": f"min({column})",
            "max": f"max({column})",
            "count": f"count({column})",
        }
        return aggregates.get(metric_type, f"{metric_type}({column})")

    def map_time_grain(self, column: str, grain: str) -> str:
        """Map standard time grains to ClickHouse toStartOf* functions."""
        grains = {
            "day": f"toStartOfDay({column})",
            "week": f"toStartOfWeek({column}, 1)",
            "month": f"toStartOfMonth({column})",
            "quarter": f"toStartOfQuarter({column})",
            "year": f"toStartOfYear({column})",
        }
        return grains.get(grain, f"toStartOfDay({column})")

    def safe_divide(self, numerator: str, denominator: str) -> str:
        """Wrap division denominator with nullIf to prevent division-by-zero exceptions."""
        return f"({numerator}) / nullIf({denominator}, 0)"
