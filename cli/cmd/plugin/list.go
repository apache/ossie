package plugin

import (
	"fmt"
	"os"
	"path/filepath"
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
		name := filepath.Base(p.Path)
		fmt.Fprintf(w, "%s\t%s\t%s\n", name, p.Platform.Name, p.OSSIEPluginSpec)
	}
	return w.Flush()
}
