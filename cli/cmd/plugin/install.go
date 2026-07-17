package plugin

import (
	"fmt"

	"github.com/spf13/cobra"
)

var installCmd = &cobra.Command{
	Use:   "install [name[@version] | url]",
	Short: "Install a plugin from the registry or a URL",
	RunE:  runPluginInstall,
}

func init() {
	installCmd.Flags().Bool("all", false, "Install the latest version of all registry plugins")
}

func runPluginInstall(cmd *cobra.Command, args []string) error {
	fmt.Fprintln(cmd.OutOrStdout(), "not yet implemented")
	return nil
}
