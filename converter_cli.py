"""Batch conversion for the extended CSTR command-line inputs."""

import sys

from comic_source import Source


def run_inputs(args, options):
    output_dir = args.output_dir
    if output_dir is not None and not args.list_pages:
        output_dir.mkdir(parents=True, exist_ok=True)
    used = set()
    failures = 0
    for path in args.input:
        try:
            source = Source.from_path(path)
            if args.list_pages:
                names = source.page_names()
                if len(args.input) > 1:
                    print(f"[{path}]")
                for index, name in enumerate(names, 1):
                    print(f"{index}\t{name}")
                continue

            suggested = source.suggested_output()
            destination = args.output or (output_dir / suggested.name if output_dir is not None else suggested)
            output = destination
            suffix = 2
            while output.resolve() in used:
                output = destination.with_name(f"{destination.stem} ({suffix}){destination.suffix}")
                suffix += 1
            used.add(output.resolve())
            if not args.overwrite and output.exists():
                raise FileExistsError(f"Output already exists: {output}. Use --overwrite to replace it.")
            count, chunks = source.convert(output, dict(options, overwrite=args.overwrite))
            print(f"{output} ({count} pages, {chunks} chunks)")
        except Exception as error:
            failures += 1
            print(f"cstr: {path}: {error or type(error).__name__}", file=sys.stderr)
    return 1 if failures else 0
