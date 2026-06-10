package osidir

import (
	"fmt"
	"os"
	"path/filepath"
)

const (
	defaultOSIDir = ".ossie"
	pluginsSubdir = "plugins"
	envVar        = "OSSIE_PLUGIN_DIR"
)

// PluginDir returns the resolved plugin directory path.
// It respects $OSSIE_PLUGIN_DIR if set, otherwise defaults to ~/.ossie/plugins/.
func PluginDir() (string, error) {
	if override := os.Getenv(envVar); override != "" {
		return override, nil
	}
	// Use os.UserHomeDir rather than $HOME for Windows portability.
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("could not determine home directory: %w", err)
	}
	return filepath.Join(home, defaultOSIDir, pluginsSubdir), nil
}

// EnsurePluginDir ensures the plugin directory exists, creating it if needed.
// It is safe to call multiple times — os.MkdirAll is idempotent.
func EnsurePluginDir() error {
	dir, err := PluginDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("could not create plugin directory %s: %w", dir, err)
	}
	return nil
}
