package cmd

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/open-semantic-interchange/ossie/cli/internal/ossiedir"
	"github.com/open-semantic-interchange/ossie/cli/internal/plugin"
	"github.com/open-semantic-interchange/ossie/cli/internal/schema"
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
}

func runConvert(cmd *cobra.Command, args []string) error {
	// --- 1. Parse flags ---
	from, _ := cmd.Flags().GetString("from")
	to, _ := cmd.Flags().GetString("to")
	if from == "" && to == "" {
		return fmt.Errorf("exactly one of --from or --to must be specified")
	}

	input, _ := cmd.Flags().GetString("input")
	outputFlag, _ := cmd.Flags().GetString("output")
	pluginFlag, _ := cmd.Flags().GetString("plugin")
	timeoutSecs, _ := cmd.Flags().GetInt("timeout")
	maxInputSizeStr, _ := cmd.Flags().GetString("max-input-size")
	// verbose is a PersistentFlag on root; Cobra injects it into child flag sets.
	verbose, _ := cmd.Flags().GetBool("verbose")

	// --- 2. Resolve plugin ---
	var p *plugin.Plugin
	if pluginFlag != "" {
		loaded, err := plugin.LoadPlugin(pluginFlag)
		if err != nil {
			return fmt.Errorf("could not load plugin from %q: %w", pluginFlag, err)
		}
		p = loaded
	} else {
		platformName := from
		if to != "" {
			platformName = to
		}
		pluginsDir, err := ossiedir.PluginDir()
		if err != nil {
			return err
		}
		plugins, err := plugin.Discover(pluginsDir, cmd.ErrOrStderr())
		if err != nil {
			return err
		}
		var matches []*plugin.Plugin
		for _, candidate := range plugins {
			if candidate.Platform.Name == platformName {
				matches = append(matches, candidate)
			}
		}
		switch len(matches) {
		case 0:
			return fmt.Errorf("no plugin found for %q; run 'ossie plugin install %s' to install it", platformName, platformName)
		case 1:
			p = matches[0]
		default:
			return fmt.Errorf("multiple plugins found for %q; use --plugin to specify a path", platformName)
		}
	}

	// --- 3. Select direction ---
	var direction string
	var dir plugin.Direction
	if from != "" {
		direction = "to_osi"
		dir = p.Convert.ToOSI
	} else {
		direction = "from_osi"
		dir = p.Convert.FromOSI
	}

	// --- 4. Parse max-input-size ---
	maxBytes, err := parseMaxInputSize(maxInputSizeStr)
	if err != nil {
		return fmt.Errorf("invalid --max-input-size %q: %w", maxInputSizeStr, err)
	}

	// --- 5. Collect input files ---
	files, err := plugin.CollectFiles(input, dir.Accepts, maxBytes)
	if err != nil {
		return fmt.Errorf("input collection failed: %w", err)
	}

	// --- 6. Build context with timeout ---
	ctx := cmd.Context()
	if ctx == nil {
		ctx = context.Background()
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(timeoutSecs)*time.Second)
	defer cancel()

	// --- 7. Route plugin stderr ---
	var pluginStderr io.Writer = io.Discard
	if verbose {
		pluginStderr = cmd.ErrOrStderr()
	}

	// --- 8. Invoke plugin ---
	resp, err := plugin.Invoke(ctx, p.Path, dir.Invoke, plugin.Request{Files: files}, pluginStderr)
	if err != nil {
		return fmt.Errorf("plugin invocation failed: %w", err)
	}

	// --- 9. Validate output (to_osi direction only) ---
	// Schema validation runs before any files are written to disk.
	// Files that fail validation are removed from resp.Files and reported
	// as error-severity issues.
	if direction == "to_osi" {
		for name, content := range resp.Files {
			if verr := schema.Validate([]byte(content)); verr != nil {
				resp.Issues = append(resp.Issues, plugin.Issue{
					Severity: "error",
					Path:     name,
					Message:  fmt.Sprintf("output validation failed: %v", verr),
				})
				delete(resp.Files, name)
			}
		}
	}

	// --- 10. Write output files ---
	outDir := outputFlag
	if outDir == "" {
		outDir = filepath.Join("ossie-output", p.Platform.Name, direction)
	}
	if len(resp.Files) > 0 {
		if err := os.MkdirAll(outDir, 0755); err != nil {
			return fmt.Errorf("could not create output directory %q: %w", outDir, err)
		}
	}
	for name, content := range resp.Files {
		dest := filepath.Join(outDir, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(dest), 0755); err != nil {
			return fmt.Errorf("could not create output subdirectory for %q: %w", name, err)
		}
		if err := os.WriteFile(dest, []byte(content), 0644); err != nil {
			return fmt.Errorf("could not write output file %q: %w", dest, err)
		}
	}

	// --- 11. Render issues and set exit code ---
	if hasErrors := renderIssues(resp.Issues, cmd.ErrOrStderr(), verbose); hasErrors {
		return fmt.Errorf("conversion completed with errors")
	}
	return nil
}

// parseMaxInputSize parses a size string like "100MB", "2GB", or a raw integer
// (interpreted as bytes). The suffix is case-insensitive.
func parseMaxInputSize(s string) (int64, error) {
	upper := strings.ToUpper(strings.TrimSpace(s))
	if strings.HasSuffix(upper, "GB") {
		n, err := strconv.ParseInt(strings.TrimSuffix(upper, "GB"), 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid GB value: %w", err)
		}
		return n * 1024 * 1024 * 1024, nil
	}
	if strings.HasSuffix(upper, "MB") {
		n, err := strconv.ParseInt(strings.TrimSuffix(upper, "MB"), 10, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid MB value: %w", err)
		}
		return n * 1024 * 1024, nil
	}
	n, err := strconv.ParseInt(upper, 10, 64)
	if err != nil {
		return 0, fmt.Errorf("unsupported size format %q (use e.g. 100MB, 2GB, or a raw byte count)", s)
	}
	return n, nil
}

// renderIssues writes issues to w and returns true if any error-severity issue
// is present. info-severity issues are suppressed unless verbose is true.
func renderIssues(issues []plugin.Issue, w io.Writer, verbose bool) bool {
	hasErrors := false
	for _, iss := range issues {
		if iss.Severity == "info" && !verbose {
			continue
		}
		if iss.Path != "" {
			fmt.Fprintf(w, "%s: %s: %s\n", iss.Severity, iss.Path, iss.Message)
		} else {
			fmt.Fprintf(w, "%s: %s\n", iss.Severity, iss.Message)
		}
		if iss.Severity == "error" {
			hasErrors = true
		}
	}
	return hasErrors
}
