package plugin

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os/exec"
)

// Request is the JSON envelope sent to a plugin on stdin.
type Request struct {
	Files map[string]string `json:"files"`
}

// Response is the JSON envelope received from a plugin on stdout.
type Response struct {
	Files  map[string]string `json:"files"`
	Issues []Issue           `json:"issues,omitempty"`
}

// Issue is a single diagnostic emitted by a plugin during conversion.
// Severity is one of "error", "warning", or "info".
// Path is optional; it is omitted for issues not tied to a specific location.
type Issue struct {
	Severity string `json:"severity"`
	Message  string `json:"message"`
	Path     string `json:"path,omitempty"`
}

// Invoke runs a plugin subprocess, pipes req as JSON to its stdin,
// and decodes its stdout as a JSON Response.
//
// pluginDir is used as the working directory for the subprocess so that
// relative paths in the invoke array resolve correctly.
// invoke is the command and its arguments (invoke[0] is the executable).
// pluginStderr receives the plugin's stderr verbatim; pass io.Discard to suppress.
//
// Hard errors: failed to marshal request, process start failure, context
// deadline exceeded, non-zero exit code, or invalid JSON in the response.
// A non-empty Issues slice in the response is NOT itself a Go error — the
// caller is responsible for inspecting severities and setting the exit code.
func Invoke(ctx context.Context, pluginDir string, invoke []string, req Request, pluginStderr io.Writer) (*Response, error) {
	// Normalise nil to empty map so plugins always receive {"files":{}} not {"files":null}.
	if req.Files == nil {
		req.Files = map[string]string{}
	}

	reqJSON, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal plugin request: %w", err)
	}

	var stdout bytes.Buffer
	cmd := exec.CommandContext(ctx, invoke[0], invoke[1:]...)
	cmd.Dir = pluginDir
	cmd.Stdin = bytes.NewReader(reqJSON)
	// Use an explicit buffer rather than cmd.Output() — cmd.Output() is
	// incompatible with a pre-assigned cmd.Stderr writer.
	cmd.Stdout = &stdout
	cmd.Stderr = pluginStderr

	if err := cmd.Run(); err != nil {
		// ctx.Err() is the authoritative signal for timeout/cancellation.
		// exec.CommandContext kills the process and returns "signal: killed",
		// not context.DeadlineExceeded, so we must check ctx.Err() explicitly.
		if ctx.Err() != nil {
			return nil, fmt.Errorf("plugin timed out: %w", ctx.Err())
		}
		return nil, fmt.Errorf("plugin process failed: %w", err)
	}

	var resp Response
	if err := json.Unmarshal(stdout.Bytes(), &resp); err != nil {
		return nil, fmt.Errorf("invalid JSON response from plugin: %w", err)
	}
	return &resp, nil
}
