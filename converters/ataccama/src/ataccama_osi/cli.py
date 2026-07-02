"""CLI: import selected Ataccama catalog items into an OSI semantic model.

Scope is defined by explicit catalog-item URNs (``--urn`` repeated, or ``--urns-file``).

Configuration is read from environment variables (optionally loaded from an env file
via ``--env-file``):

  ATACCAMA_BASE_URL       API root, e.g. https://<host>/api
  ATACCAMA_TOKEN_URL      OAuth2 token endpoint (Keycloak openid-connect/token)
  ATACCAMA_CLIENT_ID      service-account client id
  ATACCAMA_CLIENT_SECRET  service-account client secret

Example:
  ataccama-to-osi --env-file .ataccama.env \\
      --urn urn:ata:tenant:catalog:catalog-item:... \\
      --model-name my_model --output my_model.osi.yaml
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from ataccama_osi.ataccama_to_osi import ataccama_to_osi
from ataccama_osi.client import AtaccamaClient

REQUIRED_VARS = ("ATACCAMA_BASE_URL", "ATACCAMA_TOKEN_URL", "ATACCAMA_CLIENT_ID", "ATACCAMA_CLIENT_SECRET")


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines into os.environ (does not overwrite existing vars)."""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _tenant_from_urn(urn: str) -> str | None:
    # urn:ata:<tenant>:<domain>:<type>:<id>
    parts = urn.split(":")
    return parts[2] if len(parts) > 2 else None


def _collect_urns(args: argparse.Namespace) -> list[str]:
    urns: list[str] = list(args.urn or [])
    if args.urns_file:
        for raw in Path(args.urns_file).read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                urns.append(line)
    # de-duplicate, preserve order
    seen: set[str] = set()
    return [u for u in urns if not (u in seen or seen.add(u))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import Ataccama ONE catalog items into an OSI semantic model.")
    parser.add_argument("--urn", action="append", help="Catalog-item URN to convert (repeatable).")
    parser.add_argument("--urns-file", help="File with one catalog-item URN per line.")
    parser.add_argument("--model-name", default="ataccama_model", help="OSI semantic model name.")
    parser.add_argument("--model-description", default=None, help="OSI semantic model description.")
    parser.add_argument("--output", "-o", default="-", help="Output YAML path ('-' for stdout).")
    parser.add_argument("--env-file", help="Optional KEY=VALUE file with Ataccama connection config.")
    args = parser.parse_args(argv)

    if args.env_file:
        _load_env_file(Path(args.env_file))

    urns = _collect_urns(args)
    if not urns:
        parser.error("provide at least one catalog-item URN via --urn or --urns-file")

    missing = [v for v in REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        parser.error(f"missing required configuration: {', '.join(missing)}")

    client = AtaccamaClient(
        base_url=os.environ["ATACCAMA_BASE_URL"],
        token_url=os.environ["ATACCAMA_TOKEN_URL"],
        client_id=os.environ["ATACCAMA_CLIENT_ID"],
        client_secret=os.environ["ATACCAMA_CLIENT_SECRET"],
    )

    bundles = []
    for urn in urns:
        print(f"Fetching {urn} ...", file=sys.stderr)
        bundles.append(client.fetch_bundle(urn))

    document = ataccama_to_osi(
        bundles,
        model_name=args.model_name,
        model_description=args.model_description,
        tenant=_tenant_from_urn(urns[0]),
    )

    text = yaml.dump(document, default_flow_style=False, sort_keys=False, allow_unicode=True)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text)
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
