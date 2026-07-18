package ossiedir

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPluginDir_envOverride(t *testing.T) {
	want := "/custom/plugin/dir"
	t.Setenv(envVar, want)

	got, err := PluginDir()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestPluginDir_default(t *testing.T) {
	t.Setenv(envVar, "")

	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("could not determine home dir: %v", err)
	}
	want := filepath.Join(home, defaultOssieDir, pluginsSubdir)

	got, err := PluginDir()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestEnsurePluginDir_createsDirectory(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "plugins")
	t.Setenv(envVar, target)

	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if _, err := os.Stat(target); os.IsNotExist(err) {
		t.Errorf("expected directory %q to exist, but it does not", target)
	}
}

func TestEnsurePluginDir_idempotent(t *testing.T) {
	tmp := t.TempDir()
	target := filepath.Join(tmp, "plugins")
	t.Setenv(envVar, target)

	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("first call failed: %v", err)
	}
	if err := EnsurePluginDir(); err != nil {
		t.Fatalf("second call failed: %v", err)
	}
}
