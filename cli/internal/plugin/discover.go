package plugin

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

const pluginYAML = "plugin.yaml"

// Discover scans pluginsDir for subdirectories that contain a plugin.yaml,
// parses and validates each one, and returns all valid plugins.
//
// Malformed or invalid plugin directories are skipped with a warning written
// to stderr in the format:
//
//	warning: skipping plugin at <path>: <reason>
//
// A non-existent pluginsDir is treated as empty and returns (nil, nil).
// A hard error is only returned if pluginsDir itself cannot be read.
func Discover(pluginsDir string, stderr io.Writer) ([]*Plugin, error) {
	entries, err := os.ReadDir(pluginsDir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, fmt.Errorf("could not read plugin directory %s: %w", pluginsDir, err)
	}

	var plugins []*Plugin
	for _, entry := range entries {
		// TODO: handle symlinked plugin directories
		// (entry.Type()&os.ModeSymlink != 0 requires os.Stat to resolve)
		if !entry.IsDir() {
			continue
		}
		pluginPath := filepath.Join(pluginsDir, entry.Name())
		p, err := loadPlugin(pluginPath)
		if err != nil {
			fmt.Fprintf(stderr, "warning: skipping plugin at %s: %s\n", pluginPath, err)
			continue
		}
		plugins = append(plugins, p)
	}
	return plugins, nil
}

// loadPlugin reads, parses, and validates the plugin.yaml inside dir.
func loadPlugin(dir string) (*Plugin, error) {
	yamlPath := filepath.Join(dir, pluginYAML)
	data, err := os.ReadFile(yamlPath)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, fmt.Errorf("no plugin.yaml found")
		}
		return nil, fmt.Errorf("could not read plugin.yaml: %w", err)
	}

	// yaml.Unmarshal is lenient by default: unknown fields are silently ignored.
	// This is intentional — future spec versions may add fields that older CLI
	// versions should tolerate rather than reject.
	var raw rawPlugin
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("invalid YAML: %w", err)
	}

	if err := raw.validate(); err != nil {
		return nil, err
	}

	return raw.toPlugin(dir), nil
}
