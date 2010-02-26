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

"""module describing a directory packaging object

This module contains the DirectoryAction class, which represents a
directory-type packaging object."""

import os
import errno
import stat
import generic
import pkg.portable as portable
import pkg.actions

class DirectoryAction(generic.Action):
        """Class representing a directory-type packaging object."""

        name = "dir"
        attributes = ("mode", "owner", "group", "path")
        key_attr = "path"
        globally_unique = True

        def __init__(self, data=None, **attrs):
                generic.Action.__init__(self, data, **attrs)
                if "path" in self.attrs:
                        self.attrs["path"] = self.attrs["path"].lstrip(
                            os.path.sep)
                        if not self.attrs["path"]:
                                raise pkg.actions.InvalidActionError(
                                    str(self), _("Empty path attribute"))

        def compare(self, other):
                return cmp(self.attrs["path"], other.attrs["path"])

        def directory_references(self):
                return [os.path.normpath(self.attrs["path"])]

        def install(self, pkgplan, orig):
                """Client-side method that installs a directory."""

                mode = None
                try:
                        mode = int(self.attrs.get("mode", None), 8)
                except (TypeError, ValueError):
                        # Mode isn't valid, so let validate raise a more
                        # informative error.
                        self.validate(fmri=pkgplan.destination_fmri)

                owner, group = self.get_fsobj_uid_gid(pkgplan,
                        pkgplan.destination_fmri)
                if orig:
                        try:
                                omode = int(orig.attrs.get("mode", None), 8)
                        except (TypeError, ValueError):
                                # Mode isn't valid, so let validate raise a more
                                # informative error.
                                orig.validate(fmri=pkgplan.origin_fmri)
                        oowner, ogroup = orig.get_fsobj_uid_gid(pkgplan,
                            pkgplan.origin_fmri)

                path = os.path.normpath(os.path.sep.join(
                    (pkgplan.image.get_root(), self.attrs["path"])))

                # XXX Hack!  (See below comment.)
                if not portable.is_admin():
                        mode |= stat.S_IWUSR

                if not orig:
                        try:
                                self.makedirs(path, mode=mode)
                        except OSError, e:
                                if e.errno == errno.EROFS:
                                        # Treat EROFS like EEXIST if both are
                                        # applicable, since we'll end up with
                                        # EROFS instead.
                                        if os.path.isdir(path):
                                                return
                                        raise
                                if e.errno != errno.EEXIST:
                                        raise

                # The downside of chmodding the directory is that as a non-root
                # user, if we set perms u-w, we won't be able to put anything in
                # it, which is often not what we want at install time.  We save
                # the chmods for the postinstall phase, but it's always possible
                # that a later package install will want to place something in
                # this directory and then be unable to.  So perhaps we need to
                # (in all action types) chmod the parent directory to u+w on
                # failure, and chmod it back aftwards.  The trick is to
                # recognize failure due to missing file_dac_write in contrast to
                # other failures.  Or can we require that everyone simply have
                # file_dac_write who wants to use the tools.  Probably not.
                elif mode != omode:
                        os.chmod(path, mode)

                if not orig or oowner != owner or ogroup != group:
                        try:
                                portable.chown(path, owner, group)
                        except OSError, e:
                                if e.errno != errno.EPERM and \
                                    e.errno != errno.ENOSYS:
                                        raise

        def verify(self, img, **args):
                """Returns a tuple of lists of the form (errors, warnings,
                info).  The error list will be empty if the action has been
                correctly installed in the given image."""

                lstat, errors, warnings, info, abort = \
                    self.verify_fsobj_common(img, stat.S_IFDIR)
                return errors, warnings, info

        def remove(self, pkgplan):
                localpath = os.path.normpath(self.attrs["path"])
                path = os.path.normpath(os.path.sep.join(
                    (pkgplan.image.get_root(), localpath)))
                try:
                        os.rmdir(path)
                except OSError, e:
                        if e.errno == errno.ENOENT:
                                pass
                        elif e.errno == errno.EEXIST or \
                                    e.errno == errno.ENOTEMPTY:
                                # cannot remove directory since it's
                                # not empty...
                                pkgplan.image.salvagedir(localpath)
                        elif e.errno != errno.EACCES: # this happens on Windows
                                raise

        def generate_indices(self):
                """Generates the indices needed by the search dictionary.  See
                generic.py for a more detailed explanation."""

                return [
                    ("directory", "basename",
                    os.path.basename(self.attrs["path"].rstrip(os.path.sep)),
                    None),
                    ("directory", "path", os.path.sep + self.attrs["path"],
                    None)
                ]

        def validate(self, fmri=None):
                """Performs additional validation of action attributes that
                for performance or other reasons cannot or should not be done
                during Action object creation.  An ActionError exception (or
                subclass of) will be raised if any attributes are not valid.
                This is primarily intended for use during publication or during
                error handling to provide additional diagonostics.

                'fmri' is an optional package FMRI (object or string) indicating
                what package contained this action."""

                return self.validate_fsobj_common(fmri=fmri)
