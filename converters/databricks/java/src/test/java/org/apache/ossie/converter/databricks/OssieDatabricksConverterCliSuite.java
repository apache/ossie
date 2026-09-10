/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements.  See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.ossie.converter.databricks;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Tests for the command-line wrapper: which command means which direction, which selector flag
 * each command accepts, and that stdout carries the same bytes as {@code -o}.
 */
public class OssieDatabricksConverterCliSuite {

  private static final String OSSIE_MODEL =
      "version: \"0.2.0.dev0\"\n"
      + "semantic_model:\n"
      + "  - name: sales\n"
      + "    datasets:\n"
      + "      - name: orders\n"
      + "        source: cat.sch.orders\n"
      + "        fields:\n"
      + "          - name: o_status\n"
      + "            expression:\n"
      + "              dialects: [{dialect: DATABRICKS, expression: o_orderstatus}]\n";

  private static final String METRIC_VIEW =
      "version: '1.1'\n"
      + "source: cat.sch.orders\n"
      + "dimensions:\n"
      + "- {name: o_status, expr: o_orderstatus}\n";

  @TempDir
  Path dir;

  private Path write(String name, String content) throws IOException {
    Path path = dir.resolve(name);
    Files.writeString(path, content, StandardCharsets.UTF_8);
    return path;
  }

  /** Runs the CLI core and returns whatever it wrote to stdout. */
  private static String stdout(String... args) {
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    ByteArrayOutputStream err = new ByteArrayOutputStream();
    PrintStream outStream = new PrintStream(out, true, StandardCharsets.UTF_8);
    PrintStream errStream = new PrintStream(err, true, StandardCharsets.UTF_8);
    OssieDatabricksConverter.run(args, outStream, errStream);
    outStream.flush();
    errStream.flush();
    return out.toString(StandardCharsets.UTF_8);
  }

  private static OssieDatabricksConverter.ExitException exitFrom(String... args) {
    return assertThrows(OssieDatabricksConverter.ExitException.class, () -> stdout(args));
  }

  @Test
  public void exportConvertsAnOssieModelToAMetricView() throws IOException {
    // The directions are named from the Apache Ossie model's point of view, matching the library
    // Javadoc and the Python CLI: `export` takes a model OUT to a Metric View.
    String out = stdout("export", write("model.yaml", OSSIE_MODEL).toString());
    assertTrue(out.contains("version: \"1.1\""), "expected a Metric View, got:\n" + out);
    assertTrue(out.contains("cat.sch.orders"), "expected the source, got:\n" + out);
  }

  @Test
  public void importConvertsAMetricViewToAnOssieModel() throws IOException {
    String out = stdout("import", write("view.yaml", METRIC_VIEW).toString());
    assertTrue(out.contains("semantic_model"), "expected an Apache Ossie model, got:\n" + out);
    assertTrue(out.contains("0.2.0.dev0"), "expected the Apache Ossie version, got:\n" + out);
  }

  @Test
  public void stdoutCarriesTheSameBytesAsTheOutputFile() throws IOException {
    Path model = write("model.yaml", OSSIE_MODEL);
    String piped = stdout("export", model.toString());
    Path file = dir.resolve("view.yaml");
    stdout("export", model.toString(), "-o", file.toString());
    // println used to add a second newline, so piping the CLI produced different bytes than -o.
    assertEquals(Files.readString(file, StandardCharsets.UTF_8), piped,
        "stdout and -o must agree byte-for-byte");
  }

  @Test
  public void exportRejectsTheImportSelectorFlag() throws IOException {
    // --name is the import's flag. Both flags used to share one field, so this was silently
    // accepted and used as the model name -- an export naming its own output model.
    OssieDatabricksConverter.ExitException e =
        exitFrom("export", write("model.yaml", OSSIE_MODEL).toString(), "--name", "zzz");
    assertEquals(2, e.code);
    assertTrue(e.getMessage().contains("'--name' is not valid for 'export'"),
        "expected the flag to be rejected, got: " + e.getMessage());
  }

  @Test
  public void importRejectsTheExportSelectorFlag() throws IOException {
    OssieDatabricksConverter.ExitException e =
        exitFrom("import", write("view.yaml", METRIC_VIEW).toString(), "--source", "zzz");
    assertEquals(2, e.code);
    assertTrue(e.getMessage().contains("'--source' is not valid for 'import'"),
        "expected the flag to be rejected, got: " + e.getMessage());
  }

  @Test
  public void importUsesNameForTheModelName() throws IOException {
    String out = stdout("import", write("view.yaml", METRIC_VIEW).toString(), "--name", "custom");
    assertTrue(out.contains("name: \"custom\""), "expected the named model, got:\n" + out);
  }

  @Test
  public void helpPrintsUsage() {
    // Arguments used to be parsed before the command was looked at, so `--help` reported a
    // missing input file.
    String out = stdout("--help");
    assertTrue(out.contains("Usage:"), "expected usage, got:\n" + out);
    assertTrue(out.contains("export <model.yaml>"), "expected the export line, got:\n" + out);
  }

  @Test
  public void unknownCommandNamesItself() throws IOException {
    OssieDatabricksConverter.ExitException e =
        exitFrom("frobnicate", write("model.yaml", OSSIE_MODEL).toString());
    assertEquals(2, e.code);
    assertTrue(e.getMessage().contains("Unknown command 'frobnicate'"),
        "expected the command to be named, got: " + e.getMessage());
  }

  @Test
  public void missingInputFileIsReported() {
    OssieDatabricksConverter.ExitException e = exitFrom("export");
    assertEquals(2, e.code);
    assertTrue(e.getMessage().contains("Missing input file"),
        "expected a missing-input error, got: " + e.getMessage());
  }
}
