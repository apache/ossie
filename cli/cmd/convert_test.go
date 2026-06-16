package cmd

import (
	"strings"
	"testing"

	"github.com/open-semantic-interchange/ossie/cli/internal/plugin"
)

// --- parseMaxInputSize tests ---

func TestParseMaxInputSize_MB(t *testing.T) {
	got, err := parseMaxInputSize("100MB")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := int64(100 * 1024 * 1024)
	if got != want {
		t.Errorf("got %d, want %d", got, want)
	}
}

func TestParseMaxInputSize_MB_lowercase(t *testing.T) {
	got, err := parseMaxInputSize("50mb")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := int64(50 * 1024 * 1024)
	if got != want {
		t.Errorf("got %d, want %d", got, want)
	}
}

func TestParseMaxInputSize_GB(t *testing.T) {
	got, err := parseMaxInputSize("2GB")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := int64(2 * 1024 * 1024 * 1024)
	if got != want {
		t.Errorf("got %d, want %d", got, want)
	}
}

func TestParseMaxInputSize_rawBytes(t *testing.T) {
	got, err := parseMaxInputSize("1048576")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != 1048576 {
		t.Errorf("got %d, want 1048576", got)
	}
}

func TestParseMaxInputSize_invalid(t *testing.T) {
	_, err := parseMaxInputSize("fooMB")
	if err == nil {
		t.Fatal("expected error for invalid input, got nil")
	}
}

func TestParseMaxInputSize_invalidNoSuffix(t *testing.T) {
	_, err := parseMaxInputSize("not-a-number")
	if err == nil {
		t.Fatal("expected error for non-numeric input without suffix, got nil")
	}
}

// --- renderIssues tests ---

func TestRenderIssues_errorSetsReturnTrue(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "error", Message: "something failed"}}
	if !renderIssues(issues, &buf, false) {
		t.Error("expected hasErrors=true for error-severity issue")
	}
	if !strings.Contains(buf.String(), "error: something failed") {
		t.Errorf("unexpected output: %q", buf.String())
	}
}

func TestRenderIssues_warningSetsReturnFalse(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "warning", Message: "a warning"}}
	if renderIssues(issues, &buf, false) {
		t.Error("expected hasErrors=false for warning-only issue")
	}
}

func TestRenderIssues_infoSuppressedWhenNotVerbose(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "info", Message: "some info"}}
	renderIssues(issues, &buf, false)
	if buf.Len() != 0 {
		t.Errorf("expected no output for info issue when not verbose, got: %q", buf.String())
	}
}

func TestRenderIssues_infoPrintedWhenVerbose(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "info", Message: "some info"}}
	renderIssues(issues, &buf, true)
	if !strings.Contains(buf.String(), "info: some info") {
		t.Errorf("expected info output when verbose, got: %q", buf.String())
	}
}

func TestRenderIssues_pathIncludedInOutput(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "warning", Path: "models/orders.yaml", Message: "deprecated field"}}
	renderIssues(issues, &buf, false)
	if !strings.Contains(buf.String(), "warning: models/orders.yaml: deprecated field") {
		t.Errorf("expected path in output, got: %q", buf.String())
	}
}

func TestRenderIssues_emptyPathNoDoubleColon(t *testing.T) {
	var buf strings.Builder
	issues := []plugin.Issue{{Severity: "error", Message: "fatal"}}
	renderIssues(issues, &buf, false)
	if strings.Contains(buf.String(), ": : ") {
		t.Errorf("empty path must not produce double colon, got: %q", buf.String())
	}
}
