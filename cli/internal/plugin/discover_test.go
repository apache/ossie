package plugin_test

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/apache/ossie/cli/internal/plugin"
)

// validPluginYAML is the canonical plugin.yaml fixture using ossie_* keys.
const validPluginYAML = `
ossie_plugin_spec: "0.1.0"
ossie_spec_version: ">=0.2.0"
platform:
  name: dbt
  vendor: dbt Labs
convert:
  to_ossie:
    invoke: ["ossie-plugin-dbt", "to-ossie"]
    accepts: [".yaml", ".json"]
  from_ossie:
    invoke: ["ossie-plugin-dbt", "from-ossie"]
`

// writePlugin creates a plugin directory under root with the given plugin.yaml content.
func writePlugin(t *testing.T, root, name, content string) string {
	t.Helper()
	dir := filepath.Join(root, name)
	if err := os.MkdirAll(dir, 0755); err != nil {
		t.Fatalf("could not create plugin dir: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "plugin.yaml"), []byte(content), 0644); err != nil {
		t.Fatalf("could not write plugin.yaml: %v", err)
	}
	return dir
}

func TestDiscover_emptyDir(t *testing.T) {
	dir := t.TempDir()
	var stderr strings.Builder

	plugins, err := plugin.Discover(dir, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 0 {
		t.Errorf("expected no plugins, got %d", len(plugins))
	}
	if stderr.Len() != 0 {
		t.Errorf("expected no warnings, got: %q", stderr.String())
	}
}

func TestDiscover_nonExistentDir(t *testing.T) {
	var stderr strings.Builder

	plugins, err := plugin.Discover(filepath.Join(t.TempDir(), "nonexistent"), &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if plugins != nil {
		t.Errorf("expected nil slice, got %v", plugins)
	}
}

func TestDiscover_validPlugin(t *testing.T) {
	root := t.TempDir()
	pluginDir := writePlugin(t, root, "dbt", validPluginYAML)
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Fatalf("expected 1 plugin, got %d", len(plugins))
	}
	p := plugins[0]
	if p.Path != pluginDir {
		t.Errorf("Path: got %q, want %q", p.Path, pluginDir)
	}
	if p.Platform.Name != "dbt" {
		t.Errorf("Platform.Name: got %q, want %q", p.Platform.Name, "dbt")
	}
	if p.Platform.Vendor != "dbt Labs" {
		t.Errorf("Platform.Vendor: got %q, want %q", p.Platform.Vendor, "dbt Labs")
	}
	if p.OSSIEPluginSpec != "0.1.0" {
		t.Errorf("OSSIEPluginSpec: got %q, want %q", p.OSSIEPluginSpec, "0.1.0")
	}
	if p.OSSIESpecVersion != ">=0.2.0" {
		t.Errorf("OSSIESpecVersion: got %q, want %q", p.OSSIESpecVersion, ">=0.2.0")
	}
	wantInvoke := []string{"ossie-plugin-dbt", "to-ossie"}
	if !slices.Equal(p.Convert.ToOssie.Invoke, wantInvoke) {
		t.Errorf("ToOssie.Invoke: got %v, want %v", p.Convert.ToOssie.Invoke, wantInvoke)
	}
	wantAccepts := []string{".yaml", ".json"}
	if !slices.Equal(p.Convert.ToOssie.Accepts, wantAccepts) {
		t.Errorf("ToOssie.Accepts: got %v, want %v", p.Convert.ToOssie.Accepts, wantAccepts)
	}
	wantFromInvoke := []string{"ossie-plugin-dbt", "from-ossie"}
	if !slices.Equal(p.Convert.FromOssie.Invoke, wantFromInvoke) {
		t.Errorf("FromOssie.Invoke: got %v, want %v", p.Convert.FromOssie.Invoke, wantFromInvoke)
	}
	if stderr.Len() != 0 {
		t.Errorf("unexpected warning: %q", stderr.String())
	}
}

func TestDiscover_malformedYAML(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "bad", "this: is: not: valid: yaml: ][")
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 0 {
		t.Errorf("expected 0 plugins, got %d", len(plugins))
	}
	if !strings.Contains(stderr.String(), filepath.Join(root, "bad")) {
		t.Errorf("warning should contain plugin path, got: %q", stderr.String())
	}
}

func TestDiscover_missingRequiredField(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "noplat", `
ossie_plugin_spec: "0.1.0"
ossie_spec_version: ">=0.2.0"
platform:
  vendor: dbt Labs
convert:
  to_ossie:
    invoke: ["bin/convert"]
    accepts: [".yaml"]
  from_ossie:
    invoke: ["bin/convert"]
`)
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 0 {
		t.Errorf("expected 0 plugins, got %d", len(plugins))
	}
	if !strings.Contains(stderr.String(), filepath.Join(root, "noplat")) {
		t.Errorf("warning should contain plugin path, got: %q", stderr.String())
	}
}

func TestDiscover_missingPluginYAML(t *testing.T) {
	root := t.TempDir()
	// Create a directory with no plugin.yaml inside it.
	if err := os.MkdirAll(filepath.Join(root, "empty-plugin"), 0755); err != nil {
		t.Fatal(err)
	}
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 0 {
		t.Errorf("expected 0 plugins, got %d", len(plugins))
	}
	if !strings.Contains(stderr.String(), filepath.Join(root, "empty-plugin")) {
		t.Errorf("warning should contain plugin path, got: %q", stderr.String())
	}
}

func TestDiscover_multipleMixed(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "valid", validPluginYAML)
	writePlugin(t, root, "malformed", "bad: yaml: ][")
	if err := os.MkdirAll(filepath.Join(root, "no-yaml"), 0755); err != nil {
		t.Fatal(err)
	}
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Errorf("expected 1 plugin, got %d", len(plugins))
	}
	warnings := stderr.String()
	if !strings.Contains(warnings, filepath.Join(root, "malformed")) {
		t.Errorf("expected warning for malformed, got: %q", warnings)
	}
	if !strings.Contains(warnings, filepath.Join(root, "no-yaml")) {
		t.Errorf("expected warning for no-yaml, got: %q", warnings)
	}
}

func TestDiscover_unknownFieldsIgnored(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "extra-fields", `
ossie_plugin_spec: "0.1.0"
ossie_spec_version: ">=0.2.0"
future_field: "some value"
platform:
  name: test
  future_platform_field: "ignored"
convert:
  to_ossie:
    invoke: ["bin/convert"]
    accepts: [".yaml"]
  from_ossie:
    invoke: ["bin/convert"]
`)
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Fatalf("expected 1 plugin, got %d: future spec fields should be silently ignored", len(plugins))
	}
	if stderr.Len() != 0 {
		t.Errorf("unexpected warning: %q", stderr.String())
	}
}

func TestDiscover_strayFileIgnored(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "valid", validPluginYAML)
	// Place a plain file alongside the plugin directory.
	if err := os.WriteFile(filepath.Join(root, "stray.txt"), []byte("not a plugin"), 0644); err != nil {
		t.Fatal(err)
	}
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Errorf("expected 1 plugin, got %d", len(plugins))
	}
	if stderr.Len() != 0 {
		t.Errorf("expected no warnings for stray file, got: %q", stderr.String())
	}
}

func TestDiscover_setupFieldPopulated(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "with-setup", `
ossie_plugin_spec: "0.1.0"
ossie_spec_version: ">=0.2.0"
platform:
  name: dbt
setup: bin/setup
convert:
  to_ossie:
    invoke: ["bin/convert", "to-ossie"]
    accepts: [".yaml"]
  from_ossie:
    invoke: ["bin/convert", "from-ossie"]
`)
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Fatalf("expected 1 plugin, got %d", len(plugins))
	}
	if plugins[0].Setup != "bin/setup" {
		t.Errorf("Setup: got %q, want %q", plugins[0].Setup, "bin/setup")
	}
	if stderr.Len() != 0 {
		t.Errorf("unexpected warning: %q", stderr.String())
	}
}

func TestDiscover_setupFieldAbsent(t *testing.T) {
	root := t.TempDir()
	writePlugin(t, root, "no-setup", validPluginYAML)
	var stderr strings.Builder

	plugins, err := plugin.Discover(root, &stderr)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(plugins) != 1 {
		t.Fatalf("expected 1 plugin, got %d", len(plugins))
	}
	if plugins[0].Setup != "" {
		t.Errorf("Setup: got %q, want empty string", plugins[0].Setup)
	}
}
