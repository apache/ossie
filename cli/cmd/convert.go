package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var convertCmd = &cobra.Command{
	Use:   "convert --from <platform> --input <path> | --to <platform> --input <path>",
	Short: "Convert a semantic model between OSI and a platform format",
	RunE:  runConvert,
}

func init() {
	convertCmd.Flags().String("from", "", "Source platform — converts platform → OSI")
	convertCmd.Flags().String("to", "", "Target platform — converts OSI → platform")
	convertCmd.Flags().StringP("input", "i", "", "Input file or directory path (required)")
	convertCmd.Flags().StringP("output", "o", "", "Output directory path (default: ./osi-output/<plugin>/<direction>)")
	convertCmd.Flags().String("plugin", "", "Path to plugin directory (bypasses name-based discovery)")
	convertCmd.Flags().Int("timeout", 60, "Plugin invocation timeout in seconds")
	convertCmd.Flags().String("max-input-size", "100MB", "Maximum total input size (e.g. 500MB)")

	_ = convertCmd.MarkFlagRequired("input")
	convertCmd.MarkFlagsMutuallyExclusive("from", "to")
}

func runConvert(cmd *cobra.Command, args []string) error {
	from, _ := cmd.Flags().GetString("from")
	to, _ := cmd.Flags().GetString("to")

	// MarkFlagsMutuallyExclusive handles the both-set case; handle neither here.
	if from == "" && to == "" {
		return fmt.Errorf("exactly one of --from or --to must be specified")
	}

	fmt.Fprintln(cmd.OutOrStdout(), "not yet implemented")
	return nil
}
