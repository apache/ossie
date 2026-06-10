package plugin

import "github.com/spf13/cobra"

// Cmd is the parent "ossie plugin" command. It is exported so cmd/root.go can
// register it. Invoking it bare prints help.
var Cmd = &cobra.Command{
	Use:   "plugin",
	Short: "Manage OSSIE plugins",
}

func init() {
	Cmd.AddCommand(listCmd)
	Cmd.AddCommand(installCmd)
	Cmd.AddCommand(removeCmd)
}
