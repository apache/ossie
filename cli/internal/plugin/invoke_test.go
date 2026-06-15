package plugin_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/open-semantic-interchange/ossie/cli/internal/plugin"
)

// TestMain handles two roles:
//  1. When GO_TEST_PLUGIN=1 it acts as a fake plugin subprocess, reads stdin,
//     and writes a canned response based on GO_TEST_PLUGIN_MODE.
//  2. Otherwise it runs the normal test suite.
func TestMain(m *testing.M) {
	if os.Getenv("GO_TEST_PLUGIN") == "1" {
		runFakePlugin()
		// runFakePlugin calls os.Exit; this line is never reached.
	}
	os.Exit(m.Run())
}

// runFakePlugin implements a minimal plugin subprocess used by invoke tests.
// It reads all of stdin (the JSON request), then dispatches on GO_TEST_PLUGIN_MODE.
func runFakePlugin() {
	stdinBytes, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "fake plugin: failed to read stdin:", err)
		os.Exit(2)
	}

	switch mode := os.Getenv("GO_TEST_PLUGIN_MODE"); mode {
	case "success":
		json.NewEncoder(os.Stdout).Encode(map[string]any{
			"files": map[string]string{"output.yaml": "converted content"},
		})

	case "warning_issue":
		json.NewEncoder(os.Stdout).Encode(map[string]any{
			"files": map[string]string{"output.yaml": "converted content"},
			"issues": []map[string]string{
				{"severity": "warning", "message": "some warning", "path": "input.yaml"},
			},
		})

	case "error_issue":
		json.NewEncoder(os.Stdout).Encode(map[string]any{
			"files": map[string]string{},
			"issues": []map[string]string{
				{"severity": "error", "message": "conversion failed"},
			},
		})

	case "invalid_json":
		fmt.Fprint(os.Stdout, "not json")

	case "stderr_output":
		fmt.Fprint(os.Stderr, "stderr from plugin")
		json.NewEncoder(os.Stdout).Encode(map[string]any{
			"files": map[string]string{"output.yaml": "ok"},
		})

	case "nonzero_exit":
		os.Exit(1)

	case "timeout":
		time.Sleep(30 * time.Second)

	case "echo_request":
		// Echo the raw stdin bytes back in files["received_request"] so the
		// test can assert the plugin received the correct request JSON.
		json.NewEncoder(os.Stdout).Encode(map[string]any{
			"files": map[string]string{"received_request": string(stdinBytes)},
		})

	default:
		fmt.Fprintln(os.Stderr, "fake plugin: unknown mode:", mode)
		os.Exit(2)
	}

	os.Exit(0)
}

// fakePluginInvoke sets up env vars for the fake plugin and returns the
// invoke slice. The test binary re-invokes itself with -test.run=^$ so that
// no tests run in the child — TestMain sees GO_TEST_PLUGIN=1 and exits early.
func fakePluginInvoke(t *testing.T, mode string) []string {
	t.Helper()
	t.Setenv("GO_TEST_PLUGIN", "1")
	t.Setenv("GO_TEST_PLUGIN_MODE", mode)
	return []string{os.Args[0], "-test.run=^$"}
}

func TestInvoke_success(t *testing.T) {
	invoke := fakePluginInvoke(t, "success")
	ctx := context.Background()

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{Files: map[string]string{}}, io.Discard)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp == nil {
		t.Fatal("expected non-nil response")
	}
	if _, ok := resp.Files["output.yaml"]; !ok {
		t.Errorf("expected output.yaml in response files, got: %v", resp.Files)
	}
	if len(resp.Issues) != 0 {
		t.Errorf("expected no issues, got %d", len(resp.Issues))
	}
}

func TestInvoke_warningIssue(t *testing.T) {
	invoke := fakePluginInvoke(t, "warning_issue")
	ctx := context.Background()

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(resp.Files) != 1 {
		t.Errorf("expected 1 output file, got %d", len(resp.Files))
	}
	if len(resp.Issues) != 1 {
		t.Fatalf("expected 1 issue, got %d", len(resp.Issues))
	}
	if resp.Issues[0].Severity != "warning" {
		t.Errorf("issue severity: got %q, want %q", resp.Issues[0].Severity, "warning")
	}
}

func TestInvoke_errorIssue(t *testing.T) {
	invoke := fakePluginInvoke(t, "error_issue")
	ctx := context.Background()

	// An error-severity issue is NOT a Go error — Invoke must return nil err.
	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err != nil {
		t.Fatalf("unexpected Go error: %v", err)
	}
	if len(resp.Issues) != 1 {
		t.Fatalf("expected 1 issue, got %d", len(resp.Issues))
	}
	if resp.Issues[0].Severity != "error" {
		t.Errorf("issue severity: got %q, want %q", resp.Issues[0].Severity, "error")
	}
}

func TestInvoke_invalidResponseJSON(t *testing.T) {
	invoke := fakePluginInvoke(t, "invalid_json")
	ctx := context.Background()

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err == nil {
		t.Fatal("expected error for invalid JSON response, got nil")
	}
	if resp != nil {
		t.Errorf("expected nil response, got %+v", resp)
	}
}

func TestInvoke_nonZeroExit(t *testing.T) {
	invoke := fakePluginInvoke(t, "nonzero_exit")
	ctx := context.Background()

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err == nil {
		t.Fatal("expected error for non-zero exit, got nil")
	}
	if resp != nil {
		t.Errorf("expected nil response, got %+v", resp)
	}
}

func TestInvoke_stderrForwarded(t *testing.T) {
	invoke := fakePluginInvoke(t, "stderr_output")
	ctx := context.Background()
	var buf strings.Builder

	_, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, &buf)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(buf.String(), "stderr from plugin") {
		t.Errorf("expected stderr content in writer, got: %q", buf.String())
	}
}

func TestInvoke_stderrSuppressed(t *testing.T) {
	invoke := fakePluginInvoke(t, "stderr_output")
	ctx := context.Background()

	// Passing io.Discard must not panic and must return a valid response.
	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp == nil {
		t.Fatal("expected non-nil response")
	}
}

func TestInvoke_timeout(t *testing.T) {
	invoke := fakePluginInvoke(t, "timeout")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, plugin.Request{}, io.Discard)

	if err == nil {
		t.Fatal("expected timeout error, got nil")
	}
	if resp != nil {
		t.Errorf("expected nil response on timeout, got %+v", resp)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("expected DeadlineExceeded in error chain, got: %v", err)
	}
}

func TestInvoke_requestPayloadReachesPlugin(t *testing.T) {
	invoke := fakePluginInvoke(t, "echo_request")
	ctx := context.Background()

	req := plugin.Request{
		Files: map[string]string{
			"core/orders.yaml": "version: 2\nmodels:\n  - name: orders",
		},
	}

	resp, err := plugin.Invoke(ctx, t.TempDir(), invoke, req, io.Discard)

	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	raw, ok := resp.Files["received_request"]
	if !ok {
		t.Fatal("expected received_request key in response files")
	}

	// Decode the echoed request and compare — avoids brittle JSON key-ordering checks.
	var echoed plugin.Request
	if err := json.Unmarshal([]byte(raw), &echoed); err != nil {
		t.Fatalf("could not decode echoed request: %v", err)
	}
	want := req.Files["core/orders.yaml"]
	if echoed.Files["core/orders.yaml"] != want {
		t.Errorf("echoed file content:\ngot:  %q\nwant: %q", echoed.Files["core/orders.yaml"], want)
	}
}
