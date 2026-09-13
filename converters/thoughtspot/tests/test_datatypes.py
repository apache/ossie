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

import pytest
from ossie_thoughtspot.datatypes import (
    OSSIE_DATATYPES, declared_loss, to_ossie, to_tml,
)


class TestToTml:
    @pytest.mark.parametrize("datatype,expected", [
        ("String", "VARCHAR"), ("Integer", "INT64"), ("Decimal", "DOUBLE"),
        ("Float", "DOUBLE"), ("Boolean", "BOOLEAN"), ("Date", "DATE"),
        ("Time", "VARCHAR"), ("DateTime", "DATE_TIME"),
        ("DateTimeTz", "DATE_TIME"), ("Opaque", "VARCHAR"),
    ])
    def test_every_ossie_datatype_maps(self, datatype, expected):
        assert to_tml(datatype) == expected

    def test_the_map_covers_the_whole_enum(self):
        # A datatype added to the Ossie enum must fail here, not silently
        # convert to nothing.
        for datatype in OSSIE_DATATYPES:
            assert to_tml(datatype)

    def test_missing_datatype_infers_rather_than_raising(self):
        # Ossie makes datatype optional; TML makes db_column_properties compulsory.
        assert to_tml(None) == "INT64"

    def test_connection_spellings_are_selectable(self):
        assert to_tml("Boolean", boolean_spelling="BOOL") == "BOOL"
        assert to_tml("Float", float_spelling="FLOAT") == "FLOAT"

    def test_the_float_spelling_does_not_leak_into_decimal(self):
        # Decimal is DOUBLE on every connection — only Float is BigQuery-sensitive.
        assert to_tml("Decimal", float_spelling="FLOAT") == "DOUBLE"

    def test_an_unknown_datatype_raises(self):
        with pytest.raises(ValueError, match="Nonsense"):
            to_tml("Nonsense")

    def test_case_sensitive_datatype_is_unknown_rather_than_normalised(self):
        # Ossie datatypes are spelled exactly as the enum ("Boolean", not
        # "boolean" or "BOOLEAN"). A caller that passes a differently-cased
        # variant — easy to do if the value came from a case-folding step
        # upstream, or from a TML string mistaken for an Ossie one — must get
        # a clear error, not a silent no-op or a wrong mapping.
        with pytest.raises(ValueError, match="boolean"):
            to_tml("boolean")


class TestToOssie:
    @pytest.mark.parametrize("tml_type,expected", [
        ("VARCHAR", "String"), ("INT64", "Integer"), ("DOUBLE", "Decimal"),
        ("FLOAT", "Float"), ("BOOL", "Boolean"), ("BOOLEAN", "Boolean"),
        ("DATE", "Date"), ("DATE_TIME", "DateTime"),
    ])
    def test_known_tml_types(self, tml_type, expected):
        assert to_ossie(tml_type) == expected

    def test_an_unknown_tml_type_returns_none_rather_than_guessing(self):
        # datatype is optional in Ossie, so omitting it is a legitimate answer
        # and strictly better than inventing one.
        assert to_ossie("GEOGRAPHY") is None

    def test_sql_type_names_are_not_accepted(self):
        # ThoughtSpot rejects these itself: "DataType BIGINT does not match CDW DataType".
        assert to_ossie("BIGINT") is None

    def test_empty_string_returns_none(self):
        # A blank data_type is a plausible malformed-document artefact (a
        # missing YAML value that parses as ""), and it is not a key in the
        # map. It must return None like any other unmapped string, not raise.
        assert to_ossie("") is None


class TestDeclaredLoss:
    @pytest.mark.parametrize("datatype", ["Float", "Time", "DateTimeTz", "Opaque"])
    def test_the_four_lossy_types_are_named(self, datatype):
        assert declared_loss(datatype)

    @pytest.mark.parametrize("datatype", ["String", "Integer", "Decimal",
                                          "Boolean", "Date", "DateTime"])
    def test_the_lossless_types_are_not(self, datatype):
        assert declared_loss(datatype) is None

    def test_round_trip_is_exact_for_every_non_lossy_type(self):
        # The property that makes `declared_loss` trustworthy: if it says a type
        # is lossless, TML -> Ossie -> TML really does return the same value.
        for datatype in OSSIE_DATATYPES:
            if declared_loss(datatype) is None:
                assert to_ossie(to_tml(datatype)) == datatype
