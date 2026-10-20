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

/**
 * Undoes the HTML escaping Salesforce applies to stored formula text.
 *
 * <p>The semantic model API accepts a formula verbatim but returns it escaped, so
 * {@code 'A&B'} comes back as {@code &#39;A&amp;B&#39;}. Importing that text as-is
 * produces an Ossie expression Salesforce itself rejects on the next export with
 * {@code Syntax Error - token recognition error at: '&'}.
 */
final class SalesforceFormulaEscaping {

    private SalesforceFormulaEscaping() {}

    /**
     * Decodes the five entities Salesforce emits. {@code &amp;} is decoded last so a
     * formula whose own text contains {@code &lt;} survives the round trip intact.
     */
    static String unescape(String formula) {
        if (formula == null || formula.indexOf('&') < 0) {
            return formula;
        }
        return formula
                .replace("&#39;", "'")
                .replace("&quot;", "\"")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&amp;", "&");
    }
}
