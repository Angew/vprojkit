"""
Copyright (c) 2020 Petr Kmoch

This file is covered by the MIT license, see accompanying LICENSE file.
"""


import argparse
import os.path
import re
import sys


class Program:
    re_sln_project_line = re.compile(
        r"""
            ^
            Project\("\{[-0-9A-F]*\}"\) \s = \s
            "[^"]*" , \s
            "(?P<project_path> [^"]* \.vcxproj)"
        """,
        re.VERBOSE
    )
    
    def __init__(self, inputs):
        self.inputs = inputs

    @classmethod
    def from_options(cls, options):
        return Program(inputs=options.input)

    def run(self):
        inputs = self.gather_input_files()
        for input_path in inputs:
            self.read_file(input_path)
        self.write_output()

    def gather_input_files(self):
        for path in self.inputs:
            ext = os.path.splitext(path)[1]
            if ext == ".vcxproj":
                yield path
            elif ext == ".sln":
                yield from self.gather_projects_from_sln(path)
            else:
                raise ValueError(f"Unsupported file type of {path}")

    @classmethod
    def gather_projects_from_sln(cls, sln_path):
        sln_dir = os.path.dirname(sln_path)
        with open(sln_path) as sln:
            for line in sln:
                match = cls.re_sln_project_line.match(line)
                if (match):
                    yield os.path.join(sln_dir, match.group("project_path"))

    def read_file(self, path):
        print(path)

    def write_output(self):
        pass


def create_argument_parser(prog=None):
    if prog is None: prog = sys.argv[0]
    parser = argparse.ArgumentParser(
        prog=prog,
        fromfile_prefix_chars="@",
    )
    parser.add_argument(
        "-o", "--output",
        help="Destination where parsed output will be stored.",
        default="-",
    )
    parser.add_argument(
        "input",
        help=".sln or .vcxproj file(s) to process",
        nargs="*",
    )
    return parser


def main(args, prog=None):
    parser = create_argument_parser(prog)
    options = parser.parse_args(args)
    return Program.from_options(options).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
