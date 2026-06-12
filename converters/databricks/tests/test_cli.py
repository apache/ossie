"""Tests for the osi-databricks CLI.

Validates:
- Import subcommand produces valid OSI YAML output
- Export subcommand produces one file per dataset named {dataset}.yaml
- Error handling: invalid input exits non-zero with stderr message
- Missing output directory is created automatically
- Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from osi.models import OSIDocument

from osi_databricks.models import MetricViewModel

# --- Fixtures ---


@pytest.fixture
def sample_metric_view_yaml(tmp_path: Path) -> Path:
    """Create a sample Metric View YAML file for testing import."""
    content = """\
version: '1.1'
source: catalog.schema.store_sales
comment: Store sales metric view
filter: ss_quantity > 0
joins:
  - name: date_dim
    source: catalog.schema.date_dim
    on: source.ss_sold_date_sk = date_dim.d_date_sk
    cardinality: many_to_one
fields:
  - name: sold_date
    expr: date_dim.d_date
    comment: Sale date
    display_name: Date of Sale
    synonyms:
      - sale date
      - transaction date
measures:
  - name: total_sales
    expr: SUM(ss_net_paid)
    comment: Total net sales
"""
    p = tmp_path / "metric_view.yaml"
    p.write_text(content)
    return p


@pytest.fixture
def sample_osi_yaml(tmp_path: Path) -> Path:
    """Create a sample OSI YAML file for testing export."""
    content = """\
version: 0.2.0.dev0
dialects:
  - DATABRICKS
  - ANSI_SQL
vendors:
  - DATABRICKS
semantic_model:
  - name: my_model
    description: Test model
    datasets:
      - name: store_sales
        source: catalog.schema.store_sales
        fields:
          - name: sold_date
            expression:
              dialects:
                - dialect: DATABRICKS
                  expression: d_date
                - dialect: ANSI_SQL
                  expression: d_date
            dimension:
              is_time: true
            description: Sale date
          - name: quantity
            expression:
              dialects:
                - dialect: DATABRICKS
                  expression: ss_quantity
                - dialect: ANSI_SQL
                  expression: ss_quantity
            dimension:
              is_time: false
    metrics:
      - name: total_sales
        expression:
          dialects:
            - dialect: DATABRICKS
              expression: SUM(ss_net_paid)
            - dialect: ANSI_SQL
              expression: SUM(ss_net_paid)
        description: Total net sales
"""
    p = tmp_path / "osi_model.yaml"
    p.write_text(content)
    return p


@pytest.fixture
def multi_dataset_osi_yaml(tmp_path: Path) -> Path:
    """Create an OSI YAML file with multiple datasets for testing multi-file export."""
    content = """\
version: 0.2.0.dev0
dialects:
  - DATABRICKS
vendors:
  - DATABRICKS
semantic_model:
  - name: multi_model
    datasets:
      - name: orders
        source: catalog.schema.orders
        fields:
          - name: order_id
            expression:
              dialects:
                - dialect: DATABRICKS
                  expression: order_id
            dimension:
              is_time: false
      - name: customers
        source: catalog.schema.customers
        fields:
          - name: customer_id
            expression:
              dialects:
                - dialect: DATABRICKS
                  expression: customer_id
            dimension:
              is_time: false
    metrics:
      - name: order_count
        expression:
          dialects:
            - dialect: DATABRICKS
              expression: COUNT(order_id)
"""
    p = tmp_path / "multi_osi.yaml"
    p.write_text(content)
    return p


# --- Helper ---


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the osi-databricks CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "osi_databricks.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


# --- Import Subcommand Tests ---


class TestImportSubcommand:
    """Tests for the 'import' subcommand (Metric View → OSI)."""

    def test_import_produces_valid_osi_yaml(self, sample_metric_view_yaml: Path, tmp_path: Path):
        """Import subcommand produces valid OSI YAML output."""
        output_path = tmp_path / "output_osi.yaml"
        result = run_cli("import", "-i", str(sample_metric_view_yaml), "-o", str(output_path))

        assert result.returncode == 0
        assert output_path.exists()

        # Validate the output is parseable OSI
        raw = yaml.safe_load(output_path.read_text())
        doc = OSIDocument.model_validate(raw)
        assert doc.version == "0.2.0.dev0"
        assert len(doc.semantic_model) == 1
        sm = doc.semantic_model[0]
        assert sm.name == "metric_view_model"
        assert len(sm.datasets) == 1
        assert sm.datasets[0].name == "store_sales"

    def test_import_with_custom_model_name(self, sample_metric_view_yaml: Path, tmp_path: Path):
        """Import subcommand respects --model-name argument."""
        output_path = tmp_path / "output.yaml"
        result = run_cli(
            "import", "-i", str(sample_metric_view_yaml),
            "-o", str(output_path),
            "--model-name", "custom_name",
        )

        assert result.returncode == 0
        raw = yaml.safe_load(output_path.read_text())
        doc = OSIDocument.model_validate(raw)
        assert doc.semantic_model[0].name == "custom_name"

    def test_import_preserves_fields_and_measures(self, sample_metric_view_yaml: Path, tmp_path: Path):
        """Import preserves field and measure data in OSI output."""
        output_path = tmp_path / "output.yaml"
        result = run_cli("import", "-i", str(sample_metric_view_yaml), "-o", str(output_path))

        assert result.returncode == 0
        raw = yaml.safe_load(output_path.read_text())
        doc = OSIDocument.model_validate(raw)
        sm = doc.semantic_model[0]

        # Check fields
        fields = sm.datasets[0].fields
        assert any(f.name == "sold_date" for f in fields)

        # Check metrics
        assert sm.metrics is not None
        assert any(m.name == "total_sales" for m in sm.metrics)

    def test_import_creates_output_parent_directory(self, sample_metric_view_yaml: Path, tmp_path: Path):
        """Import creates missing parent directories for output file."""
        output_path = tmp_path / "nested" / "deep" / "output.yaml"
        result = run_cli("import", "-i", str(sample_metric_view_yaml), "-o", str(output_path))

        assert result.returncode == 0
        assert output_path.exists()

    def test_import_writes_status_to_stderr(self, sample_metric_view_yaml: Path, tmp_path: Path):
        """Import writes a status message to stderr."""
        output_path = tmp_path / "output.yaml"
        result = run_cli("import", "-i", str(sample_metric_view_yaml), "-o", str(output_path))

        assert result.returncode == 0
        assert "Written to" in result.stderr


# --- Export Subcommand Tests ---


class TestExportSubcommand:
    """Tests for the 'export' subcommand (OSI → Metric View)."""

    def test_export_produces_valid_metric_view_yaml(self, sample_osi_yaml: Path, tmp_path: Path):
        """Export subcommand produces valid Metric View YAML output."""
        output_dir = tmp_path / "export_output"
        result = run_cli("export", "-i", str(sample_osi_yaml), "-o", str(output_dir))

        assert result.returncode == 0
        assert output_dir.exists()

        # Should produce a file named after the dataset
        output_file = output_dir / "store_sales.yaml"
        assert output_file.exists()

        # Validate the output is parseable Metric View
        mv_model = MetricViewModel.from_yaml(output_file.read_text())
        assert mv_model.source == "catalog.schema.store_sales"
        assert mv_model.fields is not None
        assert any(f.name == "sold_date" for f in mv_model.fields)

    def test_export_one_file_per_dataset(self, multi_dataset_osi_yaml: Path, tmp_path: Path):
        """Export produces one file per dataset named {dataset_name}.yaml."""
        output_dir = tmp_path / "multi_export"
        result = run_cli("export", "-i", str(multi_dataset_osi_yaml), "-o", str(output_dir))

        assert result.returncode == 0

        # Should have files for both datasets
        orders_file = output_dir / "orders.yaml"
        customers_file = output_dir / "customers.yaml"
        assert orders_file.exists()
        assert customers_file.exists()

        # Validate each
        orders_model = MetricViewModel.from_yaml(orders_file.read_text())
        assert orders_model.source == "catalog.schema.orders"

        customers_model = MetricViewModel.from_yaml(customers_file.read_text())
        assert customers_model.source == "catalog.schema.customers"

    def test_export_creates_output_directory(self, sample_osi_yaml: Path, tmp_path: Path):
        """Export creates missing output directory automatically."""
        output_dir = tmp_path / "new" / "nested" / "dir"
        assert not output_dir.exists()

        result = run_cli("export", "-i", str(sample_osi_yaml), "-o", str(output_dir))

        assert result.returncode == 0
        assert output_dir.exists()
        # Files should have been written
        assert any(output_dir.iterdir())

    def test_export_writes_status_to_stderr(self, sample_osi_yaml: Path, tmp_path: Path):
        """Export writes status messages to stderr for each file written."""
        output_dir = tmp_path / "export_out"
        result = run_cli("export", "-i", str(sample_osi_yaml), "-o", str(output_dir))

        assert result.returncode == 0
        assert "Written" in result.stderr


# --- Error Handling Tests ---


class TestErrorHandling:
    """Tests for CLI error handling."""

    def test_import_invalid_yaml_exits_nonzero(self, tmp_path: Path):
        """Import exits non-zero with stderr message on invalid YAML input."""
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("this: is: not: valid: {{yaml")
        output_path = tmp_path / "output.yaml"

        result = run_cli("import", "-i", str(bad_file), "-o", str(output_path))

        assert result.returncode != 0
        assert "Error parsing" in result.stderr

    def test_import_missing_required_field_exits_nonzero(self, tmp_path: Path):
        """Import exits non-zero when required fields are missing."""
        bad_file = tmp_path / "incomplete.yaml"
        bad_file.write_text("version: '1.1'\nfields:\n  - name: f1\n    expr: col1\n")
        output_path = tmp_path / "output.yaml"

        result = run_cli("import", "-i", str(bad_file), "-o", str(output_path))

        assert result.returncode != 0
        assert "Error parsing" in result.stderr

    def test_import_nonexistent_file_exits_nonzero(self, tmp_path: Path):
        """Import exits non-zero when input file does not exist."""
        result = run_cli("import", "-i", str(tmp_path / "does_not_exist.yaml"), "-o", str(tmp_path / "out.yaml"))

        assert result.returncode != 0
        assert "Error parsing" in result.stderr

    def test_export_invalid_osi_yaml_exits_nonzero(self, tmp_path: Path):
        """Export exits non-zero with stderr message on invalid OSI input."""
        bad_file = tmp_path / "bad_osi.yaml"
        bad_file.write_text("not_a_valid: osi_document")
        output_dir = tmp_path / "out"

        result = run_cli("export", "-i", str(bad_file), "-o", str(output_dir))

        assert result.returncode != 0
        assert "Error parsing" in result.stderr

    def test_export_nonexistent_file_exits_nonzero(self, tmp_path: Path):
        """Export exits non-zero when input file does not exist."""
        result = run_cli("export", "-i", str(tmp_path / "missing.yaml"), "-o", str(tmp_path / "out"))

        assert result.returncode != 0
        assert "Error parsing" in result.stderr

    def test_no_command_shows_help(self):
        """Running without a subcommand exits non-zero."""
        result = run_cli()
        assert result.returncode != 0

    def test_unknown_command_exits_nonzero(self):
        """Running with an unknown subcommand exits non-zero."""
        result = run_cli("unknown")
        assert result.returncode != 0
