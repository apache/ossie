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
	"testing"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
)

// execute drives the CLI through rootCmd.Execute. Calling ValidateArgs
// directly is not enough: it passes on the non-runnable plugin parent
// while the real CLI exits 0 (#345).
func execute(t *testing.T, args ...string) error {
	t.Helper()
	out := &bytes.Buffer{}
	rootCmd.SetOut(out)
	rootCmd.SetErr(out)
	rootCmd.SetArgs(args)
	err := rootCmd.Execute()
	resetFlags(rootCmd)
	return err
}

// Flag values persist across Execute calls, so reset them between cases.
func resetFlags(cmd *cobra.Command) {
	cmd.Flags().Visit(func(f *pflag.Flag) {
		_ = f.Value.Set(f.DefValue)
		f.Changed = false
	})
	for _, sub := range cmd.Commands() {
		resetFlags(sub)
	}
}

func TestArgumentValidation(t *testing.T) {
	t.Setenv("HOME", t.TempDir())

	tests := []struct {
		name    string
		args    []string
		wantErr bool
	}{
		// The five reproductions from #345.
		{name: "plugin unknown subcommand", args: []string{"plugin", "bogus"}, wantErr: true},
		{name: "plugin help routed as args", args: []string{"plugin", "help", "list"}, wantErr: true},
		{name: "plugin install extra args", args: []string{"plugin", "install", "a", "b", "c"}, wantErr: true},
		{name: "plugin install no name no --all", args: []string{"plugin", "install"}, wantErr: true},
		{name: "convert trailing arg", args: []string{"convert", "--from", "x", "--input", "y", "extra"}, wantErr: true},
		{name: "plugin bare", args: []string{"plugin"}, wantErr: true},
		{name: "plugin install --all with name", args: []string{"plugin", "install", "--all", "foo"}, wantErr: true},
		// Valid invocations keep working.
		{name: "plugin list", args: []string{"plugin", "list"}},
		{name: "plugin install by name", args: []string{"plugin", "install", "foo"}},
		{name: "plugin install --all", args: []string{"plugin", "install", "--all"}},
		{name: "convert flags only", args: []string{"convert", "--from", "x", "--input", "y"}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := execute(t, tt.args...)
			if (err != nil) != tt.wantErr {
				t.Fatalf("Execute(%q) error = %v, wantErr %v", tt.args, err, tt.wantErr)
			}
		})
	}
}

// Every command needs an Args validator, and every parent needs to be
// runnable, or cobra silently accepts arbitrary arguments (#345).
func TestEveryCommandValidatesArgs(t *testing.T) {
	var walk func(cmd *cobra.Command)
	walk = func(cmd *cobra.Command) {
		for _, sub := range cmd.Commands() {
			if sub.Name() == "help" || sub.Name() == "completion" {
				continue
			}
			if sub.HasSubCommands() {
				if !sub.Runnable() {
					t.Errorf("%s: parent is not runnable, unknown subcommands would exit 0", sub.CommandPath())
				}
				walk(sub)
			}
			if sub.Args == nil {
				t.Errorf("%s: no Args validator", sub.CommandPath())
			}
		}
	}
	walk(rootCmd)
}
