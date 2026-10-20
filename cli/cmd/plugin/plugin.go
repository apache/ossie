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

package plugin

import (
	"errors"

	"github.com/spf13/cobra"
)

// Cmd is the parent "ossie plugin" command. It is exported so cmd/root.go can
// register it.
//
// The RunE matters: cobra never reaches ValidateArgs on a non-runnable
// command, so without it a bare "ossie plugin" or an unknown subcommand
// prints help and exits 0 (#345).
var Cmd = &cobra.Command{
	Use:   "plugin",
	Short: "Manage Ossie plugins",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		return errors.New("a subcommand is required")
	},
}

func init() {
	Cmd.AddCommand(listCmd)
	Cmd.AddCommand(installCmd)
	Cmd.AddCommand(removeCmd)
}
