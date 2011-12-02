# -*- coding: UTF-8 -*-
"""Expand seeds into dependency-closed lists of packages."""

# Copyright (c) 2004, 2005, 2006, 2007, 2008, 2009, 2011 Canonical Ltd.
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

import sys
import re
import fnmatch
import logging
import codecs
import collections

import apt_pkg

from germinate.archive import IndexType
from germinate.seeds import SeedStructure

# TODO: would be much more elegant to reduce our recursion depth!
sys.setrecursionlimit(2000)


__all__ = [
    'Germinator',
]


class GerminatedSeed(object):
    def __init__(self, germinator, name, structure):
        self._germinator = germinator
        self._name = name
        self._structure = structure
        self._entries = []
        self._features = set()
        self._recommends_entries = []
        self._close_seeds = set()
        self._depends = set()
        self._build_depends = set()
        self._sourcepkgs = set()
        self._build_sourcepkgs = set()
        self._build = set()
        self._not_build = set()
        self._build_srcs = set()
        self._not_build_srcs = set()
        self._reasons = {}
        self._blacklist = set()
        self._blacklist_seen = False
        self._di_kernel_versions = set()
        self._includes = {}
        self._excludes = {}
        self._grown = False

    """Return a copy of this seed attached to a different structure."""
    def copy(self, structure):
        assert self._grown

        new = GerminatedSeed(self._germinator, self._name, structure)
        # We deliberately don't take copies of anything; this seed has been
        # grown and thus should not be modified further, and deep copies
        # would take up substantial amounts of memory.
        new._entries = self._entries
        new._features = self._features
        new._recommends_entries = self._recommends_entries
        new._close_seeds = self._close_seeds
        new._depends = self._depends
        new._build_depends = self._build_depends
        new._sourcepkgs = self._sourcepkgs
        new._build_sourcepkgs = self._build_sourcepkgs
        new._build = self._build
        new._not_build = self._not_build
        new._build_srcs = self._build_srcs
        new._not_build_srcs = self._not_build_srcs
        new._reasons = self._reasons
        new._blacklist = self._blacklist
        new._blacklist_seen = False
        new._di_kernel_versions = self._di_kernel_versions
        new._includes = self._includes
        new._excludes = self._excludes
        new._grown = True

        return new

    @property
    def name(self):
        return self._name

    @property
    def structure(self):
        return self._structure

    def __str__(self):
        return self._name

    @property
    def entries(self):
        return list(self._entries)

    @property
    def recommends_entries(self):
        return list(self._recommends_entries)

    @property
    def depends(self):
        return set(self._depends)

    @property
    def build_depends(self):
        return set(self._build_depends)

    def __cmp__(self, other):
        if isinstance(other, GerminatedSeed):
            left_inherit = self.structure.inner_seeds(self.name)
            right_inherit = other.structure.inner_seeds(other.name)
            ret = cmp(len(left_inherit), len(right_inherit))
            if ret != 0:
                return ret
            left_branch = self.structure.branch
            right_branch = other.structure.branch
            for left, right in zip(left_inherit, right_inherit):
                ret = cmp(left, right)
                if ret != 0:
                    return ret
                left_seedname = self._germinator._make_seed_name(
                    left_branch, left)
                right_seedname = other._germinator._make_seed_name(
                    right_branch, right)
                # Ignore KeyError in the following; if seeds haven't been
                # planted yet, they can't have seen blacklist entries from
                # outer seeds.
                try:
                    left_seed = self._germinator._seeds[left_seedname]
                    if left_seed._blacklist_seen:
                        return -1
                except KeyError:
                    pass
                try:
                    right_seed = other._germinator._seeds[right_seedname]
                    if right_seed._blacklist_seen:
                        return -1
                except KeyError:
                    pass
            if self._blacklist_seen or other._blacklist_seen:
                return -1
            return 0
        else:
            return cmp(self.name, other)

class GerminatedSeedStructure(object):
    def __init__(self, structure):
        self._structure = structure

        # TODO: move to collections.OrderedDict with 2.7
        self._seednames = []

        self._pkgprovides = {}

        self._all = set()
        self._all_srcs = set()
        self._all_reasons = {}

        self._blacklist = {}
        self._blacklisted = set()

class GerminatorOutput(collections.MutableMapping, object):
    def __init__(self):
        self._dict = {}

    def __iter__(self):
        return iter(self._dict)

    def __len__(self):
        return len(self._dict)

    def __getitem__(self, key):
        if isinstance(key, SeedStructure):
            return self._dict[key.branch]
        else:
            return self._dict[key]

    def __setitem__(self, key, value):
        if isinstance(key, SeedStructure):
            self._dict[key.branch] = value
        else:
            self._dict[key] = value

    def __delitem__(self, key):
        if isinstance(key, SeedStructure):
            del self._dict[key.branch]
        else:
            del self._dict[key]

class Germinator(object):
    PROGRESS = 15

    # Initialisation.
    # ---------------

    def __init__(self, arch):
        self._arch = arch
        apt_pkg.config.set("APT::Architecture", self._arch)

        # Global hints file.
        self._hints = {}

        # Parsed representation of the archive.
        self._packages = {}
        self._packagetype = {}
        self._provides = {}
        self._sources = {}

        # All the seeds we know about, regardless of seed structure.
        self._seeds = {}

        # Results of germination for each seed structure.
        self._output = GerminatorOutput()

    # Parsing.
    # --------

    def parse_hints(self, f):
        """Parse a hints file."""
        for line in f:
            if line.startswith("#") or not len(line.rstrip()): continue

            words = line.rstrip().split(None)
            if len(words) != 2:
                continue

            self._hints[words[1]] = words[0]
        f.close()

    def _parse_package(self, section, pkgtype):
        """Parse a section from a Packages file."""
        pkg = section["Package"]
        ver = section["Version"]

        # If we have already seen an equal or newer version of this package,
        # then skip this section.
        if pkg in self._packages:
            last_ver = self._packages[pkg]["Version"]
            if apt_pkg.version_compare(last_ver, ver) >= 0:
                return

        self._packages[pkg] = {}
        self._packagetype[pkg] = pkgtype

        self._packages[pkg]["Section"] = \
            section.get("Section", "").split('/')[-1]

        self._packages[pkg]["Version"] = ver

        self._packages[pkg]["Maintainer"] = \
            unicode(section.get("Maintainer", ""), "utf8", "replace")

        self._packages[pkg]["Essential"] = section.get("Essential", "")

        for field in "Pre-Depends", "Depends", "Recommends", "Suggests":
            value = section.get(field, "")
            self._packages[pkg][field] = apt_pkg.parse_depends(value)

        for field in "Size", "Installed-Size":
            value = section.get(field, "0")
            self._packages[pkg][field] = int(value)

        src = section.get("Source", pkg)
        idx = src.find("(")
        if idx != -1:
            src = src[:idx].strip()
        self._packages[pkg]["Source"] = src

        provides = apt_pkg.parse_depends(section.get("Provides", ""))
        for prov in provides:
            if prov[0][0] not in self._provides:
                self._provides[prov[0][0]] = []
                if prov[0][0] in self._packages:
                    self._provides[prov[0][0]].append(prov[0][0])
            self._provides[prov[0][0]].append(pkg)
        self._packages[pkg]["Provides"] = provides

        if pkg in self._provides:
            self._provides[pkg].append(pkg)

        self._packages[pkg]["Kernel-Version"] = section.get("Kernel-Version", "")

    def _parse_source(self, section):
        """Parse a section from a Sources file."""
        src = section["Package"]
        ver = section["Version"]

        # If we have already seen an equal or newer version of this source,
        # then skip this section.
        if src in self._sources:
            last_ver = self._sources[src]["Version"]
            if apt_pkg.version_compare(last_ver, ver) >= 0:
                return

        self._sources[src] = {}

        self._sources[src]["Maintainer"] = \
            unicode(section.get("Maintainer", ""), "utf8", "replace")
        self._sources[src]["Version"] = ver

        for field in "Build-Depends", "Build-Depends-Indep":
            value = section.get(field, "")
            self._sources[src][field] = apt_pkg.parse_src_depends(value)

        binaries = apt_pkg.parse_depends(section.get("Binary", src))
        self._sources[src]["Binaries"] = [ b[0][0] for b in binaries ]

    def parse_archive(self, archive):
        for indextype, section in archive.sections():
            if indextype == IndexType.PACKAGES:
                self._parse_package(section, "deb")
            elif indextype == IndexType.SOURCES:
                self._parse_source(section)
            elif indextype == IndexType.INSTALLER_PACKAGES:
                self._parse_package(section, "udeb")
            else:
                raise ValueError("Unknown index type %d" % indextype)

    def parse_blacklist(self, structure, f):
        """Parse a blacklist file, used to indicate unwanted packages"""

        output = self._output[structure]
        name = ''

        for line in f:
            line = line.strip()
            if line.startswith('# blacklist: '):
                name = line[13:]
            elif not line or line.startswith('#'):
                continue
            else:
                output._blacklist[line] = name
        f.close()

    # Seed structure handling.  We need to wrap a few methods.
    # --------------------------------------------------------

    def _inner_seeds(self, seed):
        branch = seed.structure.branch
        return [self._seeds[self._make_seed_name(branch, seedname)]
                for seedname in seed.structure.inner_seeds(seed.name)]

    def _strictly_outer_seeds(self, seed):
        branch = seed.structure.branch
        return [self._seeds[self._make_seed_name(branch, seedname)]
                for seedname in
                    seed.structure.strictly_outer_seeds(seed.name)]

    def _outer_seeds(self, seed):
        branch = seed.structure.branch
        return [self._seeds[self._make_seed_name(branch, seedname)]
                for seedname in seed.structure.outer_seeds(seed.name)]

    def _supported(self, seed):
        try:
            return self.get_seed(seed.structure, seed.structure.supported)
        except KeyError:
            return None

    # The main germination algorithm.
    # -------------------------------

    def _filter_packages(self, packages, pattern):
        """Filter a list of packages, returning those that match the given
        pattern. The pattern may either be a shell-style glob, or (if
        surrounded by slashes) an extended regular expression."""

        if pattern.startswith('/') and pattern.endswith('/'):
            patternre = re.compile(pattern[1:-1])
            filtered = [p for p in packages if patternre.search(p) is not None]
        elif '*' in pattern or '?' in pattern or '[' in pattern:
            filtered = fnmatch.filter(packages, pattern)
        else:
            # optimisation for common case
            if pattern in packages:
                filtered = [pattern]
            else:
                filtered = []
        return sorted(filtered)

    def _substitute_seed_vars(self, substvars, pkg):
        """Process substitution variables. These look like ${name} (e.g.
        "kernel-image-${Kernel-Version}"). The name is case-insensitive.
        Substitution variables are set with a line that looks like
        " * name: value [value ...]", values being whitespace-separated.

        A package containing substitution variables will be expanded into
        one package for each possible combination of values of those
        variables."""

        pieces = re.split(r'(\${.*?})', pkg)
        substituted = [[]]

        for piece in pieces:
            if piece.startswith("${") and piece.endswith("}"):
                name = piece[2:-1].lower()
                if name in substvars:
                    # Duplicate substituted once for each available substvar
                    # expansion.
                    newsubst = []
                    for value in substvars[name]:
                        for substpieces in substituted:
                            newsubstpieces = list(substpieces)
                            newsubstpieces.append(value)
                            newsubst.append(newsubstpieces)
                    substituted = newsubst
                else:
                    logging.error("Undefined seed substvar: %s", name)
            else:
                for substpieces in substituted:
                    substpieces.append(piece)

        substpkgs = []
        for substpieces in substituted:
            substpkgs.append("".join(substpieces))
        return substpkgs

    def _already_seeded(self, seed, pkg):
        """Has pkg already been seeded in this seed or in one from
        which we inherit?"""

        for innerseed in self._inner_seeds(seed):
            if (pkg in innerseed._entries or
                pkg in innerseed._recommends_entries):
                return True

        return False

    def _make_seed_name(self, branch, seedname):
        return '%s/%s' % (branch, seedname)

    def _plant_seed(self, structure, seedname, raw_seed):
        """Add a seed."""
        seed = GerminatedSeed(self, seedname, structure)
        full_seedname = self._make_seed_name(structure.branch, seedname)
        for existing in self._seeds.itervalues():
            if seed == existing:
                logging.info("Already planted seed %s" % seed)
                self._seeds[full_seedname] = existing.copy(structure)
                self._output[structure]._seednames.append(seedname)
                return
        self._seeds[full_seedname] = seed
        self._output[structure]._seednames.append(seedname)

        seedpkgs = []
        seedrecommends = []
        substvars = {}

        for line in raw_seed:
            if line.lower().startswith('task-seeds:'):
                seed._close_seeds.update(line[11:].strip().split())
                continue

            if not line.startswith(" * "):
                continue

            pkg = line[3:].strip()
            if pkg.find("#") != -1:
                pkg = pkg[:pkg.find("#")]

            colon = pkg.find(":")
            if colon != -1:
                # Special header
                name = pkg[:colon]
                name = name.lower()
                value = pkg[colon + 1:]
                values = value.strip().split()
                if name == "kernel-version":
                    # Allows us to pick the right modules later
                    logging.warning("Allowing d-i kernel versions: %s", values)
                    seed._di_kernel_versions.update(values)
                elif name == "feature":
                    logging.warning("Setting features {%s} for seed %s",
                                    ', '.join(values), seed)
                    seed._features.update(values)
                elif name.endswith("-include"):
                    included_seed = name[:-8]
                    if (included_seed not in self._seeds and
                        included_seed != "extra"):
                        logging.error("Cannot include packages from unknown "
                                      "seed: %s", included_seed)
                    else:
                        logging.warning("Including packages from %s: %s",
                                        included_seed, values)
                        if included_seed not in seed._includes:
                            seed._includes[included_seed] = []
                        seed._includes[included_seed].extend(values)
                elif name.endswith("-exclude"):
                    excluded_seed = name[:-8]
                    if (excluded_seed not in self._seeds and
                        excluded_seed != "extra"):
                        logging.error("Cannot exclude packages from unknown "
                                      "seed: %s", excluded_seed)
                    else:
                        logging.warning("Excluding packages from %s: %s",
                                        excluded_seed, values)
                        if excluded_seed not in seed._excludes:
                            seed._excludes[excluded_seed] = []
                        seed._excludes[excluded_seed].extend(values)
                substvars[name] = values
                continue

            pkg = pkg.strip()
            if pkg.endswith("]"):
                archspec = []
                startarchspec = pkg.rfind("[")
                if startarchspec != -1:
                    archspec = pkg[startarchspec + 1:-1].split()
                    pkg = pkg[:startarchspec - 1]
                    posarch = [x for x in archspec if not x.startswith('!')]
                    negarch = [x[1:] for x in archspec if x.startswith('!')]
                    if self._arch in negarch:
                        continue
                    if posarch and self._arch not in posarch:
                        continue

            pkg = pkg.split()[0]

            # a leading ! indicates a per-seed blacklist; never include this
            # package in the given seed or any of its inner seeds, no matter
            # what
            if pkg.startswith('!'):
                pkg = pkg[1:]
                is_blacklist = True
            else:
                is_blacklist = False

            # a (pkgname) indicates that this is a recommend
            # and not a depends
            if pkg.startswith('(') and pkg.endswith(')'):
                pkg = pkg[1:-1]
                pkgs =  self._filter_packages(self._packages, pkg)
                if not pkgs:
                    pkgs = [pkg] # virtual or expanded; check again later
                for pkg in pkgs:
                    seedrecommends.extend(self._substitute_seed_vars(
                        substvars, pkg))

            if pkg.startswith('%'):
                pkg = pkg[1:]
                if pkg in self._sources:
                    pkgs = [p for p in self._sources[pkg]["Binaries"]
                              if p in self._packages]
                else:
                    logging.warning("Unknown source package: %s", pkg)
                    pkgs = []
            else:
                pkgs = self._filter_packages(self._packages, pkg)
                if not pkgs:
                    pkgs = [pkg] # virtual or expanded; check again later

            if is_blacklist:
                for pkg in pkgs:
                    logging.info("Blacklisting %s from %s", pkg, seed)
                    seed._blacklist.update(self._substitute_seed_vars(
                        substvars, pkg))
            else:
                for pkg in pkgs:
                    seedpkgs.extend(self._substitute_seed_vars(substvars, pkg))

        for pkg in seedpkgs:
            if pkg in self._hints and self._hints[pkg] != seed.name:
                logging.warning("Taking the hint: %s", pkg)
                continue

            if pkg in self._packages:
                # Ordinary package
                if self._already_seeded(seed, pkg):
                    logging.warning("Duplicated seed: %s", pkg)
                elif self._is_pruned(seed, pkg):
                    logging.warning("Pruned %s from %s", pkg, seed)
                else:
                    if pkg in seedrecommends:
                        seed._recommends_entries.append(pkg)
                    else:
                        seed._entries.append(pkg)
            elif pkg in self._provides:
                # Virtual package, include everything
                msg = "Virtual %s package: %s" % (seed, pkg)
                for vpkg in self._provides[pkg]:
                    if self._already_seeded(seed, vpkg):
                        pass
                    elif self._is_pruned(seed, vpkg):
                        pass
                    else:
                        msg += "\n  - %s" % vpkg
                        if pkg in seedrecommends:
                            seed._recommends_entries.append(vpkg)
                        else:
                            seed._entries.append(vpkg)
                logging.info("%s", msg)

            else:
                # No idea
                logging.error("Unknown %s package: %s", seed, pkg)

        for pkg in self._hints:
            if (self._hints[pkg] == seed.name and
                not self._already_seeded(seed, pkg)):
                if pkg in self._packages:
                    if pkg in seedrecommends:
                        seed._recommends_entries.append(pkg)
                    else:
                        seed._entries.append(pkg)
                else:
                    logging.error("Unknown hinted package: %s", pkg)

    def plant_seeds(self, structure, seeds=None):
        """Add all seeds found in a seed structure."""
        if structure not in self._output:
            self._output[structure] = GerminatedSeedStructure(structure)

        if seeds is not None:
            structure.limit(seeds)

        for name in structure.names:
            with structure[name] as seed:
                self._plant_seed(structure, name, seed)

    def _is_pruned(self, seed, pkg):
        """Return True if pkg is inapplicable in seed for some reason, such
           as being for the wrong d-i kernel version."""
        if not seed._di_kernel_versions:
            return False
        kernver = self._packages[pkg]["Kernel-Version"]
        if kernver != "" and kernver not in seed._di_kernel_versions:
            return True
        return False

    def _weed_blacklist(self, pkgs, seed, build_tree, why):
        """Weed out blacklisted seed entries from a list."""
        white = []
        if build_tree:
            outerseeds = [self._supported(seed)]
        else:
            outerseeds = self._outer_seeds(seed)
        for pkg in pkgs:
            for outerseed in outerseeds:
                if outerseed is not None and pkg in outerseed._blacklist:
                    logging.error("Package %s blacklisted in %s but seeded in "
                                  "%s (%s)", pkg, outerseed, seed, why)
                    seed._blacklist_seen = True
                    break
            else:
                white.append(pkg)
        return white

    def grow(self, structure):
        """Grow the seeds."""
        output = self._output[structure]

        for seedname in output._seednames:
            seed = self.get_seed(structure, seedname)
            if seed._grown:
                logging.info("Already grown seed %s" % seed)
                continue

            logging.log(self.PROGRESS, "Resolving %s dependencies ...", seed)
            if seed.structure.branch is None:
                why = "%s seed" % seed.name.title()
            else:
                why = ("%s %s seed" %
                       (seed.structure.branch.title(), seed.name))

            # Check for blacklisted seed entries.
            seed._entries = self._weed_blacklist(
                seed._entries, seed, False, why)
            seed._recommends_entries = self._weed_blacklist(
                seed._recommends_entries, seed, False, why)

            # Note that seedrecommends are not processed with
            # recommends=True; that is reserved for Recommends of packages,
            # not packages recommended by the seed. Changing this results in
            # less helpful output when a package is recommended by an inner
            # seed and required by an outer seed.
            for pkg in seed._entries + seed._recommends_entries:
                self._add_package(seed, pkg, why)

            for rescue_seedname in output._seednames:
                self._rescue_includes(structure, seed.name, rescue_seedname,
                                      build_tree=False)
                if rescue_seedname == seed.name:
                    # only rescue from seeds up to and including the current
                    # seed; later ones have not been grown
                    break
            self._rescue_includes(structure, seed.name, "extra",
                                  build_tree=False)

            seed._grown = True

        try:
            supported = self.get_seed(structure, structure.supported)
        except KeyError:
            supported = None
        if supported is not None:
            self._rescue_includes(structure, supported.name, "extra",
                                  build_tree=True)

    def add_extras(self, structure):
        """Add packages generated by the sources but not in any seed."""
        output = self._output[structure]

        structure.add_extra()
        seed = GerminatedSeed(self, "extra", structure)
        self._seeds[self._make_seed_name(structure.branch, "extra")] = seed
        output._seednames.append("extra")

        logging.log(self.PROGRESS, "Identifying extras ...")
        found = True
        while found:
            found = False
            sorted_srcs = sorted(output._all_srcs)
            for srcname in sorted_srcs:
                for pkg in self._sources[srcname]["Binaries"]:
                    if pkg not in self._packages:
                        continue
                    if self._packages[pkg]["Source"] != srcname:
                        continue
                    if pkg in output._all:
                        continue

                    if pkg in self._hints and self._hints[pkg] != "extra":
                        logging.warning("Taking the hint: %s", pkg)
                        continue

                    seed._entries.append(pkg)
                    self._add_package(seed, pkg, "Generated by " + srcname,
                                      second_class=True)
                    found = True

    def _allowed_dependency(self, pkg, depend, seed, build_depend):
        """Is pkg allowed to satisfy a (build-)dependency using depend
           within seed? Note that depend must be a real package.

           If seed is None, check whether the (build-)dependency is allowed
           within any seed."""
        if depend not in self._packages:
            logging.warning("_allowed_dependency called with virtual package "
                            "%s", depend)
            return False
        if seed is not None and self._is_pruned(seed, depend):
            return False
        if build_depend:
            if self._packagetype[depend] == "deb":
                return True
            else:
                return False
        else:
            if self._packagetype[pkg] == self._packagetype[depend]:
                return True
            else:
                return False

    def _allowed_virtual_dependency(self, pkg, deptype):
        """May pkg's dependency relationship type deptype be satisfied by a
           virtual package? (Versioned dependencies may not be satisfied by
           virtual packages, unless pkg is a udeb.)"""
        if pkg in self._packagetype and self._packagetype[pkg] == "udeb":
            return True
        elif deptype == "":
            return True
        else:
            return False

    def _check_versioned_dependency(self, depname, depver, deptype):
        """Can this versioned dependency be satisfied with the current set
           of packages?"""
        if depname not in self._packages:
            return False
        if deptype == "":
            return True

        ver = self._packages[depname]["Version"]
        compare = apt_pkg.version_compare(ver, depver)
        if deptype == "<=":
            return compare <= 0
        elif deptype == ">=":
            return compare >= 0
        elif deptype == "<":
            return compare < 0
        elif deptype == ">":
            return compare > 0
        elif deptype == "=":
            return compare == 0
        elif deptype == "!=":
            return compare != 0
        else:
            logging.error("Unknown dependency comparator: %s" % deptype)
            return False

    def _unparse_dependency(self, depname, depver, deptype):
        """Return a string representation of a dependency."""
        if deptype == "":
            return depname
        else:
            return "%s (%s %s)" % (depname, deptype, depver)

    def _follow_recommends(self, seed=None):
        """Should we follow Recommends for this seed?"""
        if seed is not None:
            if "follow-recommends" in seed._features:
                return True
            if "no-follow-recommends" in seed._features:
                return False
        if "follow-recommends" in seed.structure.features:
            return True
        return False

    def _add_reverse(self, pkg, field, rdep):
        """Add a reverse dependency entry."""
        if "Reverse-Depends" not in self._packages[pkg]:
            self._packages[pkg]["Reverse-Depends"] = {}
        if field not in self._packages[pkg]["Reverse-Depends"]:
            self._packages[pkg]["Reverse-Depends"][field] = []

        self._packages[pkg]["Reverse-Depends"][field].append(rdep)

    def reverse_depends(self, structure):
        """Calculate the reverse dependency relationships."""
        output = self._output[structure]

        for pkg in output._all:
            fields = ["Pre-Depends", "Depends"]
            if (self._follow_recommends() or
                self._packages[pkg]["Section"] == "metapackages"):
                fields.append("Recommends")
            for field in fields:
                for deplist in self._packages[pkg][field]:
                    for dep in deplist:
                        if dep[0] in output._all and \
                           self._allowed_dependency(pkg, dep[0], None, False):
                            self._add_reverse(dep[0], field, pkg)

        for src in output._all_srcs:
            for field in "Build-Depends", "Build-Depends-Indep":
                for deplist in self._sources[src][field]:
                    for dep in deplist:
                        if dep[0] in output._all and \
                           self._allowed_dependency(src, dep[0], None, True):
                            self._add_reverse(dep[0], field, src)

        for pkg in output._all:
            if "Reverse-Depends" not in self._packages[pkg]:
                continue

            fields = ["Pre-Depends", "Depends"]
            if (self._follow_recommends() or
                self._packages[pkg]["Section"] == "metapackages"):
                fields.append("Recommends")
            fields.extend(["Build-Depends", "Build-Depends-Indep"])
            for field in fields:
                if field not in self._packages[pkg]["Reverse-Depends"]:
                    continue

                self._packages[pkg]["Reverse-Depends"][field].sort()

    def _already_satisfied(self, seed, pkg, depend, build_depend=False, with_build=False):
        """Work out whether a dependency has already been satisfied."""
        (depname, depver, deptype) = depend
        if self._allowed_virtual_dependency(pkg, deptype) and depname in self._provides:
            trylist = [ d for d in self._provides[depname]
                        if d in self._packages and self._allowed_dependency(pkg, d, seed, build_depend) ]
        elif (self._check_versioned_dependency(depname, depver, deptype) and
              self._allowed_dependency(pkg, depname, seed, build_depend)):
            trylist = [ depname ]
        else:
            return False

        for trydep in trylist:
            if with_build:
                for innerseed in self._inner_seeds(seed):
                    if trydep in innerseed._build:
                        return True
            else:
                for innerseed in self._inner_seeds(seed):
                    if trydep in innerseed._not_build:
                        return True
            if (trydep in seed._entries or
                trydep in seed._recommends_entries):
                return True
        else:
            return False

    def _add_dependency(self, seed, pkg, dependlist, build_depend,
                        second_class, build_tree, recommends):
        """Add a single dependency. Returns True if a dependency was added,
           otherwise False."""
        if build_tree and build_depend:
            why = self._packages[pkg]["Source"] + " (Build-Depend)"
        elif recommends:
            why = pkg + " (Recommends)"
        else:
            why = pkg

        dependlist = self._weed_blacklist(dependlist, seed, build_tree, why)
        if not dependlist:
            return False

        if build_tree:
            for dep in dependlist:
                seed._build_depends.add(dep)
        else:
            for dep in dependlist:
                seed._depends.add(dep)

        for dep in dependlist:
            self._add_package(seed, dep, why,
                              build_tree, second_class, recommends)

        return True

    def _promote_dependency(self, seed, pkg, depend, close, build_depend,
                            second_class, build_tree, recommends):
        """Try to satisfy a dependency by promoting an item from a lesser
           seed. If close is True, only "close-by" seeds (ones that generate
           the same task, as defined by Task-Seeds headers) are considered.
           Returns True if a dependency was added, otherwise False."""
        (depname, depver, deptype) = depend
        if (self._check_versioned_dependency(depname, depver, deptype) and
            self._allowed_dependency(pkg, depname, seed, build_depend)):
            trylist = [ depname ]
        elif (self._allowed_virtual_dependency(pkg, deptype) and
              depname in self._provides):
            trylist = [ d for d in self._provides[depname]
                        if d in self._packages and
                           self._allowed_dependency(pkg, d, seed,
                                                    build_depend) ]
        else:
            return False

        for trydep in trylist:
            lesserseeds = self._strictly_outer_seeds(seed)
            if close:
                lesserseeds = [l for l in lesserseeds
                                 if seed.name in l._close_seeds]
            for lesserseed in lesserseeds:
                if (trydep in lesserseed._entries or
                    trydep in lesserseed._recommends_entries):
                    if second_class:
                        # "I'll get you next time, Gadget!"
                        # When processing the build tree, we don't promote
                        # packages from lesser seeds, since we still want to
                        # consider them (e.g.) part of ship even if they're
                        # build-dependencies of desktop. However, we do need
                        # to process them now anyway, since otherwise we
                        # might end up selecting the wrong alternative from
                        # an or-ed build-dependency.
                        pass
                    else:
                        if trydep in lesserseed._entries:
                            lesserseed._entries.remove(trydep)
                        if trydep in lesserseed._recommends_entries:
                            lesserseed._recommends_entries.remove(trydep)
                        logging.warning("Promoted %s from %s to %s to satisfy "
                                        "%s", trydep, lesserseed, seed, pkg)

                    return self._add_dependency(seed, pkg, [trydep],
                                                build_depend, second_class,
                                                build_tree, recommends)

        return False

    def _new_dependency(self, seed, pkg, depend, build_depend,
                        second_class, build_tree, recommends):
        """Try to satisfy a dependency by adding a new package to the output
           set. Returns True if a dependency was added, otherwise False."""
        (depname, depver, deptype) = depend
        if (self._check_versioned_dependency(depname, depver, deptype) and
            self._allowed_dependency(pkg, depname, seed, build_depend)):
            virtual = None
        elif self._allowed_virtual_dependency(pkg, deptype) and depname in self._provides:
            virtual = depname
        else:
            if build_depend:
                desc = "build-dependency"
            elif recommends:
                desc = "recommendation"
            else:
                desc = "dependency"
            logging.error("Unknown %s %s by %s", desc,
                          self._unparse_dependency(depname, depver, deptype),
                          pkg)
            return False

        dependlist = [depname]
        if virtual is not None:
            reallist = [ d for d in self._provides[virtual]
                         if d in self._packages and self._allowed_dependency(pkg, d, seed, build_depend) ]
            if len(reallist):
                depname = reallist[0]
                # If this one was a d-i kernel module, pick all the modules
                # for other allowed kernel versions too.
                if self._packages[depname]["Kernel-Version"] != "":
                    dependlist = [ d for d in reallist
                                   if not seed._di_kernel_versions or
                                      self._packages[d]["Kernel-Version"] in seed._di_kernel_versions ]
                else:
                    dependlist = [depname]
                logging.info("Chose %s out of %s to satisfy %s",
                             ", ".join(dependlist), virtual, pkg)
            else:
                logging.error("Nothing to choose out of %s to satisfy %s",
                              virtual, pkg)
                return False

        return self._add_dependency(seed, pkg, dependlist, build_depend,
                                    second_class, build_tree, recommends)

    def _add_dependency_tree(self, seed, pkg, depends,
                             build_depend=False,
                             second_class=False,
                             build_tree=False,
                             recommends=False):
        """Add a package's dependency tree."""
        if build_depend: build_tree = True
        if build_tree: second_class = True
        for deplist in depends:
            for dep in deplist:
                # TODO cjwatson 2008-07-02: At the moment this check will
                # catch an existing Recommends and we'll never get as far as
                # calling _remember_why with a dependency, so seed._reasons
                # will be a bit inaccurate. We may need another pass for
                # Recommends to fix this.
                if self._already_satisfied(seed, pkg, dep, build_depend, second_class):
                    break
            else:
                firstdep = True
                for dep in deplist:
                    if firstdep:
                        # For the first (preferred) alternative, we may
                        # consider promoting it from any lesser seed.
                        close = False
                        firstdep = False
                    else:
                        # Other alternatives are less favoured, and will
                        # only be promoted from closely-allied seeds.
                        close = True
                    if self._promote_dependency(seed, pkg, dep, close,
                                                build_depend, second_class,
                                                build_tree, recommends):
                        if len(deplist) > 1:
                            logging.info("Chose %s to satisfy %s", dep[0], pkg)
                        break
                else:
                    for dep in deplist:
                        if self._new_dependency(seed, pkg, dep,
                                                build_depend,
                                                second_class, build_tree,
                                                recommends):
                            if len(deplist) > 1:
                                logging.info("Chose %s to satisfy %s", dep[0],
                                             pkg)
                            break
                    else:
                        if len(deplist) > 1:
                            logging.error("Nothing to choose to satisfy %s",
                                          pkg)

    def _remember_why(self, reasons, pkg, why, build_tree=False,
                      recommends=False):
        """Remember why this package was added to the output for this seed."""
        if pkg in reasons:
            old_why, old_build_tree, old_recommends = reasons[pkg]
            # Reasons from the dependency tree beat reasons from the
            # build-dependency tree; but pick the first of either type that
            # we see. Within either tree, dependencies beat recommendations.
            if not old_build_tree and build_tree:
                return
            if old_build_tree == build_tree:
                if not old_recommends or recommends:
                    return

        reasons[pkg] = (why, build_tree, recommends)

    def _add_package(self, seed, pkg, why,
                     second_class=False,
                     build_tree=False,
                     recommends=False):
        """Add a package and its dependency trees."""
        if self._is_pruned(seed, pkg):
            logging.warning("Pruned %s from %s", pkg, seed)
            return
        if build_tree:
            outerseeds = [self._supported(seed)]
        else:
            outerseeds = self._outer_seeds(seed)
        for outerseed in outerseeds:
            if outerseed is not None and pkg in outerseed._blacklist:
                logging.error("Package %s blacklisted in %s but seeded in %s "
                              "(%s)", pkg, outerseed, seed, why)
                seed._blacklist_seen = True
                return
        if build_tree: second_class=True

        output = self._output[seed.structure]

        if pkg not in output._all:
            output._all.add(pkg)

        for innerseed in self._inner_seeds(seed):
            if pkg in innerseed._build:
                break
        else:
            seed._build.add(pkg)

        if not build_tree:
            for innerseed in self._inner_seeds(seed):
                if pkg in innerseed._not_build:
                    break
            else:
                seed._not_build.add(pkg)

        # Remember why the package was added to the output for this seed.
        # Also remember a reason for "all" too, so that an aggregated list
        # of all selected packages can be constructed easily.
        self._remember_why(seed._reasons, pkg, why, build_tree, recommends)
        self._remember_why(output._all_reasons, pkg, why, build_tree,
                           recommends)

        for prov in self._packages[pkg]["Provides"]:
            if prov[0][0] not in output._pkgprovides:
                output._pkgprovides[prov[0][0]] = set()
            output._pkgprovides[prov[0][0]].add(pkg)

        self._add_dependency_tree(seed, pkg,
                                  self._packages[pkg]["Pre-Depends"],
                                  second_class=second_class,
                                  build_tree=build_tree)

        self._add_dependency_tree(seed, pkg,
                                  self._packages[pkg]["Depends"],
                                  second_class=second_class,
                                  build_tree=build_tree)

        if (self._follow_recommends(seed) or
            self._packages[pkg]["Section"] == "metapackages"):
            self._add_dependency_tree(seed, pkg,
                                      self._packages[pkg]["Recommends"],
                                      second_class=second_class,
                                      build_tree=build_tree,
                                      recommends=True)

        src = self._packages[pkg]["Source"]
        if src not in self._sources:
            logging.error("Missing source package: %s (for %s)", src, pkg)
            return

        if second_class:
            for innerseed in self._inner_seeds(seed):
                if src in innerseed._build_srcs:
                    return
        else:
            for innerseed in self._inner_seeds(seed):
                if src in innerseed._not_build_srcs:
                    return

        if build_tree:
            seed._build_sourcepkgs.add(src)
            if src in output._blacklist:
                output._blacklisted.add(src)

        else:
            if src in output._all_srcs:
                for buildseed in self._seeds.itervalues():
                    buildseed._build_sourcepkgs.discard(src)

            seed._not_build_srcs.add(src)
            seed._sourcepkgs.add(src)

        output._all_srcs.add(src)
        seed._build_srcs.add(src)

        self._add_dependency_tree(seed, pkg,
                                  self._sources[src]["Build-Depends"],
                                  build_depend=True)
        self._add_dependency_tree(seed, pkg,
                                  self._sources[src]["Build-Depends-Indep"],
                                  build_depend=True)

    def _rescue_includes(self, structure, seedname, rescue_seedname,
                         build_tree):
        """Automatically rescue packages matching certain patterns from
        other seeds."""

        output = self._output[structure]

        try:
            seed = self.get_seed(structure, seedname)
        except KeyError:
            return

        if rescue_seedname not in self._seeds and rescue_seedname != "extra":
            return

        # Find all the source packages.
        rescue_srcs = set()
        if rescue_seedname == "extra":
            rescue_seeds = self._inner_seeds(seed)
        else:
            rescue_seeds = [self.get_seed(structure, rescue_seedname)]
        for one_rescue_seed in rescue_seeds:
            if build_tree:
                rescue_srcs |= one_rescue_seed._build_srcs
            else:
                rescue_srcs |= one_rescue_seed._not_build_srcs

        # For each source, add any binaries that match the include/exclude
        # patterns.
        for src in rescue_srcs:
            rescue = [p for p in self._sources[src]["Binaries"]
                        if p in self._packages]
            included = set()
            if rescue_seedname in seed._includes:
                for include in seed._includes[rescue_seedname]:
                    included |= set(self._filter_packages(rescue, include))
            if rescue_seedname in seed._excludes:
                for exclude in seed._excludes[rescue_seedname]:
                    included -= set(self._filter_packages(rescue, exclude))
            for pkg in included:
                if pkg in output._all:
                    continue
                for lesserseed in self._strictly_outer_seeds(seed):
                    if pkg in lesserseed._entries:
                        seed._entries.remove(pkg)
                        logging.warning("Promoted %s from %s to %s due to "
                                        "%s-Includes",
                                        pkg, lesserseed, seed,
                                        rescue_seedname.title())
                        break
                logging.debug("Rescued %s from %s to %s", pkg,
                              rescue_seedname, seed)
                if build_tree:
                    seed._build_depends.add(pkg)
                else:
                    seed._depends.add(pkg)
                self._add_package(seed, pkg, "Rescued from %s" % src,
                                  build_tree=build_tree)

    # Accessors.
    # ----------

    def get_source(self, pkg):
        return self._packages[pkg]["Source"]

    def is_essential(self, pkg):
        return self._packages[pkg].get("Essential", "no") == "yes"

    def get_seed(self, structure, seedname):
        full_seedname = self._make_seed_name(structure.branch, seedname)
        return self._seeds[full_seedname]

    def get_seed_entries(self, structure, seedname):
        return self.get_seed(structure, seedname).entries

    def get_seed_recommends_entries(self, structure, seedname):
        return self.get_seed(structure, seedname).recommends_entries

    def get_depends(self, structure, seedname):
        return self.get_seed(structure, seedname).depends

    def get_full(self, structure, seedname):
        seed = self.get_seed(structure, seedname)
        return (set(seed._entries) |
                set(seed._recommends_entries) |
                seed._depends)

    def get_build_depends(self, structure, seedname):
        output = set(self.get_seed(structure, seedname)._build_depends)
        for outerseedname in structure.outer_seeds(seedname):
            output -= self.get_full(structure, outerseedname)
        return output

    def get_all(self, structure):
        return list(self._output[structure]._all)

    # Methods for writing output to files.
    # ------------------------------------

    def _write_list(self, reasons, filename, pkgset):
        pkglist = sorted(pkgset)

        pkg_len = len("Package")
        src_len = len("Source")
        why_len = len("Why")
        mnt_len = len("Maintainer")

        for pkg in pkglist:
            _pkg_len = len(pkg)
            if _pkg_len > pkg_len: pkg_len = _pkg_len

            _src_len = len(self._packages[pkg]["Source"])
            if _src_len > src_len: src_len = _src_len

            _why_len = len(reasons[pkg][0])
            if _why_len > why_len: why_len = _why_len

            _mnt_len = len(self._packages[pkg]["Maintainer"])
            if _mnt_len > mnt_len: mnt_len = _mnt_len

        size = 0
        installed_size = 0

        with codecs.open(filename, "w", "utf8", "replace") as f:
            print >>f, "%-*s | %-*s | %-*s | %-*s | %-15s | %-15s" % \
                  (pkg_len, "Package",
                   src_len, "Source",
                   why_len, "Why",
                   mnt_len, "Maintainer",
                   "Deb Size (B)",
                   "Inst Size (KB)")
            print >>f, ("-" * pkg_len) + "-+-" + ("-" * src_len) + "-+-" \
                  + ("-" * why_len) + "-+-" + ("-" * mnt_len) + "-+-" \
                  + ("-" * 15) + "-+-" + ("-" * 15) + "-"
            for pkg in pkglist:
                size += self._packages[pkg]["Size"]
                installed_size += self._packages[pkg]["Installed-Size"]
                print >>f, "%-*s | %-*s | %-*s | %-*s | %15d | %15d" % \
                      (pkg_len, pkg,
                       src_len, self._packages[pkg]["Source"],
                       why_len, reasons[pkg][0],
                       mnt_len, self._packages[pkg]["Maintainer"],
                       self._packages[pkg]["Size"],
                       self._packages[pkg]["Installed-Size"])
            print >>f, ("-" * (pkg_len + src_len + why_len + mnt_len + 9)) \
                  + "-+-" + ("-" * 15) + "-+-" + ("-" * 15) + "-"
            print >>f, "%*s | %15d | %15d" % \
                  ((pkg_len + src_len + why_len + mnt_len + 9), "",
                   size, installed_size)

    def _write_source_list(self, filename, srcset):
        srclist = sorted(srcset)

        src_len = len("Source")
        mnt_len = len("Maintainer")

        for src in srclist:
            _src_len = len(src)
            if _src_len > src_len: src_len = _src_len

            _mnt_len = len(self._sources[src]["Maintainer"])
            if _mnt_len > mnt_len: mnt_len = _mnt_len

        with codecs.open(filename, "w", "utf8", "replace") as f:
            fmt = "%-*s | %-*s"

            print >>f, fmt % (src_len, "Source", mnt_len, "Maintainer")
            print >>f, ("-" * src_len) + "-+-" + ("-" * mnt_len) + "-"
            for src in srclist:
                print >>f, fmt % (src_len, src, mnt_len,
                                  self._sources[src]["Maintainer"])

    def write_full_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_list(seed._reasons, filename,
                         self.get_full(structure, seedname))

    def write_seed_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_list(seed._reasons, filename, seed._entries)

    def write_seed_recommends_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_list(seed._reasons, filename, seed._recommends_entries)

    def write_depends_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_list(seed._reasons, filename, seed._depends)

    def write_build_depends_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_list(seed._reasons, filename,
                         self.get_build_depends(structure, seedname))

    def write_sources_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_source_list(filename, seed._sourcepkgs)

    def write_build_sources_list(self, structure, filename, seedname):
        seed = self.get_seed(structure, seedname)
        self._write_source_list(filename, seed._build_sourcepkgs)

    def write_all_list(self, structure, filename):
        all_bins = set()

        for seedname in structure.names:
            if seedname == "extra":
                continue

            all_bins |= self.get_full(structure, seedname)
            all_bins |= self.get_build_depends(structure, seedname)

        self._write_list(self._output[structure]._all_reasons, filename,
                         all_bins)

    def write_all_source_list(self, structure, filename):
        all_srcs = set()

        for seedname in structure.names:
            if seedname == "extra":
                continue
            seed = self.get_seed(structure, seedname)

            all_srcs |= seed._sourcepkgs
            all_srcs |= seed._build_sourcepkgs

        self._write_source_list(filename, all_srcs)

    def write_supported_list(self, structure, filename):
        sup_bins = set()

        for seedname in structure.names:
            if seedname == "extra":
                continue

            if seedname == structure.supported:
                sup_bins |= self.get_full(structure, seedname)

            # Only include those build-dependencies that aren't already in
            # the dependency outputs for inner seeds of supported. This
            # allows supported+build-depends to be usable as an "everything
            # else" output.
            build_depends = set(self.get_build_depends(structure, seedname))
            for innerseedname in structure.inner_seeds(structure.supported):
                build_depends -= self.get_full(structure, innerseedname)
            sup_bins |= build_depends

        self._write_list(self._output[structure]._all_reasons, filename,
                         sup_bins)

    def write_supported_source_list(self, structure, filename):
        sup_srcs = set()

        for seedname in structure.names:
            if seedname == "extra":
                continue
            seed = self.get_seed(structure, seedname)

            if seedname == structure.supported:
                sup_srcs |= seed._sourcepkgs

            # Only include those build-dependencies that aren't already in
            # the dependency outputs for inner seeds of supported. This
            # allows supported+build-depends to be usable as an "everything
            # else" output.
            build_sourcepkgs = set(seed._build_sourcepkgs)
            for innerseed in self._inner_seeds(self._supported(seed)):
                build_sourcepkgs -= innerseed._sourcepkgs
            sup_srcs |= build_sourcepkgs

        self._write_source_list(filename, sup_srcs)

    def write_all_extra_list(self, structure, filename):
        output = self._output[structure]
        self._write_list(output._all_reasons, filename, output._all)

    def write_all_extra_source_list(self, structure, filename):
        output = self._output[structure]
        self._write_source_list(filename, output._all_srcs)

    def write_rdepend_list(self, structure, filename, pkg):
        with open(filename, "w") as f:
            print >>f, pkg
            self._write_rdepend_list(structure, f, pkg, "", done=set())

    def _write_rdepend_list(self, structure, f, pkg, prefix, stack=None,
                            done=None):
        if stack is None:
            stack = []
        else:
            stack = list(stack)
            if pkg in stack:
                print >>f, prefix + "! loop"
                return
        stack.append(pkg)

        if done is None:
            done = set()
        elif pkg in done:
            print >>f, prefix + "! skipped"
            return
        done.add(pkg)

        for seedname in self._output[structure]._seednames:
            if pkg in self.get_seed_entries(structure, seedname):
                print >>f, prefix + "*", seedname.title(), "seed"

        if "Reverse-Depends" not in self._packages[pkg]:
            return

        for field in ("Pre-Depends", "Depends", "Recommends",
                      "Build-Depends", "Build-Depends-Indep"):
            if field not in self._packages[pkg]["Reverse-Depends"]:
                continue

            i = 0
            print >>f, prefix + "*", "Reverse", field + ":"
            for dep in self._packages[pkg]["Reverse-Depends"][field]:
                i += 1
                print >>f, prefix + " +- " + dep
                if field.startswith("Build-"):
                    continue

                if i == len(self._packages[pkg]["Reverse-Depends"][field]):
                    extra = "    "
                else:
                    extra = " |  "
                self._write_rdepend_list(structure, f, dep, prefix + extra,
                                         stack, done)

    def write_provides_list(self, structure, filename):
        output = self._output[structure]

        with open(filename, "w") as f:
            for prov in sorted(output._pkgprovides.keys()):
                print >>f, prov
                for pkg in sorted(output._pkgprovides[prov]):
                    print >>f, "\t%s" % (pkg,)
                print >>f

    def write_blacklisted(self, structure, filename):
        """Write out the list of blacklisted packages we encountered"""

        output = self._output[structure]

        with open(filename, 'w') as fh:
            for pkg in sorted(output._blacklisted):
                blacklist = output._blacklist[pkg]
                fh.write('%s\t%s\n' % (pkg, blacklist))


def pretty_logging():
    logging.addLevelName(logging.DEBUG, '  ')
    logging.addLevelName(Germinator.PROGRESS, '')
    logging.addLevelName(logging.INFO, '* ')
    logging.addLevelName(logging.WARNING, '! ')
    logging.addLevelName(logging.ERROR, '? ')
