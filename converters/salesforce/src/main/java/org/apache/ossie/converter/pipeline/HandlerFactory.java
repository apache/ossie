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

package org.apache.ossie.converter.pipeline;

import org.apache.ossie.converter.*;
import org.apache.ossie.exception.ConversionException;

/**
 * Factory for creating handler instances using a hardcoded registry.
 * pipeline configuration specifies which handlers to run and in what order.
 *
 */
public class HandlerFactory {
    private final CustomExtensionHandler customExtensionHandler;

    public HandlerFactory(CustomExtensionHandler customExtensionHandler) {
        this.customExtensionHandler = customExtensionHandler;
    }

    /**
     * Creates a handler instance from the registered handler names.
     *
     * @param handlerName The handler name from pipeline config
     * @param direction The conversion direction
     * @return A PipelineStep instance
     * @throws ConversionException if handler name is unknown
     */
    public PipelineStep createHandler(String handlerName, ConversionDirection direction) {
        return switch(handlerName) {
            case "DatasetMappingHandler" ->
                new DatasetMappingHandler(direction, customExtensionHandler);
            case "FieldMappingHandler" ->
                new FieldMappingHandler(direction, customExtensionHandler);
            case "RelationshipMappingHandler" ->
                new RelationshipMappingHandler(direction, customExtensionHandler);
            case "MetricMappingHandler" ->
                new MetricMappingHandler(direction, customExtensionHandler);
            case "SemanticModelMappingHandler" ->
                new SemanticModelMappingHandler(direction, customExtensionHandler);
            default ->
                throw new ConversionException("Unknown handler: " + handlerName);
        };
    }
}
