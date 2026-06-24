package plugin

import (
	"fmt"
	"os"
	"text/tabwriter"

	"github.com/spf13/cobra"

	"github.com/open-semantic-interchange/ossie/cli/internal/ossiedir"
	"github.com/open-semantic-interchange/ossie/cli/internal/plugin"
	"github.com/open-semantic-interchange/ossie/cli/internal/registry"
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

	// Index installed plugins by platform name for O(1) lookup.
	installedByPlatform := make(map[string]*plugin.Plugin, len(installed))
	for _, p := range installed {
		installedByPlatform[p.Platform.Name] = p
	}

	// Identify community plugins: installed but absent from the registry.
	var community []*plugin.Plugin
	for _, p := range installed {
		if _, ok := reg.LatestEntry(p.Platform.Name); !ok {
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
			fmt.Fprintf(w, "%s\t%s\n", p.Platform.Name, p.OSSIEPluginSpec)
		}
		if err := w.Flush(); err != nil {
			return err
		}
	}

	return nil
}
