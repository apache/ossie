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

"""Reproducibly restore the optional Microsoft TOM assemblies from NuGet."""

import argparse
import json
import subprocess
from pathlib import Path

AMO_VERSION = "19.114.8"
PROJECT = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ManagePackageVersionsCentrally>false</ManagePackageVersionsCentrally>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Microsoft.AnalysisServices" Version="{version}" />
  </ItemGroup>
</Project>
"""
RUNTIME_CONFIG = {
    "runtimeOptions": {
        "tfm": "net8.0",
        "framework": {"name": "Microsoft.NETCore.App", "version": "8.0.0"},
        "rollForward": "Major",
    }
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".tom") / "assemblies",
        help="assembly output directory (default: .tom/assemblies)",
    )
    args = parser.parse_args(argv)

    output = args.output.resolve()
    work = output.parent
    work.mkdir(parents=True, exist_ok=True)
    project = work / "tom-dependencies.csproj"
    project.write_text(PROJECT.format(version=AMO_VERSION), encoding="utf-8")
    subprocess.run(
        [
            "dotnet",
            "publish",
            str(project),
            "--configuration",
            "Release",
            "--output",
            str(output),
        ],
        check=True,
    )
    (output / "dotnet.runtimeconfig.json").write_text(
        json.dumps(RUNTIME_CONFIG, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Restored Microsoft.AnalysisServices {AMO_VERSION} to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
