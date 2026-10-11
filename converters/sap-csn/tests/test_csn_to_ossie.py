# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import copy

import pytest

from ossie_sap_csn import CsnToOssieConverter
from ossie_sap_csn.converter_issues import ConverterError, ConverterIssueType

from .helpers import BOOKS, ORDERS, by_name, load_fixture, sap_ext, sql_of, validate_against_schema


@pytest.fixture(scope="module")
def result():
    return CsnToOssieConverter().convert(load_fixture("bookshop.csn.json"), name="bookshop")


@pytest.fixture(scope="module")
def datasets(result):
    return by_name(result.output.datasets)


def issues_of(result, kind):
    return [i for i in result.issues if i.issue_type is kind]


def test_output_validates_against_the_core_schema(result):
    validate_against_schema(result.output)


def test_every_entity_becomes_a_dataset_named_without_its_namespace(datasets):
    assert set(datasets) == {"Authors", "Books", "Orders", "Items", "ListOfBooks"}
    assert datasets["Books"].source == BOOKS
    assert sap_ext(datasets["Books"])["entity"] == BOOKS


def test_colliding_short_names_fall_back_to_the_full_name():
    csn = {
        "definitions": {
            "a.Item": {"kind": "entity", "elements": {"ID": {"type": "cds.Integer", "key": True}}},
            "b.Item": {"kind": "entity", "elements": {"ID": {"type": "cds.Integer", "key": True}}},
        }
    }
    names = {d.name for d in CsnToOssieConverter().convert(csn).output.datasets}
    assert names == {"a_Item", "b_Item"}


def test_persistence_name_wins_over_the_cds_name_as_source():
    csn = {"definitions": {"my.E": {"kind": "entity", "@cds.persistence.name": "MY_E_TABLE", "elements": {}}}}
    assert CsnToOssieConverter().convert(csn).output.datasets[0].source == "MY_E_TABLE"


def test_keys_and_unique_constraints(datasets):
    assert datasets["Books"].primary_key == ["ID"]
    assert datasets["Books"].unique_keys == [["isbn"]]
    assert sap_ext(datasets["Books"])["unique_key_names"] == ["isbn"]


def test_labels_descriptions_and_doc_comments(datasets):
    books = by_name(datasets["Books"].fields)
    assert books["title"].label == "Title"
    assert books["title"].description == "Title as printed on the cover"
    assert datasets["Authors"].description == "People who wrote books."
    assert by_name(datasets["Authors"].fields)["name"].label == "Author name"


def test_datatypes_resolve_through_user_defined_types(datasets):
    books = by_name(datasets["Books"].fields)
    assert books["price"].datatype.value == "Decimal"  # sap.capire.bookshop.Amount -> cds.Decimal
    assert books["published"].datatype.value == "Date"
    assert books["isbn"].datatype.value == "String"
    assert by_name(datasets["Orders"].fields)["orderedAt"].datatype.value == "DateTime"
    assert by_name(datasets["Orders"].fields)["ID"].datatype.value == "String"  # cds.UUID


def test_binary_columns_are_opaque_and_keep_their_cds_type(result, datasets):
    cover = by_name(datasets["Books"].fields)["cover"]
    assert cover.datatype.value == "Opaque"
    assert sap_ext(cover)["type"] == "cds.LargeBinary"
    assert issues_of(result, ConverterIssueType.OPAQUE_DATATYPE) == []  # a known type, so nothing is lost


def test_unknown_type_is_opaque_and_reported():
    csn = {"definitions": {"E": {"kind": "entity", "elements": {"x": {"type": "vendor.Weird"}}}}}
    result = CsnToOssieConverter().convert(csn)
    assert result.output.datasets[0].fields[0].datatype.value == "Opaque"
    assert [i.element_name for i in issues_of(result, ConverterIssueType.OPAQUE_DATATYPE)] == ["E.x"]


def test_temporal_fields_are_time_dimensions(datasets):
    books = by_name(datasets["Books"].fields)
    assert books["published"].is_time_dimension()
    assert not books["title"].is_time_dimension()


def test_calculated_elements_become_sql_expressions(datasets):
    books = by_name(datasets["Books"].fields)
    assert sql_of(books["priceWithTax"]) == "price * 1.19"
    assert sql_of(books["authorName"]) == "Authors.name"  # a path through the `author` association


def test_managed_association_yields_a_relationship_and_its_foreign_key_field(result, datasets):
    relationships = by_name(result.output.relationships)
    author = relationships["author"]
    assert (author.from_dataset, author.to) == ("Books", "Authors")
    assert (author.from_columns, author.to_columns) == (["author_ID"], ["ID"])
    fk = by_name(datasets["Books"].fields)["author_ID"]
    assert fk.datatype.value == "Integer"
    assert sap_ext(fk) == {"association": "author", "generated_foreign_key": True}


def test_on_condition_association_uses_the_declared_columns(result):
    book = by_name(result.output.relationships)["book"]
    assert (book.from_dataset, book.to) == ("Orders", "Books")
    assert (book.from_columns, book.to_columns) == (["book_ID"], ["ID"])
    assert sap_ext(book)["managed"] is False


def test_composition_is_a_relationship_too(result):
    customers = by_name(result.output.relationships)["customers"]
    assert customers.to == "Items"
    assert sap_ext(customers)["type"] == "cds.Composition"


def test_backlink_is_kept_aside_and_reported(result, datasets):
    assert "books" in sap_ext(datasets["Authors"])["unmodeled_elements"]
    assert [i.element_name for i in issues_of(result, ConverterIssueType.RELATIONSHIP_DROPPED)] == ["Authors.books"]
    assert "books" not in by_name(datasets["Authors"].fields)


def test_structured_element_is_kept_aside_and_reported(result, datasets):
    assert "tags" in sap_ext(datasets["Orders"])["unmodeled_elements"]
    assert [i.element_name for i in issues_of(result, ConverterIssueType.ELEMENT_NOT_MODELED)] == ["Orders.tags"]


def test_aggregation_annotations_become_metrics(result):
    metrics = by_name(result.output.metrics)
    assert sql_of(metrics["stock"]) == "SUM(Books.stock)"
    assert sql_of(metrics["price"]) == "AVG(Books.price)"
    assert metrics["stock"].datatype.value == "Integer"
    assert metrics["price"].datatype.value == "Decimal"


def test_a_measure_stays_a_field_unless_it_is_derived(datasets):
    assert "stock" in by_name(datasets["Books"].fields)
    assert "amount" not in by_name(datasets["Orders"].fields)
    assert by_name(datasets["Books"].fields)["stock"].dimension is None


def test_derived_measure_aggregates_its_expression_across_the_association(result):
    assert sql_of(by_name(result.output.metrics)["amount"]) == "SUM(Orders.quantity * Books.price)"


def test_annotations_without_an_ossie_property_are_preserved(datasets):
    price = sap_ext(by_name(datasets["Books"].fields)["price"])
    assert price["annotations"] == {"@Semantics.amount.currencyCode": "currency_code"}
    assert sap_ext(by_name(datasets["Books"].fields)["title"])["native"] == {"length": 111}
    assert sap_ext(datasets["Books"])["annotations"] == {"@Analytics.dataCategory": {"#": "CUBE"}}


def test_the_view_definition_is_preserved(datasets):
    assert sap_ext(datasets["ListOfBooks"])["native"]["projection"]["from"]["ref"] == [BOOKS]


def test_other_definitions_and_root_keys_are_preserved(result):
    data = sap_ext(result.output)
    assert set(data["other_definitions"]) >= {"CatalogService", "sap.capire.bookshop.Amount", "sap.capire.bookshop.submitOrder"}
    assert data["csn"]["namespace"] == "sap.capire.bookshop"
    assert [i.element_name for i in issues_of(result, ConverterIssueType.UNSUPPORTED_DEFINITION_KIND)] == [
        "sap.capire.bookshop.submitOrder"
    ]


def test_model_name_defaults_when_none_is_given():
    csn = load_fixture("bookshop.csn.json")
    assert CsnToOssieConverter().convert(csn).output.name == "sap_csn_model"


def test_same_association_name_on_two_entities_is_disambiguated():
    key = {"type": "cds.Integer", "key": True}
    csn = {
        "definitions": {
            "T": {"kind": "entity", "elements": {"ID": key}},
            "A": {"kind": "entity", "elements": {"ID": key, "t": {"type": "cds.Association", "target": "T"}}},
            "B": {"kind": "entity", "elements": {"ID": key, "t": {"type": "cds.Association", "target": "T"}}},
        }
    }
    names = {r.name for r in CsnToOssieConverter().convert(csn).output.relationships}
    assert names == {"A_t", "B_t"}


def test_same_measure_name_on_two_entities_is_disambiguated():
    def entity():
        return {"kind": "entity", "elements": {"total": {"type": "cds.Integer", "@Aggregation.default": "#SUM"}}}

    csn = {"definitions": {"A": entity(), "B": entity()}}
    names = {m.name for m in CsnToOssieConverter().convert(csn).output.metrics}
    assert names == {"A_total", "B_total"}


def test_calculated_element_that_cannot_become_sql_is_kept_aside():
    csn = {
        "definitions": {
            "E": {
                "kind": "entity",
                "elements": {
                    "ID": {"type": "cds.Integer", "key": True},
                    "who": {"type": "cds.String", "value": {"ref": ["$session", "user"]}},
                },
            }
        }
    }
    result = CsnToOssieConverter().convert(csn)
    assert "who" not in by_name(result.output.datasets[0].fields)
    assert "who" in sap_ext(result.output.datasets[0])["unmodeled_elements"]
    assert [i.issue_type for i in result.issues] == [ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE]


def test_association_to_an_unknown_target_is_kept_aside():
    csn = {
        "definitions": {
            "E": {
                "kind": "entity",
                "elements": {"ID": {"type": "cds.Integer", "key": True}, "x": {"type": "cds.Association", "target": "Elsewhere"}},
            }
        }
    }
    result = CsnToOssieConverter().convert(csn)
    assert result.output.relationships is None
    assert [i.issue_type for i in result.issues] == [ConverterIssueType.RELATIONSHIP_DROPPED]


def test_input_is_not_mutated():
    csn = load_fixture("bookshop.csn.json")
    before = copy.deepcopy(csn)
    CsnToOssieConverter().convert(csn)
    assert csn == before


@pytest.mark.parametrize(
    "bad",
    [[], {}, {"definitions": []}, {"definitions": {"T": {"kind": "type", "type": "cds.String"}}}],
)
def test_input_that_is_not_a_csn_model_is_an_error(bad):
    with pytest.raises(ConverterError):
        CsnToOssieConverter().convert(bad)
