"""Command-line interface for the OSI <-> Omni converter.

    osi-omni export -i model.yaml -o omni_model/ [--base-view orders] [--dialect SNOWFLAKE]
    osi-omni import -i omni_model/ [-o model.yaml] [--name my_model] [--topic orders]

`export` converts an OSI semantic model into an Omni model directory
(model.yaml / relationships.yaml / views/*.view.yaml / topics/*.topic.yaml);
`import` does the reverse. Import with no `-o` writes the OSI YAML to stdout;
export always needs `-o` (a directory). Conversions that drop information emit
warnings to stderr.
"""

import argparse
import os
import sys

from ._common import ConversionError
from .omni_to_osi import convert_omni_to_osi
from .osi_to_omni import convert_osi_to_omni


def _build_parser():
    parser = argparse.ArgumentParser(prog="osi-omni", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    exp = sub.add_parser("export", help="OSI semantic model -> Omni model directory")
    exp.add_argument("-i", "--input", required=True, help="OSI YAML file")
    exp.add_argument("-o", "--output", required=True,
                     help="output directory for the Omni model files")
    exp.add_argument("-b", "--base-view",
                     help="dataset the generated topic is rooted at "
                          "(default: the FK-sink dataset)")
    exp.add_argument("-d", "--dialect",
                     help="preferred OSI expression dialect (e.g. SNOWFLAKE); "
                          "ANSI_SQL is always the fallback")

    imp = sub.add_parser("import", help="Omni model directory -> OSI semantic model YAML")
    imp.add_argument("-i", "--input", required=True, help="Omni model directory")
    imp.add_argument("-o", "--output", help="output OSI YAML file (default: stdout)")
    imp.add_argument("--name", help="OSI model name (default: the mapped topic's name)")
    imp.add_argument("--topic",
                     help="topic whose description/AI context map onto the OSI model "
                          "(default: the sole topic, if there is exactly one)")
    return parser


def _read_model_dir(path):
    """Collect every YAML file under an Omni model directory as {relative path:
    text}. Hidden files and non-YAML extensions (except the canonical
    extensionless `model`/`relationships`/`*.view`/`*.topic` names) are skipped."""
    if not os.path.isdir(path):
        raise ConversionError(f"'{path}' is not a directory")
    files = {}
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in sorted(dirnames) if not d.startswith(".")]
        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fname), path)
            rel = rel.replace(os.sep, "/")
            base = fname.lower()
            if not (base.endswith((".yaml", ".yml", ".view", ".topic"))
                    or base in ("model", "relationships")):
                continue
            with open(os.path.join(dirpath, fname)) as fh:
                files[rel] = fh.read()
    return files


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "export":
            with open(args.input) as fh:
                osi_yaml = fh.read()
            files = convert_osi_to_omni(osi_yaml, base_view=args.base_view,
                                        dialect=args.dialect)
            for rel, text in files.items():
                dest = os.path.join(args.output, *rel.split("/"))
                os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                with open(dest, "w") as fh:
                    fh.write(text)
            print(f"Wrote {len(files)} file(s) to {args.output}", file=sys.stderr)
        else:
            files = _read_model_dir(args.input)
            out = convert_omni_to_osi(files, model_name=args.name, topic=args.topic)
            if args.output:
                with open(args.output, "w") as fh:
                    fh.write(out)
            else:
                sys.stdout.write(out)
    except (ConversionError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
