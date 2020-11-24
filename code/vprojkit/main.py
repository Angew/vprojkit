"""
Copyright (c) 2020 Petr Kmoch

This file is covered by the MIT license, see accompanying LICENSE file.
"""


import argparse
import enum
import os.path
import re
import sys
import xml.etree.ElementTree as ET


class TargetType(enum.Enum):
    EXECUTABLE = enum.auto()
    STATIC_LIBRARY = enum.auto()
    SHARED_LIBRARY = enum.auto()


class Target:
    def __init__(self):
        self.name = None
        self.type = None
        self.include_directories = []


class Program:
    """
    One run of the processing program.
    """

    def __init__(self, inputs):
        self.inputs = inputs
        self.node_conditions = {
            None,
            "'$(Configuration)|$(Platform)'=='Release|x64'"
        }
        self.targets = []

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

    _re_sln_project_line = re.compile(
        r"""
            ^
            Project\("\{[-0-9A-F]*\}"\) \s = \s
            "[^"]*" , \s
            "(?P<project_path> [^"]* \.vcxproj)"
        """,
        re.VERBOSE
    )
    @classmethod
    def gather_projects_from_sln(cls, sln_path):
        sln_dir = os.path.dirname(sln_path)
        with open(sln_path) as sln:
            for line in sln:
                match = cls._re_sln_project_line.match(line)
                if (match):
                    yield os.path.join(sln_dir, match.group("project_path"))

    def read_file(self, path):
        xml = ET.parse(path)
        root = xml.getroot()
        self.process_project(root)

    def write_output(self):
        out = sys.stdout
        for target in self.targets:
            if target.type == TargetType.EXECUTABLE:
                out.write(f"add_executable({target.name}")
            elif target.type == TargetType.SHARED_LIBRARY:
                out.write(f"add_library({target.name} SHARED")
            elif target.type == TargetType.STATIC_LIBRARY:
                out.write(f"add_library({target.name} STATIC")
            out.write(")\n")
            if target.include_directories:
                out.write(f"target_include_directories({target.name} PRIVATE\n  ")
                out.write("\n  ".join(target.include_directories))
                out.write("\n)\n")

    _re_project_xmlns = re.compile(r"(\{[^}]*\})Project")
    def process_project(self, root):
        self.start_new_target()
        ns = self._re_project_xmlns.match(root.tag)[1]
        for node in (child for child in root if self.node_applies(child)):
            if node.tag == f"{ns}PropertyGroup":
                label = node.get("Label")
                if label == "Globals":
                    self.current_target.name = node.find(f"{ns}ProjectName").text
                elif label == "Configuration":
                    self.current_target.type = self.target_type_from_xml(node.find(f"{ns}ConfigurationType").text)
            elif node.tag == f"{ns}ItemDefinitionGroup":
                cl = node.find(f"{ns}ClCompile")
                self.current_target.include_directories = [
                    self.expand_macros(d) for d in 
                    self.parse_list(cl.find(f"{ns}AdditionalIncludeDirectories").text)
                    if not d.startswith("%(")
                ]

    def node_applies(self, node):
        return node.get("Condition") in self.node_conditions
        
    def start_new_target(self):
        self.targets.append(Target())
        
    @property
    def current_target(self):
        return self.targets[-1]

    _target_type_from_xml_mapping = {
        "DynamicLibrary": TargetType.SHARED_LIBRARY
    }
    @classmethod
    def target_type_from_xml(cls, type):
        return cls._target_type_from_xml_mapping[type]

    @staticmethod
    def parse_list(text):
        return text.split(";")
        
    _re_vs_macro = re.compile(r"\$ \( ( [^)]* ) \)", re.VERBOSE)
    def expand_macros(self, text):
        return re.sub(
            self._re_vs_macro,
            lambda m: self.expand_macro(m[1]),
            text
        )
        
    def expand_macro(self, macro):
        return f"$({macro})" #no-op for now, will be fallback anyway


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


def run(options):
    return Program.from_options(options).run()


def main(args, prog=None):
    parser = create_argument_parser(prog)
    options = parser.parse_args(args)
    return run(options)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
