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
	"fmt"
	"os"
	"text/tabwriter"

	"github.com/spf13/cobra"

	"github.com/apache/ossie/cli/internal/ossiedir"
	"github.com/apache/ossie/cli/internal/plugin"
)

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List installed plugins",
	// TODO(P1): cross-reference against the embedded plugin registry to show
	// latest available versions and update indicators once registry embedding
	// (F4) is implemented.
	RunE: runPluginList,
}

func runPluginList(cmd *cobra.Command, args []string) error {
	pluginsDir, err := ossiedir.PluginDir()
	if err != nil {
		return err
	}

	plugins, err := plugin.Discover(pluginsDir, os.Stderr)
	if err != nil {
		return err
	}

	if len(plugins) == 0 {
		fmt.Fprintln(cmd.OutOrStdout(), "no plugins installed")
		return nil
	}

	w := tabwriter.NewWriter(cmd.OutOrStdout(), 0, 0, 2, ' ', 0)
	fmt.Fprintln(w, "NAME\tPLATFORM\tSPEC")
	for _, p := range plugins {
		fmt.Fprintf(w, "%s\t%s\t%s\n", p.Name, p.Platform, p.OSSIEPluginSpec)
	}
	return w.Flush()
}
