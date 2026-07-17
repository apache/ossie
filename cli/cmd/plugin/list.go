package plugin

import (
	"fmt"

	"github.com/spf13/cobra"
)

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List installed and available plugins",
	RunE:  runPluginList,
}

func runPluginList(cmd *cobra.Command, args []string) error {
	fmt.Fprintln(cmd.OutOrStdout(), "not yet implemented")
	return nil
}
