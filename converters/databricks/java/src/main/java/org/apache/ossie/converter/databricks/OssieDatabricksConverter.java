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

import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * Command-line entry point for the converter between Apache Ossie and Databricks Metric Views.
 *
 * <pre>{@code
 *   ossie-databricks export <model.yaml> [-o <view.yaml>] [--source <dataset>]
 *   ossie-databricks import <view.yaml>  [-o <model.yaml>] [--name <model>]
 * }</pre>
 *
 * <p>The directions are named from the Apache Ossie model's point of view, matching
 * {@link OssieConverter} and the Python converter: {@code export} converts an Apache Ossie semantic
 * model to a Metric View; {@code import} converts a Metric View to an Apache Ossie model. Output
 * goes to the {@code -o} file, or to stdout when omitted. Conversion notices (features dropped on
 * export) are written to stderr. A broken input raises
 * {@link OssieConverter.ConversionException}, reported as a non-zero exit.
 *
 * <p>This is a thin command-line wrapper around {@link OssieConverter}: it parses arguments, reads
 * the input YAML, invokes the library, and writes the result. Programmatic callers should use
 * {@link OssieConverter} directly.
 */
public final class OssieDatabricksConverter {

  private OssieDatabricksConverter() {}

  public static void main(String[] args) {
    try {
      run(args, System.out, System.err);
    } catch (ExitException e) {
      System.err.println(e.getMessage());
      System.exit(e.code);
    } catch (OssieConverter.ConversionException e) {
      System.err.println("Conversion failed: " + e.getMessage());
      System.exit(1);
    }
  }

  /** Testable core: parses args, runs the conversion, and writes output. */
  static void run(String[] args, PrintStream out, PrintStream err) {
    if (args.length == 0) {
      throw new ExitException(2, usage());
    }
    String first = args[0];
    if ("-h".equals(first) || "--help".equals(first) || "help".equals(first)) {
      out.println(usage());
      return;
    }
    // Resolve the command before parsing the rest, so an unknown command reports itself rather
    // than whatever the argument parser trips over first.
    Command command = Command.parse(first);
    Args parsed = Args.parse(args, command);

    String input = read(parsed.inputPath);
    OssieConverter.Result result =
        switch (command) {
          // `--source` picks the fact/grain (optional).
          case EXPORT -> OssieConverter.convertOssieToMetricView(input, parsed.option);
          // `--name` sets the model name (optional).
          case IMPORT -> OssieConverter.convertMetricViewToOssie(input, parsed.option);
        };

    write(parsed.outputPath, result.yaml, out);
    List<String> notices = result.notices;
    if (!notices.isEmpty()) {
      err.println("Conversion notices (" + notices.size() + "):");
      for (String notice : notices) {
        err.println("  " + notice);
      }
    }
  }

  private static String read(String path) {
    try {
      return Files.readString(Path.of(path), StandardCharsets.UTF_8);
    } catch (IOException e) {
      throw new ExitException(1, "Cannot read input file '" + path + "': " + e.getMessage());
    }
  }

  private static void write(String path, String content, PrintStream out) {
    if (path == null) {
      // print, not println: the serialized YAML already ends in a newline, so stdout and `-o`
      // produce the same bytes.
      out.print(content);
      out.flush();
      return;
    }
    try {
      Files.writeString(Path.of(path), content, StandardCharsets.UTF_8);
    } catch (IOException e) {
      throw new ExitException(1, "Cannot write output file '" + path + "': " + e.getMessage());
    }
  }

  private static String usage() {
    return "Usage:\n"
        + "  ossie-databricks export <model.yaml> [-o <view.yaml>] [--source <dataset>]\n"
        + "  ossie-databricks import <view.yaml>  [-o <model.yaml>] [--name <model>]";
  }

  /**
   * The two conversion directions, named from the Apache Ossie model's point of view. Each command
   * accepts exactly one selector flag, so passing the other one is an error rather than a silent
   * reinterpretation.
   */
  private enum Command {
    /** Apache Ossie -&gt; Metric View. */
    EXPORT("export", "--source"),
    /** Metric View -&gt; Apache Ossie. */
    IMPORT("import", "--name");

    final String name;
    final String optionFlag;

    Command(String name, String optionFlag) {
      this.name = name;
      this.optionFlag = optionFlag;
    }

    static Command parse(String arg) {
      for (Command command : values()) {
        if (command.name.equals(arg)) {
          return command;
        }
      }
      throw new ExitException(2, "Unknown command '" + arg + "'.\n" + usage());
    }
  }

  /** Parsed command-line arguments: the input file, an optional output file, and the option. */
  private static final class Args {
    final String inputPath;
    final String outputPath;
    final String option;

    private Args(String inputPath, String outputPath, String option) {
      this.inputPath = inputPath;
      this.outputPath = outputPath;
      this.option = option;
    }

    static Args parse(String[] args, Command command) {
      String inputPath = null;
      String outputPath = null;
      String option = null;
      for (int i = 1; i < args.length; i++) {
        String arg = args[i];
        switch (arg) {
          case "-o":
          case "--output":
            outputPath = requireValue(args, ++i, arg);
            break;
          case "--source":
          case "--name":
            if (!arg.equals(command.optionFlag)) {
              throw new ExitException(
                  2,
                  "Option '"
                      + arg
                      + "' is not valid for '"
                      + command.name
                      + "'; use '"
                      + command.optionFlag
                      + "'.\n"
                      + usage());
            }
            option = requireValue(args, ++i, arg);
            break;
          default:
            if (arg.startsWith("-")) {
              throw new ExitException(2, "Unknown option '" + arg + "'.\n" + usage());
            }
            if (inputPath != null) {
              throw new ExitException(2, "Unexpected extra argument '" + arg + "'.\n" + usage());
            }
            inputPath = arg;
        }
      }
      if (inputPath == null) {
        throw new ExitException(2, "Missing input file.\n" + usage());
      }
      return new Args(inputPath, outputPath, option);
    }

    private static String requireValue(String[] args, int index, String flag) {
      if (index >= args.length) {
        throw new ExitException(2, "Option '" + flag + "' requires a value.\n" + usage());
      }
      return args[index];
    }
  }

  /** Signals a clean CLI exit with a message and status code (kept out of the library core). */
  static final class ExitException extends RuntimeException {
    final int code;

    ExitException(int code, String message) {
      super(message);
      this.code = code;
    }
  }
}
