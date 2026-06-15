package registry_test

import (
	"testing"

	"github.com/open-semantic-interchange/ossie/cli/internal/registry"
)

// knownPlatforms is the authoritative list of platforms that must be present
// in the embedded registry for the CLI to have useful output.
var knownPlatforms = []string{"dbt", "gooddata", "polaris", "salesforce", "snowflake"}

func TestLoad_succeeds(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	if reg == nil {
		t.Fatal("Load() returned nil registry")
	}
}

func TestLoad_containsAllPlatforms(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	for _, platform := range knownPlatforms {
		entries, ok := reg[platform]
		if !ok {
			t.Errorf("platform %q not found in registry", platform)
			continue
		}
		if len(entries) == 0 {
			t.Errorf("platform %q has no entries", platform)
		}
	}
}

func TestRegistry_Platforms_sorted(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	got := reg.Platforms()
	for i := 1; i < len(got); i++ {
		if got[i] < got[i-1] {
			t.Errorf("Platforms() not sorted: %q appears after %q", got[i-1], got[i])
		}
	}
}

func TestRegistry_LatestEntry_found(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	entry, ok := reg.LatestEntry("dbt")
	if !ok {
		t.Fatal("LatestEntry(\"dbt\") returned false, want true")
	}
	if entry.Version == "" {
		t.Error("LatestEntry(\"dbt\") returned entry with empty Version")
	}
	if entry.Type == "" {
		t.Error("LatestEntry(\"dbt\") returned entry with empty Type")
	}
	if entry.URL == "" {
		t.Error("LatestEntry(\"dbt\") returned entry with empty URL")
	}
	if entry.Checksum == "" {
		t.Error("LatestEntry(\"dbt\") returned entry with empty Checksum")
	}
}

func TestRegistry_LatestEntry_notFound(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	_, ok := reg.LatestEntry("does-not-exist")
	if ok {
		t.Error("LatestEntry(\"does-not-exist\") returned true, want false")
	}
}

func TestRegistry_FindEntry_found(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	entry, ok := reg.FindEntry("dbt", "0.1.0")
	if !ok {
		t.Fatal("FindEntry(\"dbt\", \"0.1.0\") returned false, want true")
	}
	if entry.Version != "0.1.0" {
		t.Errorf("FindEntry version: got %q, want %q", entry.Version, "0.1.0")
	}
}

func TestRegistry_FindEntry_notFound(t *testing.T) {
	reg, err := registry.Load()
	if err != nil {
		t.Fatalf("Load() returned error: %v", err)
	}
	_, ok := reg.FindEntry("dbt", "99.99.99")
	if ok {
		t.Error("FindEntry(\"dbt\", \"99.99.99\") returned true, want false")
	}
}
