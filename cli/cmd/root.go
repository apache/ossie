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
	"github.com/apache/ossie/cli/cmd/plugin"
	"github.com/apache/ossie/cli/internal/ossiedir"
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "ossie",
	Short: "Apache Ossie (incubating) CLI",
	Long:  `ossie is the command-line tool for the Apache Ossie (incubating) project.`,
	// NOTE: Cobra does NOT automatically chain PersistentPreRunE from parent to
	// child. If any subcommand defines its own PersistentPreRunE or PreRunE, this
	// function will not run for that subcommand. Future subcommands that define
	// their own must call ossiedir.EnsurePluginDir() explicitly.
	PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
		return ossiedir.EnsurePluginDir()
	},
}

// Execute runs the root command. Called by main.
func Execute() error {
	return rootCmd.Execute()
}

// SetVersion sets the version string reported by `ossie --version`.
func SetVersion(v string) {
	rootCmd.Version = v
}

func init() {
	rootCmd.PersistentFlags().BoolP("verbose", "v", false, "Enable verbose output (shows plugin stderr)")

	rootCmd.AddCommand(convertCmd)
	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(plugin.Cmd)
}
