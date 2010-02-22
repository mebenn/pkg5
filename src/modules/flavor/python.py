#!/usr/bin/python
#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#

#
# Copyright 2010 Sun Microsystems, Inc.  All rights reserved.
# Use is subject to license terms.
#

import os
import re
import subprocess
import sys

import pkg.flavor.base as base
import pkg.flavor.depthlimitedmf as modulefinder

from pkg.portable import PD_LOCAL_PATH

class PythonModuleMissingPath(base.DependencyAnalysisError):
        """Exception that is raised when a module reports a module as a
        dependency without a path to that module."""

        def __init__(self, name, localpath):
                Exception.__init__(self)
                self.name = name
                self.localpath = localpath

        def __str__(self):
                return _("Could not find the file for %(name)s imported "
                    "in %(localpath)s") % self.__dict__


class PythonMismatchedVersion(base.DependencyAnalysisError):
        """Exception that is raised when a module is installed into a path
        associated with a known version of python (/usr/lib/python2.4 for
        example) but has a different version of python specified in its
        #! line (#!/usr/bin/python2.5 for example)."""

        def __init__(self, installed_version, declared_version, local_file,
            installed_path):
                self.inst_v = installed_version
                self.decl_v = declared_version
                self.lp = local_file
                self.ip = installed_path

        def __str__(self):
                return _("The file to be installed at %(ip)s declares a "
                    "python version of %(decl_v)s.  However, the path suggests "
                    "that the version should be %(inst_v)s.  The text of the "
                    "file can be found at %(lp)s") % self.__dict__


class PythonSubprocessError(base.DependencyAnalysisError):
        """This exception is raised when the subprocess created to analyze the
        module using a different version of python exits with an error code."""

        def __init__(self, rc, cmd, err):
                self.rc = rc
                self.cmd = cmd
                self.err = err

        def __str__(self):
                return _("The command %(cmd)s\nexited with return code %(rc)s "
                    "and this message:\n%(err)s") % self.__dict__


class PythonSubprocessBadLine(base.DependencyAnalysisError):
        """This exception is used when the output from the subprocess does not
        follow the expected format."""

        def __init__(self, cmd, lines):
                self.lines = "\n".join(lines)
                self.cmd = cmd

        def __str__(self):
                return _("The command %(cmd)s produced the following lines "
                    "which cannot be understood:\n%(lines)s") % self.__dict__

class PythonUnspecifiedVersion(base.PublishingDependency):
        """This exception is used when an executable file starts with
        #!/usr/bin/python and is not installed into a location from which its
        python version may be inferred."""

        def __init__(self, local_file, installed_path):
                self.lp = local_file
                self.ip = installed_path

        def __str__(self):
                return _("The file to be installed in %(ip)s does not specify "
                    "a specific version of python either in its installed path "
                    "nor in its text.  Such a file cannot be analyzed for "
                    "dependencies since the version of python it will be used "
                    "with is unknown.  The text of the file is here: %(lp)s.") \
                    % self.__dict__

class PythonDependency(base.PublishingDependency):
        """Class representing the dependency created by importing a module
        in python."""

        def __init__(self, action, base_names, run_paths, pkg_vars, proto_dir):
                base.PublishingDependency.__init__(self, action,
                    base_names, run_paths, pkg_vars, proto_dir, "python")

        def __repr__(self):
                return "PythonDep(%s, %s, %s, %s)" % (self.action,
                    self.base_names, self.run_paths, self.pkg_vars)


py_bin_re = re.compile(r"^\#\!\s*/usr/bin/python(?P<major>\d+)\.(?P<minor>\d+)")
py_lib_re = re.compile(r"^usr/lib/python(?P<major>\d+)\.(?P<minor>\d+)/")

def process_python_dependencies(proto_dir, action, pkg_vars, script_path):
        """Analyze the file delivered by the action for any python dependencies.

        The 'proto_dir' parameter points to the proto area containing the file
        which 'action' uses.

        The 'action' parameter contain the action which delivers the file.

        The 'pkg_vars' parameter contains the variants against which
        the action's package was published.

        The 'script_path' parameter is None of the file is not executable, or
        is the path for the binary which is used to execute the file.
        """

        # There are three conditions which determine whether python dependency
        # analysis is performed on a file with python in its #! line.
        # 1) Is the file executable. (Represented in the table below by X)
        # 2) Is the file installed into a directory which provides information
        #     about what version of python should be used for it.
        #     (Represented by D)
        # 3) Does the first line of the file include a specific version of
        #     python. (Represented by F)
        #
        # Conditions || Perform Analysis
        #  X  D  F   || Y, if F and D disagree, display a warning in the output
        #            ||     and use D to analyze the file.
        #  X  D !F   || Y
        #  X !D  F   || Y
        #  X !D !F   || N, and display a warning in the output.
        # !X  D  F   || Y
        # !X  D !F   || Y
        # !X !D  F   || N
        # !X !D !F   || N
        
        local_file = action.attrs[PD_LOCAL_PATH]
        deps = []
        errs = []
        path_version = None

        dir_major = None
        dir_minor = None
        file_major = None
        file_minor = None
        cur_major = None
        cur_minor = None
        executable = bool(script_path)

        # Version of python to use to do the analysis.
        analysis_major = None
        analysis_minor = None
        
        cur_major, cur_minor = sys.version_info[0:2]
        install_match = py_lib_re.match(action.attrs["path"])
        if install_match:
                dir_major = install_match.group("major")
                dir_minor = install_match.group("minor")

        script_match = None
        if script_path:
                script_match = py_bin_re.match(script_path)
                if script_match:
                        file_major = script_match.group("major")
                        file_minor = script_match.group("minor")

        if executable:
                # Check whether the version of python declared in the #! line
                # of the file and the version of python implied by the directory
                # the file is delivered into match.
                if install_match and script_match and \
                    (file_major != dir_major or file_minor != dir_minor):
                        errs.append(PythonMismatchedVersion(
                            "%s.%s" % (dir_major, dir_minor),
                            "%s.%s" % (file_major, file_minor),
                            local_file, action.attrs["path"]))
                if install_match:
                        analysis_major = dir_major
                        analysis_minor = dir_minor
                elif script_match:
                        analysis_major = file_major
                        analysis_minor = file_minor
                else:
                        # An example of this case is an executable file in
                        # /usr/bin with #!/usr/bin/python as its first line.
                        errs.append(PythonUnspecifiedVersion(
                            local_file, action.attrs["path"]))
        elif install_match:
                analysis_major = dir_major
                analysis_minor = dir_minor

        if analysis_major is None or analysis_minor is None:
                return deps, errs

        analysis_major = int(analysis_major)
        analysis_minor = int(analysis_minor)

        # If the version implied by the directory hierarchy matches the version
        # of python running, use the default analyzer and don't fork and exec.
        if cur_major == analysis_major and cur_minor == analysis_minor:
                mf = modulefinder.DepthLimitedModuleFinder(proto_dir)
                loaded_modules = mf.run_script(local_file)

                for names, dirs in set([
                    (tuple(m.get_file_names()), tuple(m.dirs))
                    for m in loaded_modules
                ]):
                        deps.append(PythonDependency(action, names, dirs,
                            pkg_vars, proto_dir))
                missing, maybe = mf.any_missing_maybe()
                for name in missing:
                        errs.append(PythonModuleMissingPath(name,
                            action.attrs[PD_LOCAL_PATH]))
                return deps, errs

        # If the version implied by the directory hierarchy does not match the
        # version of python running, it's necessary to fork and run the
        # appropriate version of python.
        root_dir = os.path.dirname(__file__)
        exec_file = os.path.join(root_dir,
            "depthlimitedmf%s%s.py" % (analysis_major, analysis_minor))
        cmd = ["python%s.%s" % (analysis_major, analysis_minor), exec_file,
            proto_dir, local_file]
        try:
                sp = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
        except Exception, e:
                return [], [PythonSubprocessError(None, " ".join(cmd), str(e))]
        out, err = sp.communicate()
        if sp.returncode:
                errs.append(PythonSubprocessError(sp.returncode, " ".join(cmd),
                    err))
        bad_lines = []
        for l in out.splitlines():
                l = l.strip()
                if l.startswith("DEP "):
                        try:
                                names, dirs = eval(l[4:])
                        except Exception:
                                bad_lines.append(l)
                        else:
                                deps.append(PythonDependency(action, names,
                                    dirs, pkg_vars, proto_dir))
                elif l.startswith("ERR "):
                        errs.append(PythonModuleMissingPath(l[4:],
                            action.attrs[PD_LOCAL_PATH]))
                else:
                        bad_lines.append(l)
        if bad_lines:
                errs.append(PythonSubprocessBadLine(" ".join(cmd), bad_lines))
        return deps, errs
