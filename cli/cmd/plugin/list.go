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
	"github.com/apache/ossie/cli/internal/registry"
)

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List available and installed plugins",
	RunE:  runPluginList,
}

func runPluginList(cmd *cobra.Command, args []string) error {
	reg, err := registry.Load()
	if err != nil {
		return err
	}

	pluginsDir, err := ossiedir.PluginDir()
	if err != nil {
		return err
	}

	installed, err := plugin.Discover(pluginsDir, os.Stderr)
	if err != nil {
		return err
	}

	// Index installed plugins by name for O(1) lookup. Plugin.Name is the
	// identity matched against --from/--to values and against registry
	// platform keys; Plugin.Platform is only a freeform display label.
	installedByPlatform := make(map[string]*plugin.Plugin, len(installed))
	for _, p := range installed {
		installedByPlatform[p.Name] = p
	}

	// Identify community plugins: installed but absent from the registry.
	var community []*plugin.Plugin
	for _, p := range installed {
		if _, ok := reg.LatestEntry(p.Name); !ok {
			community = append(community, p)
		}
	}

	platforms := reg.Platforms() // sorted alphabetically
	out := cmd.OutOrStdout()

	if len(platforms) == 0 && len(installed) == 0 {
		fmt.Fprintln(out, "no plugins available")
		return nil
	}

	if len(platforms) > 0 {
		w := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "NAME\tSTATUS\tINSTALLED\tLATEST")
		for _, platform := range platforms {
			latest, _ := reg.LatestEntry(platform)
			p, isInstalled := installedByPlatform[platform]

			var status, installedVer string
			if isInstalled {
				installedVer = p.OSSIEPluginSpec
				if installedVer == latest.Version {
					status = "installed"
				} else {
					status = "update available"
				}
			} else {
				installedVer = "—"
				status = "not installed"
			}

			fmt.Fprintf(w, "%s\t%s\t%s\t%s\n", platform, status, installedVer, latest.Version)
		}
		if err := w.Flush(); err != nil {
			return err
		}
	}

	if len(community) > 0 {
		fmt.Fprintln(out)
		fmt.Fprintln(out, "Community plugins:")
		w := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
		fmt.Fprintln(w, "NAME\tINSTALLED")
		for _, p := range community {
			fmt.Fprintf(w, "%s\t%s\n", p.Name, p.OSSIEPluginSpec)
		}
		if err := w.Flush(); err != nil {
			return err
		}
	}

	return nil
}
