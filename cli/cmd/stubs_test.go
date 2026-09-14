// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package cmd

import (
	"bytes"
	"strings"
	"testing"
)

func TestUnimplementedCommandsFail(t *testing.T) {
	t.Setenv("OSSIE_PLUGIN_DIR", t.TempDir())

	tests := [][]string{
		{"validate", "model.yaml"},
		{"convert", "--from", "dbt", "--input", "model.yaml"},
		{"plugin", "install", "dbt"},
		{"plugin", "remove", "dbt"},
	}

	for _, args := range tests {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			out := new(bytes.Buffer)
			rootCmd.SetOut(out)
			rootCmd.SetErr(out)
			rootCmd.SetArgs(args)

			err := rootCmd.Execute()
			if err == nil {
				t.Fatalf("Execute(%q) returned nil, want not-implemented error", args)
			}
			if !strings.Contains(err.Error(), "not yet implemented") {
				t.Errorf("Execute(%q) error = %q, want it to mention not yet implemented", args, err)
			}
			if strings.Contains(out.String(), "Usage:") {
				t.Errorf("Execute(%q) printed usage:\n%s", args, out.String())
			}
		})
	}
}
