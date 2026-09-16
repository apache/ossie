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

import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.ossie.exception.ConversionException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class RelationshipMappingHandlerTest {
    private final CustomExtensionHandler extensions = new CustomExtensionHandler(new ObjectMapper());
    private final RelationshipMappingHandler handler =
            new RelationshipMappingHandler(ConversionDirection.OSSIE_TO_SALESFORCE, extensions);

    @Test
    void invalidFirstRelationshipFailsWithoutMisassigningItsKeysToTheSecond() {
        Map<String, Object> bad = relation("bad", "computed");
        Map<String, Object> good = relation("good", "customer_id");
        Map<String, Object> source = source(bad, good);
        Map<String, Object> target = target();
        Map<String, String> mappings = mappings();
        ConversionException error = assertThrows(ConversionException.class,
                () -> handler.execute(source, target, mappings));
        assertTrue(error.getMessage().contains("bad"));
        assertTrue(error.getMessage().contains("calculated join keys"));
        assertEquals(List.of(bad, good), source.get("relationships"), "Do not filter or mutate source relationships");
        assertFalse(target.containsKey("semanticRelationships"), "No partially mapped valid relationship is published");
        assertFalse(mappings.containsKey("relationships"));
    }

    @Test
    void allInvalidRelationshipsCannotFallThroughToGenericRawMapping() {
        Map<String, Object> source = source(relation("bad", "computed"));
        Map<String, Object> target = target();
        Map<String, String> mappings = mappings();
        assertThrows(ConversionException.class, () -> handler.execute(source, target, mappings));
        new SemanticModelMappingHandler(ConversionDirection.OSSIE_TO_SALESFORCE, extensions)
                .execute(source, target, mappings);
        assertFalse(target.containsKey("semanticRelationships"));
    }

    @Test
    void mapsEveryCompositeKeyPairAndUsesOssieDirectionForDefaultCardinality() {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("from_columns", List.of("customer_id", "region"));
        relation.put("to_columns", List.of("id", "region"));
        Map<String, Object> target = target();
        handler.execute(source(relation), target, mappings());
        Map<String, Object> exported = relationships(target).get(0);
        assertEquals("ManyToOne", exported.get("cardinality"));
        assertEquals(true, exported.get("isEnabled"));
        assertEquals("Auto", exported.get("joinType"));
        assertEquals(List.of(Map.of("leftSemanticFieldApiName", "customer_id", "rightSemanticFieldApiName", "id"),
                Map.of("leftSemanticFieldApiName", "region", "rightSemanticFieldApiName", "region")), exported.get("criteria"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"OneToOne", "OneToMany", "ManyToOne", "ManyToMany", "Unspecified"})
    void preservesExplicitNativeCardinalityForSalesforceRoundTrips(String cardinality) {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"cardinality\":\"" + cardinality + "\",\"isEnabled\":false,\"joinType\":\"Left\"}")));
        Map<String, Object> target = target();
        handler.execute(source(relation), target, mappings());
        Map<String, Object> exported = relationships(target).get(0);
        assertEquals(cardinality, exported.get("cardinality"));
        assertEquals(false, exported.get("isEnabled"));
        assertEquals("Left", exported.get("joinType"));
    }

    @Test
    void rejectsInvalidNativeCardinalityInsteadOfSilentlyReplacingIt() {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"cardinality\":\"Guess\"}")));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source(relation), target(), mappings())).getMessage().contains("cardinality"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"cardinality", "isEnabled", "joinType"})
    void doesNotReplaceExplicitNullNativeMetadataWithDefaults(String property) {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"" + property + "\":null}")));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source(relation), target(), mappings())).getMessage().contains(property));
    }

    @Test
    void rejectsCompositeArityMismatch() {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("from_columns", List.of("customer_id", "region"));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source(relation), target(), mappings())).getMessage().contains("same number"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"from", "to"})
    void rejectsUnknownEndpoint(String endpoint) {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put(endpoint, "missing");
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source(relation), target(), mappings())).getMessage().contains("missing"));
    }

    @Test
    void rejectsDuplicateRelationNamesAndRepeatedKeys() {
        assertTrue(assertThrows(ConversionException.class, () -> handler.execute(
                source(relation("join", "customer_id"), relation("JOIN", "customer_id")), target(), mappings()))
                .getMessage().contains("Duplicate"));
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("from_columns", List.of("customer_id", "customer_id"));
        relation.put("to_columns", List.of("id", "region"));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source(relation), target(), mappings())).getMessage().contains("duplicate field"));
    }

    @Test
    void declaredUniquenessMustCoverTheRelationshipTargetKey() {
        Map<String, Object> source = source(relation("join", "customer_id"));
        datasets(source).get(1).put("primary_key", List.of("region"));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source, target(), mappings())).getMessage().contains("declared primary_key"));
        datasets(source).get(1).put("unique_keys", List.of(List.of("id")));
        Map<String, Object> target = target();
        assertDoesNotThrow(() -> handler.execute(source, target, mappings()));
        assertFalse(relationships(target).get(0).containsKey("primaryNameField"));
    }

    @Test
    void nativeOneToManyChecksLeftUniquenessAndAllowsNonUniqueRightKey() {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"cardinality\":\"OneToMany\"}")));
        Map<String, Object> source = source(relation);
        datasets(source).get(0).put("primary_key", List.of("customer_id"));
        datasets(source).get(1).put("primary_key", List.of("region"));
        assertDoesNotThrow(() -> handler.execute(source, target(), mappings()));
        datasets(source).get(0).put("primary_key", List.of("region"));
        assertTrue(assertThrows(ConversionException.class,
                () -> handler.execute(source, target(), mappings())).getMessage().contains("from_columns"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"ManyToMany", "Unspecified"})
    void nativeNonUniqueCardinalitiesDoNotInventKeyConstraints(String cardinality) {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"cardinality\":\"" + cardinality + "\"}")));
        Map<String, Object> source = source(relation);
        datasets(source).get(0).put("primary_key", List.of("region"));
        datasets(source).get(1).put("primary_key", List.of("region"));
        assertDoesNotThrow(() -> handler.execute(source, target(), mappings()));
    }

    @Test
    void coreJoinKeysRemainAuthoritativeOverStaleNativeExtensions() {
        Map<String, Object> relation = relation("join", "customer_id");
        relation.put("custom_extensions", List.of(Map.of("vendor_name", "SALESFORCE", "data",
                "{\"criteria\":[{\"leftSemanticFieldApiName\":\"stale\",\"rightSemanticFieldApiName\":\"stale\"}]}")));
        Map<String, Object> target = target();
        handler.execute(source(relation), target, mappings());
        assertEquals(List.of(Map.of("leftSemanticFieldApiName", "customer_id", "rightSemanticFieldApiName", "id")),
                relationships(target).get(0).get("criteria"));
    }

    @Test
    void salesforceToOssieKeepsNativeCardinalityAndPairOrder() {
        Map<String, Object> sf = target();
        handler.execute(source(relation("join", "customer_id")), sf, mappings());
        relationships(sf).get(0).put("cardinality", "OneToMany");
        Map<String, Object> osi = new LinkedHashMap<>();
        Map<String, String> reverseMappings = new LinkedHashMap<>();
        reverseMappings.put("semanticRelationships", "relationships");
        reverseMappings.put("semanticRelationships.apiName", "relationships.name");
        new RelationshipMappingHandler(ConversionDirection.SALESFORCE_TO_OSSIE, extensions)
                .execute(sf, osi, reverseMappings);
        @SuppressWarnings("unchecked")
        Map<String, Object> relation = ((List<Map<String, Object>>) osi.get("relationships")).get(0);
        assertEquals("orders", relation.get("from"));
        assertEquals(List.of("customer_id"), relation.get("from_columns"));
        assertEquals(List.of("id"), relation.get("to_columns"));
        assertTrue(relation.get("custom_extensions").toString().contains("OneToMany"));
    }

    private static Map<String, Object> source(Map<String, Object>... relationships) {
        Map<String, Object> source = new LinkedHashMap<>();
        source.put("name", "sales");
        source.put("datasets", new ArrayList<>(List.of(
                sourceDataset("orders", "customer_id", "region", "computed"), sourceDataset("customers", "id", "region"))));
        source.put("relationships", new ArrayList<>(List.of(relationships)));
        return source;
    }

    private static Map<String, Object> sourceDataset(String name, String... fields) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("name", name);
        result.put("fields", java.util.Arrays.stream(fields).map(field -> Map.<String, Object>of("name", field)).toList());
        return result;
    }

    private static Map<String, Object> target() {
        Map<String, Object> target = new LinkedHashMap<>();
        target.put("semanticDataObjects", List.of(
                Map.of("apiName", "orders", "semanticDimensions", List.of(Map.of("apiName", "customer_id"), Map.of("apiName", "region"))),
                Map.of("apiName", "customers", "semanticDimensions", List.of(Map.of("apiName", "id"), Map.of("apiName", "region")))));
        return target;
    }

    private static Map<String, Object> relation(String name, String field) {
        return new LinkedHashMap<>(Map.of("name", name, "from", "orders", "to", "customers",
                "from_columns", List.of(field), "to_columns", List.of("id")));
    }

    private static Map<String, String> mappings() {
        Map<String, String> mappings = new LinkedHashMap<>();
        mappings.put("name", "apiName");
        mappings.put("relationships", "semanticRelationships");
        mappings.put("relationships.name", "semanticRelationships.apiName");
        return mappings;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> relationships(Map<String, Object> target) {
        return (List<Map<String, Object>>) target.get("semanticRelationships");
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> datasets(Map<String, Object> source) {
        return (List<Map<String, Object>>) source.get("datasets");
    }
}
