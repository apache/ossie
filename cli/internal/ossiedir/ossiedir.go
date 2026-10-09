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
	"fmt"
	"os"
	"path/filepath"
)

const (
	defaultOssieDir = ".ossie"
	pluginsSubdir   = "plugins"
	envVar          = "OSSIE_PLUGIN_DIR"
)

// PluginDir returns the resolved plugin directory path.
// It respects $OSSIE_PLUGIN_DIR if set, otherwise defaults to ~/.ossie/plugins/.
func PluginDir() (string, error) {
	if override := os.Getenv(envVar); override != "" {
		return override, nil
	}
	// Use os.UserHomeDir rather than $HOME for Windows portability.
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not determine home directory: %w", err)
	}
	return filepath.Join(home, defaultOssieDir, pluginsSubdir), nil
}

// EnsurePluginDir ensures the plugin directory exists, creating it if needed.
// It is safe to call multiple times — os.MkdirAll is idempotent.
func EnsurePluginDir() error {
	dir, err := PluginDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("could not create plugin directory %s: %w", dir, err)
	}
	return nil
}
