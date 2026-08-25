from ossie_hex.hex import HexProject
from ossie_hex.ossie_to_hex.dump_hex_resource import _reorder_fields
from ossie_hex.util.problem import Problem
from ossie_hex.util.yaml import dump_yaml


def problems_snapshot(
    problems: list[Problem],
    *,
    include_causes: bool = False,
) -> str:
    return "\n\n".join(p.to_str(include_cause=include_causes) for p in problems)


def hex_project_snapshot(hex_project: HexProject) -> str:
    json_data = hex_project.model_dump(
        mode="json",
        exclude_none=True,
        exclude_unset=True,
        exclude_defaults=True,
    )
    json_data = _reorder_fields(json_data)
    yaml_str = dump_yaml(json_data)
    return yaml_str
