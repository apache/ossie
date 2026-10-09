from ossie_clickhouse.dialect import ClickHouseDialectMapper


def test_clickhouse_aggregates():
    mapper = ClickHouseDialectMapper()
    assert mapper.map_aggregate("count_distinct", "user_id") == "uniqExact(user_id)"
    assert mapper.map_aggregate("approx_distinct", "user_id") == "uniqCombined64(user_id)"
    assert mapper.map_aggregate("median", "latency") == "quantileExact(0.5)(latency)"
    assert mapper.map_aggregate("percentile", "latency", level=0.95) == "quantileExact(0.95)(latency)"
    assert mapper.map_aggregate("sum", "amount") == "sum(amount)"
    assert mapper.map_aggregate("avg", "amount") == "avg(amount)"
    assert mapper.map_aggregate("min", "amount") == "min(amount)"
    assert mapper.map_aggregate("max", "amount") == "max(amount)"
    assert mapper.map_aggregate("count", "id") == "count(id)"
    # Fallback test
    assert mapper.map_aggregate("custom_agg", "col") == "custom_agg(col)"


def test_clickhouse_time_grains():
    mapper = ClickHouseDialectMapper()
    assert mapper.map_time_grain("created_at", "day") == "toStartOfDay(created_at)"
    assert mapper.map_time_grain("created_at", "week") == "toStartOfWeek(created_at, 1)"
    assert mapper.map_time_grain("created_at", "month") == "toStartOfMonth(created_at)"
    assert mapper.map_time_grain("created_at", "quarter") == "toStartOfQuarter(created_at)"
    assert mapper.map_time_grain("created_at", "year") == "toStartOfYear(created_at)"
    # Fallback test
    assert mapper.map_time_grain("created_at", "unknown_grain") == "toStartOfDay(created_at)"


def test_safe_division():
    mapper = ClickHouseDialectMapper()
    assert mapper.safe_divide("revenue", "orders") == "(revenue) / nullIf(orders, 0)"
