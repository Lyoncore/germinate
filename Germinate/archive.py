# -*- coding: UTF-8 -*-
"""An abstract representation of an archive for use by Germinate."""

# Copyright (c) 2011 Canonical Ltd.
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


class IndexType:
    """Types of archive index files."""
    PACKAGES = 1
    SOURCES = 2
    INSTALLER_PACKAGES = 3


class Archive:
    def sections(self):
        """Yield a sequence of the index sections found in this archive.

        A section is an entry in an index file corresponding to a single binary
        or source package.

        Each yielded value should be an (IndexType, section) pair, where
        section is a dictionary mapping control file keys to their values.
        """
        raise NotImplementedError
