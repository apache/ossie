/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

package org.apache.ossie.converter;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Files;
import java.nio.file.Path;
import org.apache.ossie.exception.InvalidInputException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SourceDocumentValidationTest {
    @TempDir Path directory;

    @Test
    void rejectsASecondOsiYamlDocumentRatherThanOmittingItsModel() throws Exception {
        String valid = fixture("ossieToSalesforce.yaml");
        String second = valid.replace("Customer_Orders_Model", "Other_Model");
        assertInvalid(ConversionDirection.OSSIE_TO_SALESFORCE, valid + "\n---\n" + second, "Trailing token");
    }

    @Test
    void rejectsASecondNativeJsonObjectRatherThanOmittingIt() throws Exception {
        String valid = fixture("salesforceToOssie.json");
        assertInvalid(ConversionDirection.SALESFORCE_TO_OSSIE, valid + "\n" + valid, "Trailing token");
    }

    @Test
    void rejectsDuplicateYamlModelPropertiesBeforeTheyOverwriteTheFirstValue() throws Exception {
        String input = fixture("ossieToSalesforce.yaml").replace("  - name: Customer_Orders_Model",
                "  - name: Customer_Orders_Model\n    name: Silent_Replacement");
        assertTrue(input.contains("Silent_Replacement"));
        assertInvalid(ConversionDirection.OSSIE_TO_SALESFORCE, input, "Duplicate field 'name'");
    }

    @Test
    void rejectsDuplicateNestedYamlTypesBeforeTheyChangeFieldMeaning() throws Exception {
        String input = fixture("ossieToSalesforce.yaml").replaceFirst("(?m)^([ ]*)datatype: String$",
                "$1datatype: String\n$1datatype: Integer");
        assertTrue(input.contains("datatype: Integer"));
        assertInvalid(ConversionDirection.OSSIE_TO_SALESFORCE, input, "Duplicate field 'datatype'");
    }

    @Test
    void rejectsDuplicateNativeJsonPropertiesBeforeTheyOverwriteTheFirstValue() throws Exception {
        String valid = fixture("salesforceToOssie.json");
        String input = "{\"apiName\":\"Silently_Replaced\"," + valid.substring(valid.indexOf('{') + 1);
        assertInvalid(ConversionDirection.SALESFORCE_TO_OSSIE, input, "Duplicate field 'apiName'");
    }

    @Test
    void fileApiWritesNothingForTrailingSourceDocumentsAndAcceptsSingleDocumentComments() throws Exception {
        String valid = fixture("ossieToSalesforce.yaml");
        Converter converter = ConverterFactory.getConverter(ConversionDirection.OSSIE_TO_SALESFORCE);
        assertEquals(1, converter.convert(valid + "\n# trailing comments are part of the same document\n").size());
        Path input = directory.resolve("input.yaml");
        Files.writeString(input, valid + "\n---\nnull\n");
        Path existing = directory.resolve("Customer_Orders_Model.json");
        Files.writeString(existing, "preserve existing output");
        assertThrows(InvalidInputException.class, () -> converter.convert(input, directory));
        assertEquals("preserve existing output", Files.readString(existing));
        try (var files = Files.list(directory)) { assertEquals(2, files.count()); }
    }

    private static void assertInvalid(ConversionDirection direction, String content, String message) {
        InvalidInputException error = assertThrows(InvalidInputException.class,
                () -> ConverterFactory.getConverter(direction).convert(content));
        assertTrue(error.getMessage().contains(message), error.getMessage());
    }

    private static String fixture(String file) throws Exception {
        return Files.readString(Path.of("src/test/resources/examples", file));
    }
}
