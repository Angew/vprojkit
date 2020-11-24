"""
Copyright (c) 2020 Petr Kmoch

This file is covered by the MIT license, see accompanying LICENSE file.
"""


import argparse
from collections import namedtuple
import enum
import os.path
import re
import sys
import xml.etree.ElementTree as ET


def printed(arg, *args):
    print(arg, *args)
    return arg


class TargetType(enum.Enum):
    EXECUTABLE = enum.auto()
    STATIC_LIBRARY = enum.auto()
    SHARED_LIBRARY = enum.auto()


class Target:
    def __init__(self, project_path, sln_dir=None):
        self.name = None
        self.type = None
        self.include_directories = []
        self.compile_definitions = []

        mpath = lambda p: p+"\\" if p else p
        self.vs_macros = {
            "ProjectDir": mpath(os.path.dirname(project_path)),
            "SolutionDir": mpath(sln_dir),
        }

        self.unexpected = []

    def get_macro_expansion(self, macro):
        return self.vs_macros.get(macro)

    def add_unexpected(self, expectation, actual_text):
        self.unexpected.append((expectation, actual_text))


Expectation = namedtuple("Expectation", "tag, text")


class Program:
    """
    One run of the processing program.
    """

    def __init__(self, inputs, expand_macros=None):
        if expand_macros is None:
            expand_macros = True
        self.inputs = inputs
        self.expand_macros_on = expand_macros
        self.node_conditions = {
            None,
            "'$(Configuration)|$(Platform)'=='Release|x64'"
        }
        self.targets = []
        self.cl_expectations = [
            Expectation("Optimization", "MaxSpeed"),
            Expectation("RuntimeLibrary", "MultiThreadedDLL"),
            Expectation("PrecompiledHeader", ""),
        ]
        self.link_expectations = [
            Expectation("OutputFile", "$(OutDir)$(ProjectName)$(RADF_BUILD_VER).dll"),
            Expectation("ImportLibrary", r"$(SolutionDir)lib\$(RADF_ARCH_RELEASE)\$(ProjectName)$(RADF_BUILD_VER).lib"),
            Expectation("TargetMachine", "MachineX64"),
        ]

    @classmethod
    def from_options(cls, options):
        return Program(
            inputs=options.input,
            expand_macros=options.expand_macros
        )

    def run(self):
        inputs = self.gather_input_files()
        for input_project in inputs:
            self.read_project(input_project)
        self.write_output()

    def gather_input_files(self):
        for path in (os.path.abspath(p) for p in self.inputs):
            ext = os.path.splitext(path)[1]
            if ext == ".vcxproj":
                yield path, None
            elif ext == ".sln":
                yield from (
                    (p, os.path.dirname(path))
                    for p in self.gather_projects_from_sln(path)
                )
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

    def read_project(self, project):
        xml = ET.parse(project[0])
        root = xml.getroot()
        self.process_project(root, project)

    def write_output(self):
        out = sys.stdout
        for target in self.targets:
            if target.type == TargetType.EXECUTABLE:
                out.write(f"\nadd_executable({target.name}")
            elif target.type == TargetType.SHARED_LIBRARY:
                out.write(f"add_library({target.name} SHARED")
            elif target.type == TargetType.STATIC_LIBRARY:
                out.write(f"add_library({target.name} STATIC")
            out.write(")\n")
            if target.include_directories:
                out.write(f"target_include_directories({target.name} PRIVATE\n  ")
                out.write("\n  ".join(map(self.to_cmake_path, target.include_directories)))
                out.write("\n)\n")
            if target.compile_definitions:
                out.write(f"target_compile_definitions({target.name} PRIVATE\n  ")
                out.write("\n  ".join(target.compile_definitions))
                out.write("\n)\n")
            for ex in target.unexpected:
                out.write(f"# {target.name}: expected <{ex[0].tag}> of '{ex[0].text}' was actually '{ex[1]}'\n")


    _re_project_xmlns = re.compile(r"(\{[^}]*\})Project")
    def process_project(self, root, path_info):
        self.start_new_target(path_info)
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
                self.current_target.include_directories = self.process_list(
                    cl.find(f"{ns}AdditionalIncludeDirectories")
                )
                self.current_target.compile_definitions = self.process_list(
                    cl.find(f"{ns}PreprocessorDefinitions")
                )
                self.check_expectations(cl, self.cl_expectations, ns)
                if self.current_target.type != TargetType.STATIC_LIBRARY:
                    link = node.find(f"{ns}Link")
                    # ...
                    self.check_expectations(link, self.link_expectations, ns)


    def node_applies(self, node):
        return node.get("Condition") in self.node_conditions

    def start_new_target(self, path_info):
        self.targets.append(Target(path_info[0], sln_dir=path_info[1]))

    @property
    def current_target(self):
        return self.targets[-1]

    def process_list(self, node):
        return [
            self.expand_macros(d)
            for d in self.parse_list(node.text.strip())
            if not d.startswith("%(")
        ]

    def check_expectations(self, node, expectations, ns):
        for ex in expectations:
            text = node.find(f"{ns}{ex.tag}").text.strip()
            if text != ex.text:
                self.current_target.add_unexpected(ex, text)

    _target_type_from_xml_mapping = {
        "DynamicLibrary": TargetType.SHARED_LIBRARY
    }
    @classmethod
    def target_type_from_xml(cls, type):
        return cls._target_type_from_xml_mapping[type]

    @staticmethod
    def parse_list(text):
        return text.split(";")

    @staticmethod
    def to_cmake_path(path):
        return path.replace("\\", "/")

    _re_vs_macro = re.compile(r"\$ \( ( [^)]* ) \)", re.VERBOSE)
    def expand_macros(self, text):
        return re.sub(
            self._re_vs_macro,
            lambda m: self.expand_macro(m[1]),
            text
        )

    def expand_macro(self, macro):
        if self.expand_macros_on:
            expansion = self.get_macro_expansion(macro)
            if expansion is not None:
                return expansion
        return f"$({macro})" # fall back to no-op

    def get_macro_expansion(self, macro):
        return self.current_target.get_macro_expansion(macro)


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
    macros = parser.add_mutually_exclusive_group()
    macros.add_argument(
        "-m", "--expand-macros",
        help="Expand VS macros in properties.",
        action="store_true",
        default=True,
        dest="expand_macros",
    )
    macros.add_argument(
        "-M", "--no-expand-macros",
        help="Do not expand VS macros in properties.",
        action="store_false",
        default=True,
        dest="expand_macros",
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
