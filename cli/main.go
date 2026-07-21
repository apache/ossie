package main

import (
	"os"

	"github.com/apache/ossie/cli/cmd"
)

// version, commit, and date are set at build time by GoReleaser via ldflags.
var (
	version = "dev"
	commit  = "none"
	date    = "unknown"
)

func main() {
	cmd.SetVersion(version)
	if err := cmd.Execute(); err != nil {
		os.Exit(1)
	}
}
