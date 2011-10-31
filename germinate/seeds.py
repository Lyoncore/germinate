# -*- coding: UTF-8 -*-
"""Fetch seeds from a URL collection or from bzr."""

# Copyright (c) 2004, 2005, 2006, 2008, 2009, 2011 Canonical Ltd.
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
import tempfile
import atexit
import logging
import urlparse
import urllib2
import shutil
import re
import subprocess
import codecs
import io

import germinate.defaults
from germinate.tsort import topo_sort


__all__ = [
    'SeedError',
    'Seed',
    'SeedStructure',
]


bzr_cache_dir = None

class SeedError(RuntimeError):
    pass


def _cleanup_bzr_cache(directory):
    shutil.rmtree(directory, ignore_errors=True)


class Seed(object):
    """A single seed from a collection."""

    def _open_seed(self, base, branch, name, bzr=False):
        path = os.path.join(base, branch)
        if not path.endswith('/'):
            path += '/'
        if bzr:
            global bzr_cache_dir
            if bzr_cache_dir is None:
                bzr_cache_dir = tempfile.mkdtemp(prefix='germinate-')
                atexit.register(_cleanup_bzr_cache, bzr_cache_dir)
            checkout = os.path.join(bzr_cache_dir, branch)
            if not os.path.isdir(checkout):
                command = ['bzr']
                # https://launchpad.net/products/bzr/+bug/39542
                if path.startswith('http:'):
                    command.append('branch')
                    logging.info("Fetching branch of %s", path)
                else:
                    command.extend(['checkout', '--lightweight'])
                    logging.info("Checking out %s", path)
                command.extend([path, checkout])
                status = subprocess.call(command)
                if status != 0:
                    raise SeedError("Command failed with exit status %d:\n"
                                    "  '%s'" % (status, ' '.join(command)))
            return open(os.path.join(checkout, name))
        else:
            url = urlparse.urljoin(path, name)
            logging.info("Downloading %s", url)
            req = urllib2.Request(url)
            req.add_header('Cache-Control', 'no-cache')
            req.add_header('Pragma', 'no-cache')
            return urllib2.urlopen(req)

    def __init__(self, bases, branches, name, bzr=False):
        if (isinstance(branches, str) or
            isinstance(branches, unicode)):
            branches = [branches]

        self._name = name
        self._base = None
        self._branch = None
        self._file = None

        fd = None
        ssh_host = None
        for base in bases:
            for branch in branches:
                try:
                    fd = self._open_seed(base, branch, name, bzr)
                    self._base = base
                    self._branch = branch
                    break
                except SeedError:
                    ssh_match = re.match(
                        r'bzr\+ssh://(?:[^/]*?@)?(.*?)(?:/|$)', base)
                    if ssh_match:
                        ssh_host = ssh_match.group(1)
                except (OSError, IOError, urllib2.URLError):
                    pass
            if fd is not None:
                break

        if fd is None:
            if bzr:
                logging.warning("Could not open %s from checkout of (any of):",
                                name)
                for base in bases:
                    for branch in branches:
                        logging.warning('  %s' % os.path.join(base, branch))

                if ssh_host is not None:
                    logging.error("Do you need to set your user name on %s?",
                                  ssh_host)
                    logging.error("Try a section such as this in "
                                  "~/.ssh/config:")
                    logging.error("")
                    logging.error("Host %s", ssh_host)
                    logging.error("        User YOUR_USER_NAME")
            else:
                logging.warning("Could not open (any of):")
                for base in bases:
                    for branch in branches:
                        path = os.path.join(base, branch)
                        if not path.endswith('/'):
                            path += '/'
                        logging.warning(
                            '  %s' % urlparse.urljoin(path, name))
            raise SeedError("Could not open %s" % name)

        try:
            self._text = fd.read()
        finally:
            fd.close()

    def open(self):
        self._file = io.BytesIO(self._text)
        return self._file

    def read(self, *args, **kwargs):
        return self._file.read(*args, **kwargs)

    def readline(self, *args, **kwargs):
        return self._file.readline(*args, **kwargs)

    def readlines(self, *args, **kwargs):
        return self._file.readlines(*args, **kwargs)

    def next(self):
        return self._file.next()

    def close(self):
        self._file.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, unused_exc_type, unused_exc_value, unused_exc_tb):
        self.close()

    @property
    def name(self):
        return self._name

    @property
    def base(self):
        return self._base

    @property
    def branch(self):
        return self._branch

    @property
    def text(self):
        return self._text


class CustomSeed(Seed):
    """A seed created from custom input data."""

    def __init__(self, name, entries):
        self._name = name
        self._base = None
        self._branch = None
        self._text = '\n'.join(entries) + '\n'


class SingleSeedStructure(object):
    """A single seed collection structure file.

    The input data is an ordered sequence of lines as follows:

    SEED:[ INHERITED]

    INHERITED is a space-separated list of seeds from which SEED inherits.
    For example, "ship: base desktop" indicates that packages in the "ship"
    seed may depend on packages in the "base" or "desktop" seeds without
    requiring those packages to appear in the "ship" output.  INHERITED may
    be empty.

    The lines should be topologically sorted with respect to inheritance,
    with inherited-from seeds at the start.

    Any line as follows:

    include BRANCH

    causes another seed branch to be included.  Seed names will be resolved
    in included branches if they cannot be found in the current branch.

    This is for internal use; applications should use the SeedStructure
    class instead.
    """

    def __init__(self, branch, f):
        """Parse a single seed structure file."""

        self.seed_order = []
        self.inherit = {}
        self.branches = [branch]
        self.lines = []
        self.features = set()

        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                continue
            words = line.split()
            if words[0].endswith(':'):
                seed = words[0][:-1]
                if '/' in seed:
                    raise SeedError(
                        "seed name '%s' may not contain '/'" % seed)
                self.seed_order.append(seed)
                self.inherit[seed] = list(words[1:])
                self.lines.append(line)
            elif words[0] == 'include':
                self.branches.extend(words[1:])
            elif words[0] == 'feature':
                self.features.update(words[1:])
            else:
                logging.error("Unparseable seed structure entry: %s", line)


class SeedStructure(object):
    """The full structure of a seed collection.

    This deals with acquiring the seed structure files and recursively
    acquiring any seed structure files it includes.
    """

    def __init__(self, branch, seed_bases=germinate.defaults.seeds, bzr=False):
        self._seed_bases = seed_bases
        self.branch = branch
        self._bzr = bzr
        self.features = set()
        self.seed_order, self.inherit, branches, self.lines = \
            self._parse(self.branch, set())
        self.seeds = {}
        for seed in self.seed_order:
            self.seeds[seed] = Seed(seed_bases, branches, seed, bzr=bzr)
        self._expand_inheritance()

    def _parse(self, branch, got_branches):
        all_seed_order = []
        all_inherit = {}
        all_branches = []
        all_structure = []

        # Fetch this one
        with Seed(self._seed_bases, branch, "STRUCTURE", self._bzr) as seed:
            structure = SingleSeedStructure(branch, seed)
        got_branches.add(branch)

        # Recursively expand included branches
        for child_branch in structure.branches:
            if child_branch in got_branches:
                continue
            (child_seed_order, child_inherit, child_branches,
             child_structure) = self._parse(child_branch, got_branches)
            all_seed_order.extend(child_seed_order)
            all_inherit.update(child_inherit)
            for grandchild_branch in child_branches:
                if grandchild_branch not in all_branches:
                    all_branches.append(grandchild_branch)
            for child_structure_line in child_structure:
                child_structure_name = child_structure_line.split()[0][:-1]
                for i in range(len(all_structure)):
                    if all_structure[i].split()[0][:-1] == child_structure_name:
                        del all_structure[i]
                        break
                all_structure.append(child_structure_line)

        # Attach the main branch's data to the end
        all_seed_order.extend(structure.seed_order)
        all_inherit.update(structure.inherit)
        for child_branch in structure.branches:
            if child_branch not in all_branches:
                all_branches.append(child_branch)
        for structure_line in structure.lines:
            structure_name = structure_line.split()[0][:-1]
            for i in range(len(all_structure)):
                if all_structure[i].split()[0][:-1] == structure_name:
                    del all_structure[i]
                    break
            all_structure.append(structure_line)
        self.features.update(structure.features)

        # We generally want to process branches in reverse order, so that
        # later branches can override seeds from earlier branches
        all_branches.reverse()

        return all_seed_order, all_inherit, all_branches, all_structure

    def _expand_inheritance(self):
        """Expand out incomplete inheritance lists"""
        self.original_inherit = dict(self.inherit)

        self.names = topo_sort(self.inherit)
        for name in self.names:
            seen = set()
            new_inherit = []
            for inheritee in self.inherit[name]:
                for expanded in self.inherit[inheritee]:
                    if expanded not in seen:
                        new_inherit.append(expanded)
                        seen.add(expanded)
                if inheritee not in seen:
                    new_inherit.append(inheritee)
                    seen.add(inheritee)
            self.inherit[name] = new_inherit

    def limit(self, seeds):
        """Restrict the seeds we care about to this list."""
        self.names = []
        for name in seeds:
            for inherit in self.inherit[name]:
                if inherit not in self.names:
                    self.names.append(inherit)
            if name not in self.names:
                self.names.append(name)

    def add(self, name, entries, parent):
        self.names.append(name)
        self.inherit[name] = self.inherit[parent] + [parent]
        self.seeds[name] = CustomSeed(name, entries)

    def add_extra(self):
        """Add a special "extra" seed."""
        if "extra" in self.names:
            return
        self.names.append("extra")
        self.inherit["extra"] = list(self.names)

    def inner_seeds(self, seedname):
        """Return this seed and the seeds from which it inherits."""
        innerseeds = list(self.inherit[seedname])
        innerseeds.append(seedname)
        return innerseeds

    def strictly_outer_seeds(self, seedname):
        """Return the seeds that inherit from this seed."""
        outerseeds = []
        for seed in self.names:
            if seedname in self.inherit[seed]:
                outerseeds.append(seed)
        return outerseeds

    def outer_seeds(self, seedname):
        """Return this seed and the seeds that inherit from it."""
        outerseeds = [seedname]
        outerseeds.extend(self.strictly_outer_seeds(seedname))
        return outerseeds

    def write(self, filename):
        with open(filename, "w") as f:
            for line in self.lines:
                print >>f, line

    def write_dot(self, filename):
        """Write a dot file representing this structure."""

        # Initialize dot document
        with codecs.open(filename, "w", "utf8", "replace") as dotfile:
            print >>dotfile, "digraph structure {"
            print >>dotfile, "    node [color=lightblue2, style=filled];"

            for seed in self.seed_order:
                if seed not in self.original_inherit:
                    continue
                for inherit in self.original_inherit[seed]:
                    print >>dotfile, "    \"%s\" -> \"%s\";" % (inherit, seed)

            print >>dotfile, "}"

    def write_seed_text(self, filename, seedname):
        with open(filename, "w") as f:
            with self.seeds[seedname] as seed:
                for line in seed:
                    print >>f, line.rstrip('\n')
