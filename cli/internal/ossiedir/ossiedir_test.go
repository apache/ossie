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

package ossiedir

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPluginDir_envOverride(t *testing.T) {
	want := "/custom/plugin/dir"
	t.Setenv(envVar, want)

	got, err := PluginDir()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestPluginDir_default(t *testing.T) {
	t.Setenv(envVar, "")

	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("could not determine home dir: %v", err)
	}
	want := filepath.Join(home, defaultOssieDir, pluginsSubdir)

	got, err := PluginDir()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestEnsurePluginDir_createsDirectory(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "plugins")
	t.Setenv(envVar, target)

	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if _, err := os.Stat(target); os.IsNotExist(err) {
		t.Errorf("expected directory %q to exist, but it does not", target)
	}
}

func TestEnsurePluginDir_idempotent(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "plugins")
	t.Setenv(envVar, target)

	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("first call failed: %v", err)
	}
	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("second call failed: %v", err)
	}
}
