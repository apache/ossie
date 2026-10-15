#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI shim exposing GoodData's Python API to the interop harness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from ossie_gooddata import gooddata_to_ossie, ossie_to_gooddata
from ossie_gooddata.models import gd_model_from_dict, gd_model_to_dict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("export", "import"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.direction == "export":
        ossie = yaml.safe_load(args.input.read_text(encoding="utf-8"))
        native = gd_model_to_dict(ossie_to_gooddata(ossie))
        args.output.write_text(json.dumps(native, indent=2) + "\n", encoding="utf-8")
        return 0

    native = json.loads(args.input.read_text(encoding="utf-8"))
    ossie = gooddata_to_ossie(gd_model_from_dict(native))
    args.output.write_text(yaml.safe_dump(ossie, sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
