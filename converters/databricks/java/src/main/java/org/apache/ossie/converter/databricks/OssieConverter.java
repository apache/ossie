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

import java.util.ArrayList;
import java.util.List;

/**
 * Bidirectional converter between Apache Ossie semantic models and Databricks Metric Views.
 * This class is the public facade, re-exporting the two conversion entry points and the shared
 * {@link ConversionException}/{@link Notices}/{@link Result} types. The implementation is split
 * by direction so each half can be read on its own:
 *
 * <ul>
 *   <li>{@link OssieConverterCommon} -- shared constants, YAML I/O, and map accessors
 *   <li>{@link OssieToMetricView} -- EXPORT: Apache Ossie -&gt; Metric View
 *   <li>{@link MetricViewToOssie} -- IMPORT: Metric View -&gt; Apache Ossie
 * </ul>
 *
 * <p>The authoritative contract is Metric View YAML v1.1 as defined by the Databricks serde
 * ({@code com.databricks.sql.serde.v11}) and its validation rules; the checked-in YAML fixtures
 * pin the expected output for both directions.
 *
 * <p>Conversion operates on parsed YAML as plain maps and lists rather than typed models, so the
 * converter stays independent of the Databricks serde classes and runs standalone. Warnings
 * ("drops") are collected into a Notices buffer and returned rather than written to stderr, so a
 * SQL surface can present them to the caller.
 */
public final class OssieConverter {

  // Re-exported so callers can reference OssieConverter.OSSIE_VERSION / .MV_VERSION as before.
  public static final String OSSIE_VERSION = OssieConverterCommon.OSSIE_VERSION;
  public static final String MV_VERSION = OssieConverterCommon.MV_VERSION;

  private OssieConverter() {}

  /** Raised for any input the converter refuses to convert (Java twin of ConversionError). */
  public static final class ConversionException extends RuntimeException {
    /** Stable failure categories for callers that need structured error handling. */
    public enum Kind {
      INVALID_INPUT,
      UNSUPPORTED_VERSION,
      NOT_REPRESENTABLE,
      INTERNAL_ERROR
    }

    private final Kind kind;
    private final String reason;
    private final String unsupportedVersion;

    public ConversionException(String message) {
      this(Kind.NOT_REPRESENTABLE, message, message, null, null);
    }

    public ConversionException(String message, Throwable cause) {
      this(Kind.NOT_REPRESENTABLE, message, message, null, cause);
    }

    private ConversionException(
        Kind kind, String message, String reason, String unsupportedVersion, Throwable cause) {
      super(message, cause);
      this.kind = kind;
      this.reason = reason;
      this.unsupportedVersion = unsupportedVersion;
    }

    static ConversionException invalidInput(String message, String reason) {
      return new ConversionException(Kind.INVALID_INPUT, message, reason, null, null);
    }

    static ConversionException invalidInput(String message, String reason, Throwable cause) {
      return new ConversionException(Kind.INVALID_INPUT, message, reason, null, cause);
    }

    static ConversionException unsupportedVersion(String message, String version) {
      return new ConversionException(Kind.UNSUPPORTED_VERSION, message, message, version, null);
    }

    static ConversionException internalError(String message, Throwable cause) {
      return new ConversionException(Kind.INTERNAL_ERROR, message, message, null, cause);
    }

    public Kind getKind() {
      return kind;
    }

    public String getReason() {
      return reason;
    }

    public String getUnsupportedVersion() {
      return unsupportedVersion;
    }
  }

  /** Collects drop/rewrite notices during a conversion. */
  public static final class Notices {
    private final List<String> messages = new ArrayList<>();
    void warn(String scope, String msg) {
      messages.add("[" + scope + "] " + msg);
    }
    public List<String> toList() {
      return new ArrayList<>(messages);
    }
  }

  /** Result of a conversion: the emitted YAML plus any drop notices. */
  public static final class Result {
    public final String yaml;
    public final List<String> notices;
    Result(String yaml, List<String> notices) {
      this.yaml = yaml;
      this.notices = notices;
    }
  }

  /** EXPORT: Apache Ossie semantic model YAML -&gt; Metric View v1.1 YAML. */
  public static Result convertOssieToMetricView(String osiYamlStr, String source) {
    return OssieToMetricView.convertOssieToMetricView(osiYamlStr, source);
  }

  /** IMPORT: Metric View v1.1 YAML -&gt; Apache Ossie semantic model YAML. */
  public static Result convertMetricViewToOssie(String mvYamlStr, String modelName) {
    return MetricViewToOssie.convertMetricViewToOssie(mvYamlStr, modelName);
  }

  /** Parse YAML text into a plain value (YAML 1.2 booleans, matching the converter). */
  public static Object parseYaml(String s) {
    return OssieConverterCommon.parseYaml(s);
  }

  /** Serialize a value to YAML using the converter's write mapper (for tests without their
   * own jackson-yaml import). */
  public static String dumpYaml(Object obj) {
    return OssieConverterCommon.dumpYaml(obj);
  }
}
