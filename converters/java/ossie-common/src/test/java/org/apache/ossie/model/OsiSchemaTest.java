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
package org.apache.ossie.model;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Map;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

class OsiSchemaTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    void deserializesAllModelTypesAndCanonicalPropertyNames() throws Exception {
        String json = String.join("\n",
                "{",
                "  \"version\": \"0.2.0.dev0\",",
                "  \"semantic_model\": [{",
                "    \"name\": \"sales\",",
                "    \"ai_context\": {",
                "      \"instructions\": \"Use for revenue analysis\",",
                "      \"synonyms\": [\"orders\"]",
                "    },",
                "    \"datasets\": [{",
                "      \"name\": \"orders\",",
                "      \"source\": \"analytics.public.orders\",",
                "      \"primary_key\": [\"order_id\", \"line_id\"],",
                "      \"unique_keys\": [[\"external_id\", \"line_id\"]],",
                "      \"fields\": [{",
                "        \"name\": \"order_id\",",
                "        \"expression\": {\"dialects\": [{",
                "          \"dialect\": \"ANSI_SQL\",",
                "          \"expression\": \"order_id\"",
                "        }]},",
                "        \"dimension\": {\"is_time\": false},",
                "        \"ai_context\": \"order identifier\",",
                "        \"custom_extensions\": [{",
                "          \"vendor_name\": \"COMMON\",",
                "          \"data\": \"{}\"",
                "        }]",
                "      }]",
                "    }, {",
                "      \"name\": \"customers\",",
                "      \"source\": \"analytics.public.customers\"",
                "    }],",
                "    \"relationships\": [{",
                "      \"name\": \"orders_to_customers\",",
                "      \"from\": \"orders\",",
                "      \"to\": \"customers\",",
                "      \"from_columns\": [\"customer_id\", \"tenant_id\"],",
                "      \"to_columns\": [\"customer_id\", \"tenant_id\"]",
                "    }],",
                "    \"metrics\": [{",
                "      \"name\": \"revenue\",",
                "      \"expression\": {\"dialects\": [{",
                "        \"dialect\": \"SNOWFLAKE\",",
                "        \"expression\": \"SUM(amount)\"",
                "      }]}",
                "    }],",
                "    \"custom_extensions\": [{",
                "      \"vendor_name\": \"SNOWFLAKE\",",
                "      \"data\": \"{\\\"warehouse\\\":\\\"ANALYTICS_WH\\\"}\"",
                "    }]",
                "  }]",
                "}");

        OsiSchema schema = objectMapper.readValue(json, OsiSchema.class);

        assertEquals("0.2.0.dev0", schema.getVersion());
        assertEquals("sales", schema.getSemanticModel().get(0).getName());
        assertEquals("order_id", schema.getSemanticModel().get(0)
                .getDatasets().get(0).getPrimaryKey().get(0));
        assertEquals("line_id", schema.getSemanticModel().get(0)
                .getDatasets().get(0).getUniqueKeys().get(0).get(1));
        assertEquals(DialectExpression.Dialect.ANSI_SQL, schema.getSemanticModel().get(0)
                .getDatasets().get(0).getFields().get(0)
                .getExpression().getDialects().get(0).getDialect());
        assertEquals("order identifier", schema.getSemanticModel().get(0)
                .getDatasets().get(0).getFields().get(0).getAiContext());
        assertEquals("tenant_id", schema.getSemanticModel().get(0)
                .getRelationships().get(0).getToColumns().get(1));
        assertEquals(DialectExpression.Dialect.SNOWFLAKE, schema.getSemanticModel().get(0)
                .getMetrics().get(0).getExpression().getDialects().get(0).getDialect());
        assertEquals("SNOWFLAKE", schema.getSemanticModel().get(0)
                .getCustomExtensions().get(0).getVendorName());

        Map<?, ?> aiContext = assertInstanceOf(
                Map.class, schema.getSemanticModel().get(0).getAiContext());
        assertEquals("Use for revenue analysis", aiContext.get("instructions"));

        String serialized = objectMapper.writeValueAsString(schema);
        assertTrue(serialized.contains("\"semantic_model\""));
        assertTrue(serialized.contains("\"primary_key\""));
    }
}
