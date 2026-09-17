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

"""Command-line interface for the semantido <> Apache Ossie converter."""

import argparse
import sys

import yaml
from ossie import OSIDocument

from ossie_semantido.loaders import load_from_module
from ossie_semantido.ossie_to_semantido import ossie_to_semantido_source
from ossie_semantido.semantido_to_ossie import semantic_layer_to_ossie


def _report_issues(issues) -> None:
    for issue in issues:
        print(
            f"warning: {issue.issue_type.value}: {issue.element_name}", file=sys.stderr
        )


def _cmd_semantido_to_ossie(args: argparse.Namespace) -> None:
    layer = load_from_module(args.module, sys_path=args.path)
    result = semantic_layer_to_ossie(layer, model_name=args.name)
    _report_issues(result.issues)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(result.output.to_osi_yaml())


def _cmd_ossie_to_semantido(args: argparse.Namespace) -> None:
    with open(args.input, "r", encoding="utf-8") as handle:
        document = OSIDocument.model_validate(yaml.safe_load(handle))
    result = ossie_to_semantido_source(document)
    _report_issues(result.issues)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(result.output)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ossie-semantido",
        description="Convert between semantido semantic layers and Apache Ossie documents",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    to_ossie = subparsers.add_parser(
        "semantido-to-ossie", help="Convert decorated SQLAlchemy models → Ossie YAML"
    )
    to_ossie.add_argument(
        "-m",
        "--module",
        required=True,
        metavar="MODULE",
        help="Dotted module path of the semantido models, e.g. models.emir_reporting",
    )
    to_ossie.add_argument(
        "-p",
        "--path",
        default=None,
        metavar="DIR",
        help="Directory prepended to sys.path before import",
    )
    to_ossie.add_argument(
        "-n",
        "--name",
        required=True,
        metavar="NAME",
        help="Name for the Ossie semantic_model",
    )
    to_ossie.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="FILE",
        help="Path for output Ossie YAML",
    )
    to_ossie.set_defaults(func=_cmd_semantido_to_ossie)

    from_ossie = subparsers.add_parser(
        "ossie-to-semantido", help="Convert Ossie YAML → generated semantido model code"
    )
    from_ossie.add_argument(
        "-i", "--input", required=True, metavar="FILE", help="Path to Ossie YAML"
    )
    from_ossie.add_argument(
        "-o",
        "--output",
        required=True,
        metavar="FILE",
        help="Path for generated Python module",
    )
    from_ossie.set_defaults(func=_cmd_ossie_to_semantido)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
