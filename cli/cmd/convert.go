package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var convertCmd = &cobra.Command{
	Use:   "convert --from <platform> --input <path> | --to <platform> --input <path>",
	Short: "Convert a semantic model between OSSIE and a platform format",
	RunE:  runConvert,
}

func init() {
	convertCmd.Flags().String("from", "", "Source platform — converts platform → OSSIE")
	convertCmd.Flags().String("to", "", "Target platform — converts OSSIE → platform")
	convertCmd.Flags().StringP("input", "i", "", "Input file or directory path (required)")
	convertCmd.Flags().StringP("output", "o", "", "Output directory path (default: ./ossie-output/<plugin>/<direction>)")
	convertCmd.Flags().String("plugin", "", "Path to plugin directory (bypasses name-based discovery)")
	convertCmd.Flags().Int("timeout", 60, "Plugin invocation timeout in seconds")
	convertCmd.Flags().String("max-input-size", "100MB", "Maximum total input size (e.g. 500MB)")

	_ = convertCmd.MarkFlagRequired("input")
	convertCmd.MarkFlagsMutuallyExclusive("from", "to")
	convertCmd.MarkFlagsOneRequired("from", "to")
}

func runConvert(cmd *cobra.Command, args []string) error {
	fmt.Fprintln(cmd.OutOrStdout(), "not yet implemented")
	return nil
}
