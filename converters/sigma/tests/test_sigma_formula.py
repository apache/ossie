import pytest

from ossie_sigma.sigma_formula import (
    ColumnRef,
    FormulaParseError,
    is_plain_column_ref,
    parse_formula,
    sql_to_sigma_formula,
    to_ansi_sql,
    to_sql,
)


@pytest.mark.parametrize(
    ("formula", "dataset_alias", "expected_sql"),
    [
        ("[Amount]", "Orders", '"Amount"'),
        ("[Orders/Amount]", "Orders", '"Amount"'),
        ("[Orders/Amount]", None, '"Orders"."Amount"'),
        ("Sum([Amount])", "Orders", 'SUM("Amount")'),
        ("CountDistinct([Order Id])", "Orders", 'COUNT(DISTINCT "Order Id")'),
        ('If([Status] = "closed", 1, 0)', "Orders", "CASE WHEN \"Status\" = 'closed' THEN 1 ELSE 0 END"),
        ("IfNull([X], 0)", "Orders", 'COALESCE("X", 0)'),
        ("IsNull([X])", "Orders", '"X" IS NULL'),
        ("IsNotNull([X])", "Orders", 'NOT "X" IS NULL'),
        ('[A] & " " & [B]', "T", "\"A\" || ' ' || \"B\""),
        ("Left([Name], 3)", "T", 'SUBSTRING("Name", 1, 3)'),
        ("Right([Name], 3)", "T", 'SUBSTRING("Name", LENGTH("Name") - 3 + 1, 3)'),
        ("Mid([Name], 2, 3)", "T", 'SUBSTRING("Name", 2, 3)'),
        ("Year([Created At])", "T", 'EXTRACT(YEAR FROM "Created At")'),
        ("Upper(Trim([Name]))", "T", 'UPPER(TRIM("Name"))'),
        ("[Qty] * [Price] + 1", "T", '"Qty" * "Price" + 1'),
        ("([Qty] + 1) * [Price]", "T", '("Qty" + 1) * "Price"'),
        ("Median([Amount])", "T", 'PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "Amount")'),
        ('SumIf([Status] = "won", [Amount])', "T", "SUM(CASE WHEN \"Status\" = 'won' THEN \"Amount\" ELSE 0 END)"),
        ("CountIf([Qty] > 1)", "T", 'COUNT(CASE WHEN "Qty" > 1 THEN 1 END)'),
        ('Contains([Name], "a")', "T", "\"Name\" LIKE '%' || 'a' || '%'"),
        ("Concat([A], [B])", "T", '"A" || "B"'),
        ("2 ^ 3", "T", "POWER(2, 3)"),
        ('DateAdd([D], 3, "day")', "T", "DATE_ADD(\"D\", 3, 'DAY')"),
        ('DateDiff([Start], [End], "month")', "T", 'DATEDIFF("End", "Start", MONTH)'),
        ("If([A] = [B], Null(), [A])", "T", 'CASE WHEN "A" = "B" THEN NULL ELSE "A" END'),
        ("Percentile([X], 0.75)", "T", 'PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY "X")'),
        ("StdDev([X])", "T", 'STDDEV_SAMP("X")'),
        ("-[X]", "T", '-"X"'),
        ("NOT [X]", "T", 'NOT "X"'),
        # An embedded quote must be escaped by sqlglot's generator, not by hand.
        ('[He said "hi"]', "T", '"He said ""hi"""'),
        ('If([X] = "it""s", 1, 0)', "T", "CASE WHEN \"X\" = 'it\"s' THEN 1 ELSE 0 END"),
    ],
)
def test_translatable_formulas(formula, dataset_alias, expected_sql):
    node = parse_formula(formula)
    assert to_ansi_sql(node, dataset_alias=dataset_alias) == expected_sql


def test_renders_to_a_warehouse_dialect_from_the_same_tree():
    """The sqlglot intermediate tree is what makes non-ANSI targets a one-liner."""
    node = parse_formula('If([Status] = "won", [Amount], 0)')
    assert to_sql(node, "Orders", dialect="snowflake") == (
        "CASE WHEN \"Status\" = 'won' THEN \"Amount\" ELSE 0 END"
    )
    assert to_sql(node, "Orders", dialect="bigquery") == (
        "CASE WHEN `Status` = 'won' THEN `Amount` ELSE 0 END"
    )


@pytest.mark.parametrize(
    "formula",
    [
        "RunningSum([Amount])",
        "Rank([Amount])",
        "SomeUnknownFunction([X])",
        # A date part Sigma accepts but SQL has no unit keyword for.
        'DateAdd([D], 1, "fortnight")',
        # Arities the mapping does not claim to cover.
        "Left([Name])",
        "If([A], 1)",
    ],
)
def test_untranslatable_functions_return_none(formula):
    node = parse_formula(formula)
    assert to_ansi_sql(node) is None


@pytest.mark.parametrize(
    "formula",
    [
        "",
        "[Unterminated",
        "Sum([X]",
        "@#$%",
    ],
)
def test_unparseable_formulas_raise(formula):
    with pytest.raises(FormulaParseError):
        parse_formula(formula)


def test_is_plain_column_ref():
    assert is_plain_column_ref("[Orders/Amount]") == ColumnRef("Orders", "Amount")
    assert is_plain_column_ref("[Amount]") == ColumnRef(None, "Amount")
    assert is_plain_column_ref("Sum([Amount])") is None
    assert is_plain_column_ref("not a formula @@@") is None


@pytest.mark.parametrize(
    ("sql", "dataset_alias", "expected"),
    [
        ('"Amount"', "Orders", "[Amount]"),
        ('"Orders"."Amount"', None, "[Orders/Amount]"),
        ("SUM(ss_ext_sales_price)", "store_sales", "Sum([ss_ext_sales_price])"),
        ("COUNT(DISTINCT customer_id)", "customer", "CountDistinct([customer_id])"),
        ("CASE WHEN status = 'won' THEN 1 ELSE 0 END", "deals", 'If((["status"] = "won"), 1, 0)'.replace('["status"]', "[status]")),
    ],
)
def test_reverse_translation_basic(sql, dataset_alias, expected):
    result = sql_to_sigma_formula(sql, dataset_alias=dataset_alias)
    assert result == expected


def test_reverse_translation_gives_up_on_count_star():
    assert sql_to_sigma_formula("COUNT(*)") is None


def test_reverse_translation_gives_up_on_unparseable():
    assert sql_to_sigma_formula("not valid sql {{{") is None
