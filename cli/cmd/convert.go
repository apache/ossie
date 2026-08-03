// Licensed to the Apache Software Foundation (ASF) under one
// or more contributor license agreements.  See the NOTICE file
// distributed with this work for additional information
// regarding copyright ownership.  The ASF licenses this file
// to you under the Apache License, Version 2.0 (the
// "License"); you may not use this file except in compliance
// with the License.  You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

var convertCmd = &cobra.Command{
	Use:   "convert --from <platform> --input <path> | --to <platform> --input <path>",
	Short: "Convert a semantic model between Ossie and a platform format",
	RunE:  runConvert,
}

func init() {
	convertCmd.Flags().String("from", "", "Source platform — converts platform → Ossie")
	convertCmd.Flags().String("to", "", "Target platform — converts Ossie → platform")
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
