package schema

import (
	"bytes"
	_ "embed" // blank import required to activate //go:embed
	"encoding/json"
	"fmt"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

//go:embed osi-schema.json
var schemaJSON []byte

// schemaID must exactly match the $id in osi-schema.json.
// The compiler uses this string as both the resource URL and the compile target.
const schemaID = "https://github.com/open-semantic-interchange/OSI/core-spec/osi-schema.json"

var compiledSchema *jsonschema.Schema

func init() {
	schemaSrc, err := jsonschema.UnmarshalJSON(bytes.NewReader(schemaJSON))
	if err != nil {
		panic(fmt.Sprintf("schema: failed to parse embedded OSI schema: %v", err))
	}
	c := jsonschema.NewCompiler()
	if err := c.AddResource(schemaID, schemaSrc); err != nil {
		panic(fmt.Sprintf("schema: failed to register embedded OSI schema: %v", err))
	}
	compiledSchema = c.MustCompile(schemaID)
}

// Validate validates data (YAML or JSON bytes) against the embedded OSI schema.
// YAML is converted to JSON via a round-trip through yaml.v3 and encoding/json
// to normalise Go types to the representation jsonschema/v6 expects.
// Returns nil if valid, an error describing the violation otherwise.
func Validate(data []byte) error {
	var doc any
	if err := yaml.Unmarshal(data, &doc); err != nil {
		return fmt.Errorf("failed to parse document: %w", err)
	}
	// Round-trip through JSON to normalise yaml.v3's native int/float64
	// to json.Number, which jsonschema/v6 expects.
	jsonBytes, err := json.Marshal(doc)
	if err != nil {
		return fmt.Errorf("failed to serialise document to JSON: %w", err)
	}
	inst, err := jsonschema.UnmarshalJSON(bytes.NewReader(jsonBytes))
	if err != nil {
		return fmt.Errorf("failed to prepare document for validation: %w", err)
	}
	return compiledSchema.Validate(inst)
}
