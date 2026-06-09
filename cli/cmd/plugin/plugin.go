package plugin

import "github.com/spf13/cobra"

// Cmd is the parent "osi plugin" command. It is exported so cmd/root.go can
// register it. Invoking it bare prints help.
var Cmd = &cobra.Command{
	Use:   "plugin",
	Short: "Manage OSI plugins",
}

func init() {
	Cmd.AddCommand(listCmd)
	Cmd.AddCommand(installCmd)
	Cmd.AddCommand(removeCmd)
}
