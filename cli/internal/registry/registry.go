package registry

import (
	_ "embed" // blank import required to activate //go:embed
	"fmt"
	"sort"

	"gopkg.in/yaml.v3"
)

// Entry types supported by the registry.
// These constants are consumed by P2 (plugin install) when dispatching
// on how to download and extract a plugin archive.
const (
	EntryTypeGitRelease = "git_release"
	EntryTypeRawURL     = "raw_url"
)

// Entry is a single versioned release of a plugin for one platform.
type Entry struct {
	Version  string `yaml:"version"`
	Type     string `yaml:"type"`          // EntryTypeGitRelease or EntryTypeRawURL
	URL      string `yaml:"url"`
	Tag      string `yaml:"tag,omitempty"` // git_release only
	Checksum string `yaml:"checksum"`
}

// Registry maps a platform name to its list of versioned entries, ordered
// oldest to newest. The last entry in each slice is the latest version.
type Registry map[string][]Entry

//go:embed plugins-registry.yaml
var registryData []byte

// Load parses the embedded plugins-registry.yaml and returns a Registry.
// An error here indicates a build-time defect; registry_test.go validates
// the embedded data on every test run.
func Load() (Registry, error) {
	var r Registry
	if err := yaml.Unmarshal(registryData, &r); err != nil {
		return nil, fmt.Errorf("failed to parse embedded plugin registry: %w", err)
	}
	return r, nil
}

// Platforms returns all platform names in the registry in alphabetical order.
func (r Registry) Platforms() []string {
	names := make([]string, 0, len(r))
	for name := range r {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// LatestEntry returns the last (newest) entry for the given platform.
// It returns false if the platform is not in the registry or has no entries.
// Registry maintainers are responsible for keeping entries ordered oldest → newest.
func (r Registry) LatestEntry(platform string) (Entry, bool) {
	entries, ok := r[platform]
	if !ok || len(entries) == 0 {
		return Entry{}, false
	}
	return entries[len(entries)-1], true
}

// FindEntry returns the entry matching the given platform and version.
// It returns false if the platform or version is not found.
func (r Registry) FindEntry(platform, version string) (Entry, bool) {
	for _, entry := range r[platform] {
		if entry.Version == version {
			return entry, true
		}
	}
	return Entry{}, false
}
