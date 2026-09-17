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

package org.apache.ossie;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.TimeUnit;
import org.apache.ossie.app.OssieSalesforceConverter;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class MetricCliTest {
    @TempDir
    Path directory;

    @Test
    void reportsMetricFailureToStderrWithoutWritingAModel() throws Exception {
        Path input = directory.resolve("input.yaml");
        Files.writeString(input, Files.readString(Path.of("src/test/resources/examples/ossieToSalesforce.yaml"))
                .replace("SUM([Orders].[amount])", "SUM([Orders].[missing])"));
        Path stderr = directory.resolve("stderr.txt");
        Process process = new ProcessBuilder(
                Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                "-cp", System.getProperty("java.class.path"), OssieSalesforceConverter.class.getName(),
                "toSF", input.toString())
                .redirectError(stderr.toFile())
                .redirectOutput(directory.resolve("stdout.txt").toFile())
                .start();
        try {
            assertTrue(process.waitFor(30, TimeUnit.SECONDS), "CLI did not terminate");
            assertEquals(3, process.exitValue());
            String error = Files.readString(stderr);
            assertTrue(error.contains("Metric 'total_revenue'"), error);
            assertTrue(error.contains("Unknown field reference"), error);
            assertTrue(error.contains("missing"), error);
            assertFalse(Files.exists(directory.resolve("Customer_Orders_Model.json")));
        } finally {
            process.destroyForcibly();
        }
    }
}
