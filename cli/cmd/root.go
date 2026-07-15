package cmd

import (
	"github.com/apache/ossie/cli/cmd/plugin"
	"github.com/apache/ossie/cli/internal/ossiedir"
	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "ossie",
	Short: "Apache Ossie (incubating) CLI",
	Long:  `ossie is the command-line tool for the Apache Ossie (incubating) project.`,
	// NOTE: Cobra does NOT automatically chain PersistentPreRunE from parent to
	// child. If any subcommand defines its own PersistentPreRunE or PreRunE, this
	// function will not run for that subcommand. Future subcommands that define
	// their own must call ossiedir.EnsurePluginDir() explicitly.
	PersistentPreRunE: func(cmd *cobra.Command, args []string) error {
		return ossiedir.EnsurePluginDir()
	},
}

// Execute runs the root command. Called by main.
func Execute() error {
	return rootCmd.Execute()
}

// SetVersion sets the version string reported by `ossie --version`.
func SetVersion(v string) {
	rootCmd.Version = v
}

func init() {
	rootCmd.PersistentFlags().BoolP("verbose", "v", false, "Enable verbose output (shows plugin stderr)")

	rootCmd.AddCommand(convertCmd)
	rootCmd.AddCommand(validateCmd)
	rootCmd.AddCommand(plugin.Cmd)
}
