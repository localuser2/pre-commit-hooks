#!/usr/bin/env python3
"""Tests clang-format, clang-tidy, and oclint against .c and .cpp
With this snippet:

    int main() {  int i;  return 10;}

- Triggers clang-format because what should be on 4 lines is on 1
- Triggers clang-tidy because "magical number" 10 is used
- Triggers oclint because short variable name is used

pytest_generate_tests comes from pytest documentation and allows for
table tests to be generated and each treated as a test by pytest.
This allows for 45 tests with a descrition instead of 3 which
functionally tests the same thing.
"""
import json
import os
import re
import shutil
import subprocess as sp
import tempfile

import pytest

import tests.test_utils as utils
from hooks.clang_format import ClangFormatCmd
from hooks.clang_tidy import ClangTidyCmd
from hooks.cppcheck import CppcheckCmd
from hooks.cpplint import CpplintCmd
from hooks.include_what_you_use import IncludeWhatYouUseCmd
from hooks.oclint import OCLintCmd
from hooks.uncrustify import UncrustifyCmd


class GeneratorT:
    """Generate the test scenarios"""

    @classmethod
    def setup_class(cls):
        """Create test files that will be used by other tests"""
        cls.scenarios = cls.generate_table_tests()

    @classmethod
    def generate_list_tests(cls):
        """Generate the scenarios for class (45)

        This is all the arg (6) and file (4) combinations
        +2x tests:
            * Call the shell hooks installed with pip to mimic end user use
            * Call via importing the command classes to verify expectations"""
        cls.versions = utils.get_versions()

        test_repo_dir = os.path.join(os.getcwd(), "tests", "test_repo")
        cls.err_c = os.path.join(test_repo_dir, "err.c")
        cls.err_cpp = os.path.join(test_repo_dir, "err.cpp")
        cls.ok_c = os.path.join(test_repo_dir, "ok.c")
        cls.ok_cpp = os.path.join(test_repo_dir, "ok.cpp")
        cls.files = [cls.ok_c, cls.ok_cpp, cls.err_c, cls.err_cpp]
        cls.retcodes = [0, 0, 1, 1]

        cls.scenarios = []
        cls.scenarios += cls.get_multifile_scenarios_no_diff([cls.err_c, cls.err_cpp])
        cls.scenarios += cls.generate_formatter_tests()
        cls.scenarios += cls.generate_clang_tidy_tests()
        cls.scenarios += cls.generate_cppcheck_tests()
        cls.scenarios += cls.generate_cpplint_tests()
        if os.name != "nt":
            # iwyu works on windows, but doesn't have a choco package
            cls.scenarios += cls.generate_iwyu_tests()
            # oclint does not work on windows
            cls.scenarios += cls.generate_oclint_tests()

    @classmethod
    def generate_formatter_tests(cls):
        """Tests for both uncrustify and clang-format. Both should generate the same error output."""
        clang_format_args_sets = [["--style=google"], ["--style=google", "-i"]]
        clang_format_err = """{0}
====================
--- original

+++ formatted

@@ -1,2 +1,5 @@

 #include {1}
-int main(){{int i;return;}}
+int main() {{
+  int i;
+  return;
+}}
"""  # noqa: E501
        formatter_c_err = clang_format_err.format(cls.err_c, "<stdio.h>").encode()
        formatter_cpp_err = clang_format_err.format(cls.err_cpp, "<string>").encode()
        formatter_output = [b"", b"", formatter_c_err, formatter_cpp_err]

        # Specify config file as autogenerated one varies between uncrustify versions.
        # v0.66 on ubuntu creates an invalid config; v0.68 on osx does not.
        unc_defaults_path = os.path.join(os.getcwd(), "tests", "uncrustify_defaults.cfg")
        unc_base_args = ["-c", unc_defaults_path]
        unc_addtnl_args = [[], ["--replace", "--no-backup"]]
        uncrustify_arg_sets = [unc_base_args + arg for arg in unc_addtnl_args]

        scenarios = []
        for i in range(len(cls.files)):
            for arg_set in clang_format_args_sets:
                clang_format_scenario = [ClangFormatCmd, arg_set, [cls.files[i]], formatter_output[i], cls.retcodes[i]]
                scenarios += [clang_format_scenario]
            for arg_set in uncrustify_arg_sets:
                uncrustify_scenario = [UncrustifyCmd, arg_set, [cls.files[i]], formatter_output[i], cls.retcodes[i]]
                scenarios += [uncrustify_scenario]
        return scenarios

    def get_multifile_scenarios_no_diff(err_files):
        """Create tests to verify that commands are handling both err.c/err.cpp as input correctly and that --no-diff disables diff output."""
        expected_err = b""
        unc_defaults_path = os.path.join(os.getcwd(), "tests", "uncrustify_defaults.cfg")
        scenarios = [
            [ClangFormatCmd, ["--style=google", "--no-diff"], err_files, expected_err, 1],
            [UncrustifyCmd, ["-c", unc_defaults_path, "--no-diff"], err_files, expected_err, 1],
        ]
        return scenarios

    @classmethod
    def generate_clang_tidy_tests(cls):
        """Don't care about warnings. They'll be removed in later tests."""
        ct_base_args = ["-quiet", "-checks=clang-diagnostic-return-type"]
        # Run normal, plus two in-place arguments
        additional_args = [[], ["-fix"], ["--fix-errors"], ["--", "-std=c18"]]
        clang_tidy_args_sets = [ct_base_args + arg for arg in additional_args]
        clang_tidy_err_str = """{0}:2:18: error: non-void function 'main' should return a value [clang-diagnostic-return-type]
int main(){{int i;return;}}
                 ^
1 error generated.
Error while processing {0}.
"""  # noqa: E501
        clang_tidy_str_c = clang_tidy_err_str.format(cls.err_c, "").encode()
        clang_tidy_str_cpp = clang_tidy_err_str.format(cls.err_cpp).encode()
        clang_tidy_output = [b"", b"", clang_tidy_str_c, clang_tidy_str_cpp]
        scenarios = []
        for i in range(len(cls.files)):
            for arg_set in clang_tidy_args_sets:
                # For double dash args, make sure to use the appropriate language
                new_arg_set = list(arg_set)
                if cls.files[i].endswith(".cpp") and "-std=c18" in arg_set:
                    new_arg_set[new_arg_set.index("-std=c18")] = "-std=c++20"
                    # Clang tidy c++20 generates additional warnings
                clang_tidy_scenario = [ClangTidyCmd, new_arg_set, [cls.files[i]], clang_tidy_output[i], cls.retcodes[i]]
                scenarios += [clang_tidy_scenario]
        return scenarios

    @classmethod
    def generate_cppcheck_tests(cls):
        cppcheck_arg_sets = [[]]
        # cppcheck adds unnecessary error information.
        # See https://stackoverflow.com/questions/6986033
        if cls.versions["cppcheck"] <= "1.88":
            cppcheck_err = "[{}:1]: (style) Unused variable: i\n"
        # They've made changes to messaging
        elif cls.versions["cppcheck"] >= "1.89":
            cppcheck_err = """{}:2:16: style: Unused variable: i [unusedVariable]
int main(){{int i;return;}}
               ^
"""
        else:
            print("Problem parsing version for cppcheck", cls.versions["cppcheck"])
            print("Please create an issue on github.com/pocc/pre-commit-hooks")
            cppcheck_err = b""
        cppcheck_err_c = cppcheck_err.format(cls.err_c).encode()
        cppcheck_err_cpp = cppcheck_err.format(cls.err_cpp).encode()
        cppcheck_output = [b"", b"", cppcheck_err_c, cppcheck_err_cpp]
        scenarios = []
        for i in range(len(cls.files)):
            for arg_set in cppcheck_arg_sets:
                cppcheck_scenario = [CppcheckCmd, arg_set, [cls.files[i]], cppcheck_output[i], cls.retcodes[i]]
                scenarios += [cppcheck_scenario]
        return scenarios

    @classmethod
    def generate_cpplint_tests(cls):
        cpplint_arg_sets = [["--verbose=0", "--quiet"]]
        cpplint_err_str = """\
Done processing {0}
Total errors found: 5
{0}:0:  No copyright message found.  You should have a line: "Copyright [year] <Copyright Owner>"  [legal/copyright] [5]
{0}:2:  More than one command on the same line  [whitespace/newline] [0]
{0}:2:  Missing space after ;  [whitespace/semicolon] [3]
{0}:2:  Missing space before {{  [whitespace/braces] [5]
{0}:2:  Could not find a newline character at the end of the file.  [whitespace/ending_newline] [5]
"""
        cpplint_err_c = cpplint_err_str.format(cls.err_c).encode()
        cpplint_err_cpp = cpplint_err_str.format(cls.err_cpp).encode()
        cpplint_output = [b"", b"", cpplint_err_c, cpplint_err_cpp]
        scenarios = []
        for i in range(len(cls.files)):
            for arg_set in cpplint_arg_sets:
                cpplint_scenario = [CpplintCmd, arg_set, [cls.files[i]], cpplint_output[i], cls.retcodes[i]]
                scenarios += [cpplint_scenario]
        return scenarios

    @classmethod
    def generate_iwyu_tests(cls):
        iwyu_arg_sets = [[]]
        iwyu_err_c = """{0}:2:18: error: non-void function 'main' should return a value [-Wreturn-type]
int main(){{int i;return;}}
                 ^

{0} should add these lines:

{0} should remove these lines:
- #include <stdio.h>  // lines 1-1

The full include-list for {0}:
---
""".format(
            cls.err_c
        ).encode()
        iwyu_err_cpp = """{0}:2:18: error: non-void function 'main' should return a value [-Wreturn-type]
int main(){{int i;return;}}
                 ^

{0} should add these lines:

{0} should remove these lines:
- #include <string>  // lines 1-1

The full include-list for {0}:
---
""".format(
            cls.err_cpp
        ).encode()
        iwyu_retcodes = [0, 0, 3, 3]
        iwyu_output = [b"", b"", iwyu_err_c, iwyu_err_cpp]
        scenarios = []
        for i in range(len(cls.files)):
            for arg_set in iwyu_arg_sets:
                iwyu_scenario = [IncludeWhatYouUseCmd, arg_set, [cls.files[i]], iwyu_output[i], iwyu_retcodes[i]]
                scenarios += [iwyu_scenario]
        return scenarios

    @classmethod
    def generate_oclint_tests(cls):
        scenarios = []
        oclint_err = """
Compiler Errors:
(please be aware that these errors will prevent OCLint from analyzing this source code)

{0}:2:18: non-void function 'main' should return a value

Clang Static Analyzer Results:

{0}:2:18: non-void function 'main' should return a value


OCLint Report

Summary: TotalFiles=0 FilesWithViolations=0 P1=0 P2=0 P3=0{1}


[OCLint (http{2}://oclint.org) v{3}]
"""
        # -no-analytics required because in some versions of oclint, this causes oclint to hang (0.13.1)
        # version 20+ starts using --<option> instead of -<option>
        # Link is to https://oclint.org instead of http://oclint.org in versions >= 20
        https_s = ""
        if cls.versions["oclint"] > "20":
            oclint_arg_sets = [["--enable-global-analysis", "--enable-clang-static-analyzer"]]
            https_s = "s"
        else:
            oclint_arg_sets = [["-enable-global-analysis", "-enable-clang-static-analyzer", "-no-analytics"]]
        oclint_arg_sets[0] += ["--", "-std=c18"]
        ver_output = sp.check_output(["oclint", "--version"]).decode("utf-8")
        oclint_ver = re.search(r"OCLint version ([\d.]+)\.", ver_output).group(1)
        eol_whitespace = " "
        oclint_err_str_c = oclint_err.format(cls.err_c, eol_whitespace, https_s, oclint_ver).encode()
        oclint_err_str_cpp = oclint_err.format(cls.err_cpp, eol_whitespace, https_s, oclint_ver).encode()
        oclint_output = [b"", b"", oclint_err_str_c, oclint_err_str_cpp]
        oclint_retcodes = [0, 0, 6, 6]
        for i in range(len(cls.files)):
            for arg_set in oclint_arg_sets:
                # For double dash args, make sure to use the appropriate language
                new_arg_set = list(arg_set)
                if cls.files[i].endswith(".cpp") and "-std=c18" in arg_set:
                    new_arg_set[new_arg_set.index("-std=c18")] = "-std=c++20"
                oclint_scenario = [OCLintCmd, new_arg_set, [cls.files[i]], oclint_output[i], oclint_retcodes[i]]
                scenarios += [oclint_scenario]
        return scenarios


class TestHooks:
    """Test all C Linters: clang-format, clang-tidy, and oclint."""

    @classmethod
    def setup_class(cls):
        """Create test files that will be used by other tests.

        This function runs 2 types of test functions:
        * testing with the shell command (cls.run_shell_cmd)
        * imitating a commit with pre-commit hooks (cls.run_integration_test)

        shell command tests are generated by this file.
        integration tests are loaded from a json that was also created by this file
        with generate_table_json() in this file.

        cls.run_cmd_class is redundant, but available.
        """
        utils.set_git_identity()  # set a git identity if one doesn't exist
        generator = GeneratorT()
        generator.generate_list_tests()
        scenarios = generator.scenarios
        test_repo_temp = os.path.join("tests", "test_repo", "temp")
        os.makedirs(test_repo_temp, exist_ok=True)
        tmpdir = os.path.join(tempfile.gettempdir(), "pre-commit-hooks-testing")
        os.makedirs(tmpdir, exist_ok=True)
        base_files = ["ok.c", "ok.cpp", "err.c", "err.cpp"]
        filenames = [os.path.join("tests", "test_repo", f) for f in base_files]
        utils.set_compilation_db(filenames)
        temp_filenames = [os.path.join(tmpdir, f) for f in base_files]
        utils.set_compilation_db(temp_filenames)
        cls.scenarios = []
        for s in scenarios:
            desc = " ".join([cls.run_shell_cmd.__name__, s[0].command, " ".join(s[2]), " ".join(s[1])])
            if os.name == "nt":
                s[2] = [arg.replace("/", "\\\\") for arg in s[2]]
            test_scenario = [
                desc,
                {
                    "test_type": cls.run_shell_cmd,
                    "cmd": s[0].command,  # Changing from s[0], breaking compatibility with cls.run_class_cmd
                    "args": s[1],
                    "files": s[2],
                    "expd_output": s[3],
                    "expd_retcode": s[4],
                },
            ]
            cls.scenarios += [test_scenario]
        table_tests_integration = os.path.join("tests", "table_tests_integration.json")
        with open(table_tests_integration) as f:
            json_str = f.read()
        table_tests = json.loads(json_str)
        # initialize repo
        utils.run_in(["git", "init"], tmpdir)
        utils.run_in(["pre-commit", "install"], tmpdir)
        for s in table_tests:
            s["args"] = [arg.replace("{repo_dir}", os.getcwd()) for arg in s["args"]]
            s["files"] = [arg.replace("{test_dir}", tmpdir) for arg in s["files"]]
            if os.name == "nt":
                s["files"] = [arg.replace("/", "\\\\") for arg in s["files"]]
            desc = " ".join(
                [cls.run_integration_test.__name__, s["command"] + "-hook", " ".join(s["files"]), " ".join(s["args"])]
            )
            test_scenario = [
                desc,
                {
                    "test_type": cls.run_integration_test,
                    "cmd_name": s["command"],
                    "args": s["args"],
                    "files": s["files"],
                    "expd_output": s["expd_output"].encode(),
                    "expd_retcode": s["expd_retcode"],
                },
            ]
            cls.scenarios += [test_scenario]

    @staticmethod
    def determine_edit_in_place(cmd_name, args):
        """runtime means to check if cmd/args will edit files"""
        clang_format_in_place = cmd_name == "clang-format" and "-i" in args
        clang_tidy_in_place = cmd_name == "clang-tidy" and ("-fix" in args or "--fix-errors" in args)
        uncrustify_in_place = cmd_name == "uncrustify" and "--replace" in args
        return clang_format_in_place or clang_tidy_in_place or uncrustify_in_place

    def test_run(self, test_type, cmd_name, args, files, expd_output, expd_retcode):
        """Test each command's class from its python file
        and the command for each generated by setup.py."""
        fix_in_place = self.determine_edit_in_place(cmd_name, args)
        has_err_file = any(["err.c" in f for f in files])
        will_fix_in_place = fix_in_place and has_err_file
        test_type(cmd_name, files, args, expd_output, expd_retcode)
        if will_fix_in_place:
            for f in files:  # Restore files if they could have been changed
                base_name = os.path.split(f)[-1]
                with open(f, "w") as f:
                    f.write(utils.test_file_strs[base_name])

    @staticmethod
    def run_integration_test(cmd_name, files, args, target_output, target_retcode):
        """Run integration tests by

        1. Convert arg lists to .pre-commit-config.yaml text
        2. Set the .pre-commit-config.yaml in a directory with test files
        3. Run `git init; pre-commit install; git add .; git commit` against the files
        """
        tmpdir = os.path.join(tempfile.gettempdir(), "pre-commit-hooks-testing")
        target_output = target_output.replace(b"{test_dir}", tmpdir.encode())
        for test_file in files:
            test_file_base = os.path.split(test_file)[-1]
            with open(test_file, "w") as fd:
                fd.write(utils.test_file_strs[test_file_base])
        # Add only the files we are testing
        utils.run_in(["git", "reset"], tmpdir)
        utils.run_in(["git", "add"] + files, tmpdir)
        args = list(args)  # redeclare so there's no memory weirdness
        pre_commit_config_path = os.path.join(tmpdir, ".pre-commit-config.yaml")
        pre_commit_config = f"""\
repos:
- repo: https://github.com/pocc/pre-commit-hooks
  rev: 8a67133
  hooks:
    - id: {cmd_name}
      args: {args}
"""
        with open(pre_commit_config_path, "w") as f:
            f.write(pre_commit_config)

        # Pre-commit run will only work on staged files, which is what we want to test
        # Using git commit can cause hangs if pre-commit passes
        sp_child = sp.run(["pre-commit", "run"], cwd=tmpdir, stdout=sp.PIPE, stderr=sp.PIPE)
        output_actual = sp_child.stderr + sp_child.stdout
        # Get rid of pre-commit first run info lines
        output_actual = re.sub(rb"\[INFO\].*\n", b"", output_actual)
        # Output is unpredictable and platform/version dependent
        if any([f.endswith("err.cpp") for f in files]) and "-std=c++20" in args:
            output_actual = re.sub(rb"[\d,]+ warnings and ", b"", output_actual)
        if output_actual == b"":
            pytest.fail("pre-commit should provide output, but none found.")

        utils.assert_equal(target_output, output_actual)
        assert target_retcode == sp_child.returncode

    @staticmethod
    def generate_table_json(command, files, args, output_actual, target_retcode):
        """Generate a JSON with table test values using one of the test_run commands."""
        if not os.path.exists("table_tests.json"):
            with open("table_test.json") as f:
                f.write("[]")  # Empty table tests
        with open("table_tests.json") as f:
            json_str = f.read()
            file_tests = json.loads(json_str)
            # expected to be done on *nix system
            expd_output = output_actual.decode().replace("/tmp/pre-commit-testing", "{test_dir}")
            new_test = {
                "command": command,
                "files": [f.replace(os.getcwd(), "") for f in files],
                "args": args,
                "expd_output": expd_output,
                "expd_retcode": target_retcode,
            }
            file_tests.append(new_test)
        with open("table_tests.json", "w") as f:
            text = json.dumps(file_tests)
            f.write(text)

    @staticmethod
    def run_shell_cmd(cmd_name, files, args, target_output, target_retcode):
        """Use command generated by setup.py and installed by pip
        Ex. oclint => oclint-hook for the hook command"""
        all_args = files + args
        cmd_to_run = [cmd_name + "-hook", *all_args]
        sp_child = sp.run(cmd_to_run, stdout=sp.PIPE, stderr=sp.PIPE)
        actual = sp_child.stdout + sp_child.stderr
        # Output is unpredictable and platform/version dependent
        if any([f.endswith("err.cpp") for f in files]) and "-std=c++20" in args:
            actual = re.sub(rb"[\d,]+ warnings and ", b"", actual)
        retcode = sp_child.returncode
        utils.assert_equal(target_output, actual)
        assert target_retcode == retcode

    @staticmethod
    def teardown_class():
        """Delete files generated by these tests."""
        test_repo_dir = os.path.join("tests", "test_repo")
        generated_files = [os.path.join(test_repo_dir, f) for f in ["ok.plist", "err.plist"]]
        for filename in generated_files:
            if os.path.exists(filename):
                os.remove(filename)
