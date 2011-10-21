# -*- coding: UTF-8 -*-
"""Fetch package lists from a Debian-format archive as apt tag files."""

# Copyright (c) 2004, 2005, 2006, 2007, 2008, 2009, 2010, 2011
#               Canonical Ltd.
#
# Germinate is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2, or (at your option) any
# later version.
#
# Germinate is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Germinate; see the file COPYING.  If not, write to the Free
# Software Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA
# 02110-1301, USA.

import os
import urllib
import urllib2
import tempfile
import shutil

class TagFile:
    def __init__(self, mirrors, source_mirrors=None, installer_packages=True):
        self.mirrors = mirrors
        self.installer_packages = installer_packages
        if source_mirrors:
            self.source_mirrors = source_mirrors
        else:
            self.source_mirrors = mirrors

    def open_tag_files(self, mirrors, dirname, tagfile_type,
                       dist, component, ftppath):
        def open_tag_file(mirror, suffix):
            """Download an apt tag file if needed, then open it."""
            url = (mirror + "dists/" + dist + "/" + component + "/" + ftppath +
                   suffix)
            req = urllib2.Request(url)
            filename = None

            if req.get_type() != "file":
                filename = "%s_%s_%s_%s" % (urllib.quote(mirror, safe=""), dist,
                                            component, tagfile_type)
            else:
                # Make a more or less dummy filename for local URLs.
                filename = os.path.split(req.get_selector())[0].replace(os.sep, "_")

            fullname = os.path.join(dirname, filename)
            if req.get_type() == "file":
                # Always refresh.  TODO: we should use If-Modified-Since for
                # remote HTTP tag files.
                try:
                    os.unlink(fullname)
                except OSError:
                    pass
            if not os.path.exists(fullname):
                print "Downloading", req.get_full_url(), "file ..."

                compressed = os.path.join(dirname, filename + suffix)
                try:
                    url_f = urllib2.urlopen(req)
                    try:
                        with open(compressed, "w") as compressed_f:
                            compressed_f.write(url_f.read())
                    finally:
                        url_f.close()

                    # apt_pkg is weird and won't accept GzipFile
                    if suffix:
                        print "Decompressing", req.get_full_url(), "file ..."

                        if suffix == ".gz":
                            import gzip
                            compressed_f = gzip.GzipFile(compressed)
                        elif suffix == ".bz2":
                            import bz2
                            compressed_f = bz2.BZ2File(compressed)
                        else:
                            raise RuntimeError("Unknown suffix '%s'" % suffix)

                        # This can be simplified once we can require Python
                        # 2.7, where gzip.GzipFile and bz2.BZ2File are
                        # context managers.
                        try:
                            with open(fullname, "w") as f:
                                print >>f, compressed_f.read(),
                                f.flush()
                        finally:
                            compressed_f.close()
                finally:
                    if suffix:
                        try:
                            os.unlink(compressed)
                        except OSError:
                            pass

            return open(fullname, "r")

        tag_files = []
        for mirror in mirrors:
            tag_file = None
            for suffix in (".bz2", ".gz", ""):
                try:
                    tag_file = open_tag_file(mirror, suffix)
                    tag_files.append(tag_file)
                    break
                except (IOError, OSError):
                    pass
        if len(tag_files) == 0:
            raise IOError, "no %s files found" % tagfile_type
        return tag_files

    def feed(self, g, dists, components, arch, cleanup=False):
        if cleanup:
            dirname = tempfile.mkdtemp(prefix="germinate-")
        else:
            dirname = '.'

        for dist in dists:
            for component in components:
                packages = self.open_tag_files(
                    self.mirrors, dirname, "Packages", dist, component,
                    "binary-" + arch + "/Packages")
                try:
                    g.parsePackages(packages, "deb")
                finally:
                    for tag_file in packages:
                        tag_file.close()

                sources = self.open_tag_files(
                    self.source_mirrors, dirname, "Sources", dist, component,
                    "source/Sources")
                try:
                    g.parseSources(sources)
                finally:
                    for tag_file in packages:
                        tag_file.close()

                instpackages = ""
                if self.installer_packages:
                    try:
                        instpackages = self.open_tag_files(
                            self.mirrors, dirname, "InstallerPackages", dist, component,
                            "debian-installer/binary-" + arch + "/Packages")
                    except IOError:
                        # can live without these
                        print "Missing installer Packages file for", component, \
                              "(ignoring)"
                    else:
                        try:
                            g.parsePackages(instpackages, "udeb")
                        finally:
                            for tag_file in instpackages:
                                tag_file.close()

        if cleanup:
            shutil.rmtree(dirname)
