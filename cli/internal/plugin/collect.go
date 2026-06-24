package plugin

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// CollectFiles builds the files map for a plugin Request.
//
// If input is a regular file, it is read unconditionally (no extension filter)
// and keyed by its bare filename. The caller explicitly chose the file, so the
// plugin's Accepts list is not applied.
//
// If input is a directory, it is walked recursively. Only files whose extension
// (case-insensitive) appears in accepts are included. If accepts is empty, all
// files are included. Keys are forward-slash relative paths from the directory root.
//
// maxBytes enforces a cumulative size limit. Pass maxBytes <= 0 to disable it.
func CollectFiles(input string, accepts []string, maxBytes int64) (map[string]string, error) {
	info, err := os.Stat(input)
	if err != nil {
		return nil, fmt.Errorf("cannot access input path %q: %w", input, err)
	}

	if !info.IsDir() {
		data, err := os.ReadFile(input)
		if err != nil {
			return nil, fmt.Errorf("cannot read input file %q: %w", input, err)
		}
		if maxBytes > 0 && int64(len(data)) > maxBytes {
			return nil, fmt.Errorf("total input size exceeds limit (%dMB). Use --max-input-size to override",
				maxBytes/(1024*1024))
		}
		return map[string]string{filepath.Base(input): string(data)}, nil
	}

	// Build a lowercase set for O(1) extension lookup.
	acceptSet := make(map[string]struct{}, len(accepts))
	for _, ext := range accepts {
		acceptSet[strings.ToLower(ext)] = struct{}{}
	}

	return collectDir(input, acceptSet, maxBytes)
}

func collectDir(root string, acceptSet map[string]struct{}, maxBytes int64) (map[string]string, error) {
	files := make(map[string]string)
	var total int64

	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, werr error) error {
		if werr != nil {
			return werr
		}
		if d.IsDir() {
			return nil
		}
		if len(acceptSet) > 0 {
			ext := strings.ToLower(filepath.Ext(path))
			if _, ok := acceptSet[ext]; !ok {
				return nil
			}
		}

		data, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("cannot read %q: %w", path, err)
		}

		if maxBytes > 0 {
			total += int64(len(data))
			if total > maxBytes {
				return fmt.Errorf("total input size exceeds limit (%dMB). Use --max-input-size to override",
					maxBytes/(1024*1024))
			}
		}

		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		files[filepath.ToSlash(rel)] = string(data)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return files, nil
}
