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

import static org.apache.ossie.converter.ConverterConstants.*;
import static org.apache.ossie.util.DataStructureUtils.*;

import org.apache.ossie.converter.ConverterConstants.Level;
import org.apache.ossie.converter.pipeline.PipelineStep;
import java.util.*;

import org.apache.ossie.util.MappingUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Bidirectional handler for mapping metrics between Ossie and Salesforce formats.
 *
 * <p>Supports both conversion directions:
 * <ul>
 *   <li>Ossie → Salesforce: unwrap expression from dialects structure</li>
 *   <li>Salesforce → Ossie: wrap expression in dialects structure</li>
 * </ul>
 *
 */
public class MetricMappingHandler implements PipelineStep {

    private static final Logger logger = LoggerFactory.getLogger(MetricMappingHandler.class);

    private static final String METRICS = "metrics";
    private static final String SEMANTIC_CALCULATED_MEASUREMENTS = "semanticCalculatedMeasurements";

    private final ConversionDirection direction;
    private final CustomExtensionHandler customExtensionHandler;

    public MetricMappingHandler(ConversionDirection direction, CustomExtensionHandler customExtensionHandler) {
        this.direction = direction;
        this.customExtensionHandler = customExtensionHandler;
    }

    @Override
    public void execute(Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {
        logger.debug("Mapping metrics in {} direction", direction);
        if (direction == ConversionDirection.OSI_TO_SALESFORCE) {
            mapOsiToSalesforce(sourceData, outputData, mappings);
        } else {
            mapSalesforceToOsi(sourceData, outputData, mappings);
        }
    }

    /**
     * Maps Ossie metrics to Salesforce semanticCalculatedMeasurements.
     */
    private void mapOsiToSalesforce(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> osiMetrics = getList(sourceData, METRICS);
        if (osiMetrics == null) {
            return;
        }

        // Filter mappings to get only metric-related entries
        Map<String, String> metricMappings = MappingUtils.filterMappingsByPrefix(mappings, METRICS);
        metricMappings.keySet().forEach(mappings::remove);

        logger.debug("Metrics are not mapped in Ossie to Salesforce direction");
    }

    /**
     * Maps Salesforce semanticCalculatedMeasurements to Ossie metrics.
     */
    private void mapSalesforceToOsi(
            Map<String, Object> sourceData, Map<String, Object> outputData, Map<String, String> mappings) {

        List<Object> sfMetrics = getList(sourceData, SEMANTIC_CALCULATED_MEASUREMENTS);
        if (sfMetrics == null) {
            return;
        }

        Map<String, String> metricMappings =
                MappingUtils.filterMappingsByPrefix(mappings, SEMANTIC_CALCULATED_MEASUREMENTS);

        Set<String> allHandledProps = metricMappings.isEmpty()? new HashSet<>() : MappingUtils.extractHandledProperties(metricMappings);
        allHandledProps.add(EXPRESSION);

        Map<String, Object> mappedData = GenericMappingEngine.applyMappings(sourceData, metricMappings);
        metricMappings.keySet().forEach(mappings::remove);

        outputData.putAll(mappedData);

        List<Object> osiMetrics = getList(outputData, METRICS);
        if (osiMetrics != null) {
            wrapExpressions(sfMetrics, osiMetrics);
        }

        // Store unmapped SF properties in custom_extensions
        customExtensionHandler.storeUnmappedProperties(outputData, sourceData, allHandledProps, Level.METRICS);

        // Cleanup: remove processed structural key
        sourceData.remove(SEMANTIC_CALCULATED_MEASUREMENTS);
    }


    /**
     * Wraps expressions for SF→Ossie conversion.
     */
    private void wrapExpressions(List<Object> sfMetrics, List<Object> osiMetrics) {
        for (int i = 0; i < sfMetrics.size() && i < osiMetrics.size(); i++) {
            Map<String, Object> sfMetric = asMap(sfMetrics.get(i));
            Map<String, Object> osiMetric = asMap(osiMetrics.get(i));

            // Get expression from SF metric
            String expressionValue = getString(sfMetric, EXPRESSION);
            if (expressionValue != null) {
                // Wrap in Ossie dialect structure
                osiMetric.put(EXPRESSION, wrapExpression(expressionValue));
            }
        }
    }

    /**
     * Wraps a simple expression string in Ossie's expression.dialects structure.
     * Tags expressions with TABLEAU dialect as they come from Salesforce (Tableau CRM).
     */
    private Map<String, Object> wrapExpression(String expressionValue) {
        Map<String, Object> dialect = new LinkedHashMap<>();
        dialect.put(DIALECT, DIALECT_TABLEAU);
        dialect.put(EXPRESSION, expressionValue);

        List<Object> dialects = new ArrayList<>();
        dialects.add(dialect);

        Map<String, Object> expression = new LinkedHashMap<>();
        expression.put(DIALECTS, dialects);

        return expression;
    }

}
