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

package org.apache.ossie.app;

import org.apache.ossie.converter.Converter;
import org.apache.ossie.converter.ConverterFactory;
import org.apache.ossie.converter.ConversionDirection;
import org.apache.ossie.exception.ConversionException;
import org.apache.ossie.exception.InvalidInputException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * Main application class for the Ossie-Salesforce Converter.
 *
 * <p>Converts between Salesforce Semantic Model and Ossie formats.
 * Output file is placed in the same directory as input with the appropriate extension.</p>
 *
 */
public class OsiSalesforceConverter {

    public static void main(String[] args) {
        if (args.length < 2) {
            System.exit(1);
        }

        try {
            OsiSalesforceConverter app = new OsiSalesforceConverter();
            String directionArg = args[0];
            Path inputPath = Paths.get(args[1]);

            ConversionDirection direction = parseDirection(directionArg);
            app.convert(direction, inputPath);
        } catch (InvalidInputException e) {
            System.exit(2);
        } catch (ConversionException e) {
            System.exit(3);
        }
    }

    private static ConversionDirection parseDirection(String direction) {
        return switch (direction.toLowerCase()) {
            case "tosf" -> ConversionDirection.OSI_TO_SALESFORCE;
            case "toosi" -> ConversionDirection.SALESFORCE_TO_OSI;
            default -> throw new InvalidInputException(
                "Invalid direction: " + direction + ". Expected: toSF or toOSI"
            );
        };
    }

    /**
     * Converts a file. Output files are written to the same directory as the input file,
     * with filenames based on model apiNames.
     *
     * @param direction The conversion direction
     * @param inputPath path to the input file
     */
    public void convert(ConversionDirection direction, Path inputPath) {
        if (!Files.exists(inputPath)) {
            throw new InvalidInputException("Input file not found: " + inputPath);
        }

        Path outputDir = inputPath.getParent();
        if (outputDir == null) {
            outputDir = Path.of(".");
        }

        Converter converter = ConverterFactory.getConverter(direction);
        converter.convert(inputPath, outputDir);
    }

}
