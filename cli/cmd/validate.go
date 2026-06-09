package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var validateCmd = &cobra.Command{
	Use:   "validate [flags] <path> [<path>...]",
	Short: "Validate one or more OSI YAML or JSON files",
	Args:  cobra.MinimumNArgs(1),
	RunE:  runValidate,
}

func init() {
	validateCmd.Flags().Bool("strict", false, "Promote warnings to errors")
	validateCmd.Flags().String("output", "text", "Output format: text or json")
}

func runValidate(cmd *cobra.Command, args []string) error {
	fmt.Fprintln(cmd.OutOrStdout(), "not yet implemented")
	return nil
}
