package plugin_test

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/open-semantic-interchange/ossie/cli/internal/plugin"
)

// writeFile is a test helper that creates a file with the given content.
// Distinct from writePlugin in discover_test.go which creates a full plugin dir.
func writeFile(t *testing.T, dir, name, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0644); err != nil {
		t.Fatalf("could not write %s: %v", name, err)
	}
}

func TestCollectFiles_singleFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "model.yaml")
	if err := os.WriteFile(path, []byte("content"), 0644); err != nil {
		t.Fatal(err)
	}

	// Single file: extension filter (.json) does NOT apply.
	files, err := plugin.CollectFiles(path, []string{".json"}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(files) != 1 {
		t.Fatalf("expected 1 file, got %d", len(files))
	}
	if v, ok := files["model.yaml"]; !ok || v != "content" {
		t.Errorf("unexpected files map: %v", files)
	}
}

func TestCollectFiles_directory(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "a.yaml", "a")
	writeFile(t, dir, "b.yaml", "b")

	files, err := plugin.CollectFiles(dir, []string{".yaml"}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(files) != 2 {
		t.Errorf("expected 2 files, got %d: %v", len(files), files)
	}
}

func TestCollectFiles_directoryFiltered(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "model.yaml", "yaml-content")
	writeFile(t, dir, "readme.md", "md-content")

	files, err := plugin.CollectFiles(dir, []string{".yaml"}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(files) != 1 {
		t.Fatalf("expected 1 file after filter, got %d: %v", len(files), files)
	}
	if _, ok := files["model.yaml"]; !ok {
		t.Errorf("expected model.yaml in files, got: %v", files)
	}
}

func TestCollectFiles_forwardSlashKeys(t *testing.T) {
	dir := t.TempDir()
	sub := filepath.Join(dir, "subdir")
	if err := os.MkdirAll(sub, 0755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, sub, "model.yaml", "content")

	files, err := plugin.CollectFiles(dir, []string{".yaml"}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	for k := range files {
		for _, ch := range k {
			if ch == '\\' {
				t.Errorf("key contains backslash: %q", k)
			}
		}
		if k != "subdir/model.yaml" {
			t.Errorf("expected key %q, got %q", "subdir/model.yaml", k)
		}
	}
}

func TestCollectFiles_exceedsMaxSize(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, dir, "big.yaml", "12345") // 5 bytes

	_, err := plugin.CollectFiles(dir, []string{".yaml"}, 4) // limit 4 bytes
	if err == nil {
		t.Fatal("expected error for oversized input, got nil")
	}
}

func TestCollectFiles_emptyDirectory(t *testing.T) {
	dir := t.TempDir()

	files, err := plugin.CollectFiles(dir, []string{".yaml"}, 0)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(files) != 0 {
		t.Errorf("expected empty map, got: %v", files)
	}
}

func TestCollectFiles_nonExistentInput(t *testing.T) {
	_, err := plugin.CollectFiles(filepath.Join(t.TempDir(), "nonexistent.yaml"), nil, 0)
	if err == nil {
		t.Fatal("expected error for non-existent path, got nil")
	}
}
