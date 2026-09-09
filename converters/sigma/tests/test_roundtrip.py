"""End-to-end round trips through the same serialization boundary the CLI uses
(OssieDocument -> YAML text -> re-parsed OssieDocument), for every fixture."""

import pytest
import yaml
from ossie import OssieDocument

from ossie_sigma.ossie_to_sigma import OssieToSigmaConverter
from ossie_sigma.sigma_to_ossie import SigmaToOssieConverter

from .helpers import load_fixture, normalize


FIXTURES = ["fixtureA_sigma.json", "fixtureB_sigma.json", "fixtureC_sigma.json"]


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_sigma_osi_sigma_roundtrip_through_yaml_serialization(fixture_name):
    spec = load_fixture(fixture_name)

    document = SigmaToOssieConverter().convert(spec).output
    yaml_text = document.to_ossie_yaml()

    reparsed_document = OssieDocument.model_validate(yaml.safe_load(yaml_text))
    reconstructed_spec = OssieToSigmaConverter().convert(reparsed_document).output

    assert normalize(reconstructed_spec) == normalize(spec)


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_osi_sigma_osi_roundtrip_preserves_portable_fields(fixture_name):
    """Sigma -> Ossie -> Sigma -> Ossie: the second Ossie document's portable
    (non-custom_extensions) content must match the first, even though the Sigma
    spec in between round-trips through JSON."""
    spec = load_fixture(fixture_name)

    document_1 = SigmaToOssieConverter().convert(spec).output
    spec_2 = OssieToSigmaConverter().convert(document_1).output
    document_2 = SigmaToOssieConverter().convert(spec_2).output

    def portable(document):
        model = document.semantic_model[0]
        return {
            "datasets": [(d.name, d.source, [(f.name, f.datatype) for f in d.fields or []]) for d in model.datasets],
            "relationships": [(r.name, r.from_dataset, r.to, r.from_columns, r.to_columns) for r in model.relationships or []],
            "metrics": [(m.name,) for m in model.metrics or []],
        }

    assert portable(document_1) == portable(document_2)
