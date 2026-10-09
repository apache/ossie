# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Command-line interface for the finance semantic-view converter.

    ossie-wisdom-semantic-view to-wisdom <view.yaml> -o <domain.json> [--enrich <file>]
    ossie-wisdom-semantic-view to-semantic-view <domain.json> -o <view.yaml>

`to-wisdom` reads one Snowflake semantic view and writes a Wisdom domain-export
JSON (format 1.0). The Ossie document is the step in between and is not written.
`--enrich` applies one enrichment file after that import.

`to-semantic-view` reads that JSON and writes a Snowflake semantic view. Metric
synonyms and the business-context instruction survive that direction.

Unknown keys and unsupported expressions exit 1. This command does not call
ossie-snowflake or ossie-wisdom.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ossie_wisdom_semantic_view.pipeline import semantic_view_to_wisdom, wisdom_to_semantic_view


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ossie-wisdom-semantic-view",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    to_wisdom = sub.add_parser(
        "to-wisdom",
        help="Snowflake semantic view → Ossie → Wisdom domain-export JSON",
    )
    to_wisdom.add_argument("view", metavar="VIEW", help="Snowflake semantic view YAML")
    to_wisdom.add_argument("-o", "--output", required=True, metavar="FILE", help="Wisdom domain-export JSON")
    to_wisdom.add_argument(
        "--enrich",
        metavar="FILE",
        help="enrichment YAML applied to the domain export after import",
    )

    to_view = sub.add_parser(
        "to-semantic-view",
        help="Wisdom domain-export JSON → Ossie → Snowflake semantic view",
    )
    to_view.add_argument("domain", metavar="DOMAIN", help="Wisdom domain-export JSON")
    to_view.add_argument("-o", "--output", required=True, metavar="FILE", help="Snowflake semantic view YAML")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "to-wisdom":
        enrichment = Path(args.enrich).read_text() if args.enrich else None
        text = semantic_view_to_wisdom(Path(args.view).read_text(), enrichment)
    else:
        text = wisdom_to_semantic_view(Path(args.domain).read_text())
    Path(args.output).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
