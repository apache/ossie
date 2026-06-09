package cmd

import (
	"github.com/open-semantic-interchange/osi/cli/cmd/plugin"
	"github.com/open-semantic-interchange/osi/cli/internal/osidir"
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "osi",
	Short: "Open Semantic Interchange CLI",
	Long:  `osi is the command-line tool for the Open Semantic Interchange (OSI) project.`,
	// NOTE: Cobra does NOT automatically chain PersistentPreRunE from parent to
	// child. If any subcommand defines its own PersistentPreRunE or PreRunE, this
	// function will not run for that subcommand. Future subcommands that define
	// their own must call osidir.EnsurePluginDir() explicitly.
	PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
		return osidir.EnsurePluginDir()
	},
}

// Execute runs the root command. Called by main.
func Execute() error {
	return rootCmd.Execute()
}

// SetVersion sets the version string reported by `osi --version`.
func SetVersion(v string) {
	rootCmd.Version = v
}

func init() {
	rootCmd.PersistentFlags().BoolP("verbose", "v", false, "Enable verbose output (shows plugin stderr)")

	rootCmd.AddCommand(convertCmd)
	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(plugin.Cmd)
}
