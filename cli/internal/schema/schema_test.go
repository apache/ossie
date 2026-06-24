package schema_test

import (
	"testing"

	"github.com/open-semantic-interchange/ossie/cli/internal/schema"
)

// minimalValidYAML satisfies all schema-required fields:
// root: version (const) + semantic_model (array)
// SemanticModel: name + datasets (minItems 1)
// Dataset: name + source
const minimalValidYAML = `
version: "0.2.0.dev0"
semantic_model:
  - name: test_model
    datasets:
      - name: test_dataset
        source: db.schema.table
`

const minimalValidJSON = `{
  "version": "0.2.0.dev0",
  "semantic_model": [
    {
      "name": "test_model",
      "datasets": [
        {
          "name": "test_dataset",
          "source": "db.schema.table"
        }
      ]
    }
  ]
}`

func TestValidate_validYAML(t *testing.T) {
	if err := schema.Validate([]byte(minimalValidYAML)); err != nil {
		t.Errorf("expected valid YAML to pass, got: %v", err)
	}
}

func TestValidate_validJSON(t *testing.T) {
	if err := schema.Validate([]byte(minimalValidJSON)); err != nil {
		t.Errorf("expected valid JSON to pass, got: %v", err)
	}
}

func TestValidate_invalidDocument(t *testing.T) {
	// Missing required fields (version, semantic_model) and additionalProperties:false
	// means unknown top-level keys are also rejected.
	if err := schema.Validate([]byte(`name: foo`)); err == nil {
		t.Error("expected invalid document to fail, got nil")
	}
}
