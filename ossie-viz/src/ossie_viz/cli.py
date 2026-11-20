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

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from ossie_viz.renderer import render_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ossie-viz",
        description="Render a validated .ossie.yaml contract into a self-contained offline HTML visualizer.",
    )
    parser.add_argument(
        "model_path",
        type=Path,
        help="Path to the .ossie.yaml semantic model file.",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="Output HTML file path (defaults to <model_path>.html).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated HTML file in the default web browser.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    model_path: Path = args.model_path
    if not model_path.exists():
        print(f"Error: model file not found: {model_path}", file=sys.stderr)
        return 1

    out_path = args.out or model_path.with_suffix(".html")
    rendered_path = render_file(model_path, out_path)
    print(f"Generated Ossie Semantic Visualizer -> {rendered_path}")

    if args.open:
        webbrowser.open(rendered_path.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
