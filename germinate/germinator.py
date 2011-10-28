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

import apt_pkg

from germinate.archive import IndexType
from germinate.seeds import Seed

# TODO: would be much more elegant to reduce our recursion depth!
sys.setrecursionlimit(2000)

class Germinator:
    PROGRESS = 15

    # Initialisation.
    # ---------------

    def __init__(self, arch):
        self._arch = arch
        apt_pkg.config.set("APT::Architecture", self._arch)

        self._packages = {}
        self._packagetype = {}
        self._provides = {}
        self._sources = {}
        self._pruned = {}

        self._structure = None
        self._seeds = []
        self._seed = {}
        self._seedfeatures = {}
        self._seedrecommends = {}
        self._close_seeds = {}
        self._substvars = {}
        self._depends = {}
        self._build_depends = {}
        self._supported = None

        self._sourcepkgs = {}
        self._build_sourcepkgs = {}

        self._pkgprovides = {}

        self._all = set()
        self._build = {}
        self._not_build = {}

        self._all_srcs = set()
        self._build_srcs = {}
        self._not_build_srcs = {}

        self._why = {}
        self._why["all"] = {}

        self._hints = {}

        self._blacklist = {}
        self._blacklisted = set()
        self._seedblacklist = {}

        self._di_kernel_versions = {}
        self._includes = {}
        self._excludes = {}

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
        self._pruned[pkg] = set()

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

    def parse_blacklist(self, f):
        """Parse a blacklist file, used to indicate unwanted packages"""

        name = ''

        for line in f:
            line = line.strip()
            if line.startswith('# blacklist: '):
                name = line[13:]
            elif not line or line.startswith('#'):
                continue
            else:
                self._blacklist[line] = name
        f.close()

    # The main germination algorithm.
    # -------------------------------

    def _new_seed(self, seedname):
        self._seeds.append(seedname)
        self._seed[seedname] = []
        self._seedfeatures[seedname] = set()
        self._seedrecommends[seedname] = []
        self._close_seeds[seedname] = set()
        self._depends[seedname] = set()
        self._build_depends[seedname] = set()
        self._sourcepkgs[seedname] = set()
        self._build_sourcepkgs[seedname] = set()
        self._build[seedname] = set()
        self._not_build[seedname] = set()
        self._build_srcs[seedname] = set()
        self._not_build_srcs[seedname] = set()
        self._why[seedname] = {}
        self._seedblacklist[seedname] = set()
        self._di_kernel_versions[seedname] = set()
        self._includes[seedname] = {}
        self._excludes[seedname] = {}

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
        filtered.sort()
        return filtered

    def _substitute_seed_vars(self, pkg):
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
                if name in self._substvars:
                    # Duplicate substituted once for each available substvar
                    # expansion.
                    newsubst = []
                    for value in self._substvars[name]:
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

    def _already_seeded(self, seedname, pkg):
        """Has pkg already been seeded in this seed or in one from
        which we inherit?"""

        for seed in self._structure.inner_seeds(seedname):
            if (pkg in self._seed[seed] or
                pkg in self._seedrecommends[seed]):
                return True

        return False

    def _plant_seed(self, seedname):
        """Add a seed."""
        if seedname in self._seeds:
            return

        self._new_seed(seedname)
        seedpkgs = []
        seedrecommends = []

        for line in self._structure.texts[seedname]:
            if line.lower().startswith('task-seeds:'):
                self._close_seeds[seedname].update(line[11:].strip().split())
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
                    self._di_kernel_versions[seedname].update(values)
                elif name == "feature":
                    logging.warning("Setting features {%s} for seed %s",
                                    ', '.join(values), seedname)
                    self._seedfeatures[seedname].update(values)
                elif name.endswith("-include"):
                    included_seed = name[:-8]
                    if (included_seed not in self._seeds and
                        included_seed != "extra"):
                        logging.error("Cannot include packages from unknown "
                                      "seed: %s", included_seed)
                    else:
                        logging.warning("Including packages from %s: %s",
                                        included_seed, values)
                        if included_seed not in self._includes[seedname]:
                            self._includes[seedname][included_seed] = []
                        self._includes[seedname][included_seed].extend(values)
                elif name.endswith("-exclude"):
                    excluded_seed = name[:-8]
                    if (excluded_seed not in self._seeds and
                        excluded_seed != "extra"):
                        logging.error("Cannot exclude packages from unknown "
                                      "seed: %s", excluded_seed)
                    else:
                        logging.warning("Excluding packages from %s: %s",
                                        excluded_seed, values)
                        if excluded_seed not in self._excludes[seedname]:
                            self._excludes[seedname][excluded_seed] = []
                        self._excludes[seedname][excluded_seed].extend(values)
                self._substvars[name] = values
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
                    seedrecommends.extend(self._substitute_seed_vars(pkg))

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
                    logging.info("Blacklisting %s from %s", pkg, seedname)
                    self._seedblacklist[seedname].update(
                        self._substitute_seed_vars(pkg))
            else:
                for pkg in pkgs:
                    seedpkgs.extend(self._substitute_seed_vars(pkg))

        for pkg in seedpkgs:
            if pkg in self._hints and self._hints[pkg] != seedname:
                logging.warning("Taking the hint: %s", pkg)
                continue

            if pkg in self._packages:
                # Ordinary package
                if self._already_seeded(seedname, pkg):
                    logging.warning("Duplicated seed: %s", pkg)
                elif self._is_pruned(pkg, seedname):
                    logging.warning("Pruned %s from %s", pkg, seedname)
                else:
                    if pkg in seedrecommends:
                        self._seedrecommends[seedname].append(pkg)
                    else:
                        self._seed[seedname].append(pkg)
            elif pkg in self._provides:
                # Virtual package, include everything
                msg = "Virtual %s package: %s" % (seedname, pkg)
                for vpkg in self._provides[pkg]:
                    if self._already_seeded(seedname, vpkg):
                        pass
                    elif seedname in self._pruned[vpkg]:
                        pass
                    else:
                        msg += "\n  - %s" % vpkg
                        if pkg in seedrecommends:
                            self._seedrecommends[seedname].append(vpkg)
                        else:
                            self._seed[seedname].append(vpkg)
                logging.info("%s", msg)

            else:
                # No idea
                logging.error("Unknown %s package: %s", seedname, pkg)

        for pkg in self._hints:
            if (self._hints[pkg] == seedname and
                not self._already_seeded(seedname, pkg)):
                if pkg in self._packages:
                    if pkg in seedrecommends:
                        self._seedrecommends[seedname].append(pkg)
                    else:
                        self._seed[seedname].append(pkg)
                else:
                    logging.error("Unknown hinted package: %s", pkg)

    def plant_seeds(self, structure, seeds=None):
        """Add all seeds found in a seed structure."""
        if seeds is not None:
            structure.limit(seeds)

        self._structure = structure
        self._supported = structure.original_names[-1]
        for name in structure.names:
            structure.fetch(name)
            self._plant_seed(name)

    def _is_pruned(self, pkg, seed):
        if not self._di_kernel_versions[seed]:
            return False
        kernver = self._packages[pkg]["Kernel-Version"]
        if kernver != "" and kernver not in self._di_kernel_versions[seed]:
            return True
        return False

    def prune(self):
        """Remove packages that are inapplicable for some reason, such as
           being for the wrong d-i kernel version."""
        for pkg in self._packages:
            for seed in self._seeds:
                if self._is_pruned(pkg, seed):
                    self._pruned[pkg].add(seed)

    def _weed_blacklist(self, pkgs, seedname, build_tree, why):
        """Weed out blacklisted seed entries from a list."""
        white = []
        if build_tree:
            outerseeds = [self._supported]
        else:
            outerseeds = self._structure.outer_seeds(seedname)
        for pkg in pkgs:
            for outerseed in outerseeds:
                if (outerseed in self._seedblacklist and
                    pkg in self._seedblacklist[outerseed]):
                    logging.error("Package %s blacklisted in %s but seeded in "
                                  "%s (%s)", pkg, outerseed, seedname, why)
                    break
            else:
                white.append(pkg)
        return white

    def grow(self):
        """Grow the seeds."""
        for seedname in self._seeds:
            logging.log(self.PROGRESS,
                        "Resolving %s dependencies ...", seedname)
            if self._structure.branch is None:
                why = "%s seed" % seedname.title()
            else:
                why = ("%s %s seed" %
                       (self._structure.branch.title(), seedname))

            # Check for blacklisted seed entries.
            self._seed[seedname] = self._weed_blacklist(
                self._seed[seedname], seedname, False, why)
            self._seedrecommends[seedname] = self._weed_blacklist(
                self._seedrecommends[seedname], seedname, False, why)

            # Note that seedrecommends are not processed with
            # recommends=True; that is reserved for Recommends of packages,
            # not packages recommended by the seed. Changing this results in
            # less helpful output when a package is recommended by an inner
            # seed and required by an outer seed.
            for pkg in self._seed[seedname] + self._seedrecommends[seedname]:
                self._add_package(seedname, pkg, why)

            for rescue_seedname in self._seeds:
                self._rescue_includes(seedname, rescue_seedname,
                                      build_tree=False)
                if rescue_seedname == seedname:
                    # only rescue from seeds up to and including the current
                    # seed; later ones have not been grown
                    break
            self._rescue_includes(seedname, "extra", build_tree=False)

        self._rescue_includes(self._supported, "extra", build_tree=True)

    def add_extras(self):
        """Add packages generated by the sources but not in any seed."""
        self._structure.add_extra()
        self._new_seed("extra")

        logging.log(self.PROGRESS, "Identifying extras ...")
        found = True
        while found:
            found = False
            sorted_srcs = list(self._all_srcs)
            sorted_srcs.sort()
            for srcname in sorted_srcs:
                for pkg in self._sources[srcname]["Binaries"]:
                    if pkg not in self._packages:
                        continue
                    if self._packages[pkg]["Source"] != srcname:
                        continue
                    if pkg in self._all:
                        continue

                    if pkg in self._hints and self._hints[pkg] != "extra":
                        logging.warning("Taking the hint: %s", pkg)
                        continue

                    self._seed["extra"].append(pkg)
                    self._add_package("extra", pkg, "Generated by " + srcname,
                                      second_class=True)
                    found = True

    def _allowed_dependency(self, pkg, depend, seedname, build_depend):
        """Is pkg allowed to satisfy a (build-)dependency using depend
           within seedname? Note that depend must be a real package.
           
           If seedname is None, check whether the (build-)dependency is
           allowed within any seed."""
        if depend not in self._packages:
            logging.warning("_allowed_dependency called with virtual package "
                            "%s", depend)
            return False
        if seedname is not None and seedname in self._pruned[depend]:
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
        if seed is not None and seed in self._seedfeatures:
            if "follow-recommends" in self._seedfeatures[seed]:
                return True
            if "no-follow-recommends" in self._seedfeatures[seed]:
                return False
        if "follow-recommends" in self._structure.features:
            return True
        return False

    def _add_reverse(self, pkg, field, rdep):
        """Add a reverse dependency entry."""
        if "Reverse-Depends" not in self._packages[pkg]:
            self._packages[pkg]["Reverse-Depends"] = {}
        if field not in self._packages[pkg]["Reverse-Depends"]:
            self._packages[pkg]["Reverse-Depends"][field] = []

        self._packages[pkg]["Reverse-Depends"][field].append(rdep)

    def reverse_depends(self):
        """Calculate the reverse dependency relationships."""
        for pkg in self._all:
            fields = ["Pre-Depends", "Depends"]
            if (self._follow_recommends() or
                self._packages[pkg]["Section"] == "metapackages"):
                fields.append("Recommends")
            for field in fields:
                for deplist in self._packages[pkg][field]:
                    for dep in deplist:
                        if dep[0] in self._all and \
                           self._allowed_dependency(pkg, dep[0], None, False):
                            self._add_reverse(dep[0], field, pkg)

        for src in self._all_srcs:
            for field in "Build-Depends", "Build-Depends-Indep":
                for deplist in self._sources[src][field]:
                    for dep in deplist:
                        if dep[0] in self._all and \
                           self._allowed_dependency(src, dep[0], None, True):
                            self._add_reverse(dep[0], field, src)

        for pkg in self._all:
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

    def _already_satisfied(self, seedname, pkg, depend, build_depend=False, with_build=False):
        """Work out whether a dependency has already been satisfied."""
        (depname, depver, deptype) = depend
        if self._allowed_virtual_dependency(pkg, deptype) and depname in self._provides:
            trylist = [ d for d in self._provides[depname]
                        if d in self._packages and self._allowed_dependency(pkg, d, seedname, build_depend) ]
        elif (self._check_versioned_dependency(depname, depver, deptype) and
              self._allowed_dependency(pkg, depname, seedname, build_depend)):
            trylist = [ depname ]
        else:
            return False

        for trydep in trylist:
            if with_build:
                for seed in self._structure.inner_seeds(seedname):
                    if trydep in self._build[seed]:
                        return True
            else:
                for seed in self._structure.inner_seeds(seedname):
                    if trydep in self._not_build[seed]:
                        return True
            if (trydep in self._seed[seedname] or
                trydep in self._seedrecommends[seedname]):
                return True
        else:
            return False

    def _add_dependency(self, seedname, pkg, dependlist, build_depend,
                        second_class, build_tree, recommends):
        """Add a single dependency. Returns True if a dependency was added,
           otherwise False."""
        if build_tree and build_depend:
            why = self._packages[pkg]["Source"] + " (Build-Depend)"
        elif recommends:
            why = pkg + " (Recommends)"
        else:
            why = pkg

        dependlist = self._weed_blacklist(dependlist, seedname, build_tree,
                                          why)
        if not dependlist:
            return False

        if build_tree:
            for dep in dependlist:
                self._build_depends[seedname].add(dep)
        else:
            for dep in dependlist:
                self._depends[seedname].add(dep)

        for dep in dependlist:
            self._add_package(seedname, dep, why,
                              build_tree, second_class, recommends)

        return True

    def _promote_dependency(self, seedname, pkg, depend, close, build_depend,
                            second_class, build_tree, recommends):
        """Try to satisfy a dependency by promoting an item from a lesser
           seed. If close is True, only "close-by" seeds (ones that generate
           the same task, as defined by Task-Seeds headers) are considered.
           Returns True if a dependency was added, otherwise False."""
        (depname, depver, deptype) = depend
        if (self._check_versioned_dependency(depname, depver, deptype) and
            self._allowed_dependency(pkg, depname, seedname, build_depend)):
            trylist = [ depname ]
        elif (self._allowed_virtual_dependency(pkg, deptype) and
              depname in self._provides):
            trylist = [ d for d in self._provides[depname]
                        if d in self._packages and
                           self._allowed_dependency(pkg, d, seedname,
                                                    build_depend) ]
        else:
            return False

        for trydep in trylist:
            lesserseeds = self._structure.strictly_outer_seeds(seedname)
            if close:
                lesserseeds = [l for l in lesserseeds
                                 if seedname in self._close_seeds[l]]
            for lesserseed in lesserseeds:
                if (trydep in self._seed[lesserseed] or
                    trydep in self._seedrecommends[lesserseed]):
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
                        if trydep in self._seed[lesserseed]:
                            self._seed[lesserseed].remove(trydep)
                        if trydep in self._seedrecommends[lesserseed]:
                            self._seedrecommends[lesserseed].remove(trydep)
                        logging.warning("Promoted %s from %s to %s to satisfy "
                                        "%s",
                                        trydep, lesserseed, seedname, pkg)

                    return self._add_dependency(seedname, pkg, [trydep],
                                                build_depend, second_class,
                                                build_tree, recommends)

        return False

    def _new_dependency(self, seedname, pkg, depend, build_depend,
                        second_class, build_tree, recommends):
        """Try to satisfy a dependency by adding a new package to the output
           set. Returns True if a dependency was added, otherwise False."""
        (depname, depver, deptype) = depend
        if (self._check_versioned_dependency(depname, depver, deptype) and
            self._allowed_dependency(pkg, depname, seedname, build_depend)):
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
                         if d in self._packages and self._allowed_dependency(pkg, d, seedname, build_depend) ]
            if len(reallist):
                depname = reallist[0]
                # If this one was a d-i kernel module, pick all the modules
                # for other allowed kernel versions too.
                if self._packages[depname]["Kernel-Version"] != "":
                    dependlist = [ d for d in reallist
                                   if not self._di_kernel_versions[seedname] or
                                      self._packages[d]["Kernel-Version"] in self._di_kernel_versions[seedname] ]
                else:
                    dependlist = [depname]
                logging.info("Chose %s out of %s to satisfy %s",
                             ", ".join(dependlist), virtual, pkg)
            else:
                logging.error("Nothing to choose out of %s to satisfy %s",
                              virtual, pkg)
                return False

        return self._add_dependency(seedname, pkg, dependlist, build_depend,
                                    second_class, build_tree, recommends)

    def _add_dependency_tree(self, seedname, pkg, depends,
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
                # calling _remember_why with a dependency, so self._why will
                # be a bit inaccurate. We may need another pass for
                # Recommends to fix this.
                if self._already_satisfied(seedname, pkg, dep, build_depend, second_class):
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
                    if self._promote_dependency(seedname, pkg, dep, close,
                                                build_depend, second_class,
                                                build_tree, recommends):
                        if len(deplist) > 1:
                            logging.info("Chose %s to satisfy %s", dep[0], pkg)
                        break
                else:
                    for dep in deplist:
                        if self._new_dependency(seedname, pkg, dep,
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

    def _remember_why(self, seedname, pkg, why, build_tree=False,
                      recommends=False):
        """Remember why this package was added to the output for this seed."""
        if pkg in self._why[seedname]:
            old_why, old_build_tree, old_recommends = self._why[seedname][pkg]
            # Reasons from the dependency tree beat reasons from the
            # build-dependency tree; but pick the first of either type that
            # we see. Within either tree, dependencies beat recommendations.
            if not old_build_tree and build_tree:
                return
            if old_build_tree == build_tree:
                if not old_recommends or recommends:
                    return

        self._why[seedname][pkg] = (why, build_tree, recommends)

    def _add_package(self, seedname, pkg, why,
                     second_class=False,
                     build_tree=False,
                     recommends=False):
        """Add a package and its dependency trees."""
        if seedname in self._pruned[pkg]:
            logging.warning("Pruned %s from %s", pkg, seedname)
            return
        if build_tree:
            outerseeds = [self._supported]
        else:
            outerseeds = self._structure.outer_seeds(seedname)
        for outerseed in outerseeds:
            if (outerseed in self._seedblacklist and
                pkg in self._seedblacklist[outerseed]):
                logging.error("Package %s blacklisted in %s but seeded in %s "
                              "(%s)", pkg, outerseed, seedname, why)
                return
        if build_tree: second_class=True

        if pkg not in self._all:
            self._all.add(pkg)
        elif not build_tree:
            for buildseed in self._structure.inner_seeds(seedname):
                self._build_depends[buildseed].discard(pkg)

        for seed in self._structure.inner_seeds(seedname):
            if pkg in self._build[seed]:
                break
        else:
            self._build[seedname].add(pkg)

        if not build_tree:
            for seed in self._structure.inner_seeds(seedname):
                if pkg in self._not_build[seed]:
                    break
            else:
                self._not_build[seedname].add(pkg)

        # Remember why the package was added to the output for this seed.
        # Also remember a reason for "all" too, so that an aggregated list
        # of all selected packages can be constructed easily.
        self._remember_why(seedname, pkg, why, build_tree, recommends)
        self._remember_why("all", pkg, why, build_tree, recommends)

        for prov in self._packages[pkg]["Provides"]:
            if prov[0][0] not in self._pkgprovides:
                self._pkgprovides[prov[0][0]] = set()
            self._pkgprovides[prov[0][0]].add(pkg)

        self._add_dependency_tree(seedname, pkg,
                                  self._packages[pkg]["Pre-Depends"],
                                  second_class=second_class,
                                  build_tree=build_tree)

        self._add_dependency_tree(seedname, pkg,
                                  self._packages[pkg]["Depends"],
                                  second_class=second_class,
                                  build_tree=build_tree)

        if (self._follow_recommends(seedname) or
            self._packages[pkg]["Section"] == "metapackages"):
            self._add_dependency_tree(seedname, pkg,
                                      self._packages[pkg]["Recommends"],
                                      second_class=second_class,
                                      build_tree=build_tree,
                                      recommends=True)

        src = self._packages[pkg]["Source"]
        if src not in self._sources:
            logging.error("Missing source package: %s (for %s)", src, pkg)
            return

        if second_class:
            for seed in self._structure.inner_seeds(seedname):
                if src in self._build_srcs[seed]:
                    return
        else:
            for seed in self._structure.inner_seeds(seedname):
                if src in self._not_build_srcs[seed]:
                    return

        if build_tree:
            self._build_sourcepkgs[seedname].add(src)
            if src in self._blacklist:
                self._blacklisted.add(src)

        else:
            if src in self._all_srcs:
                for buildseed in self._seeds:
                    self._build_sourcepkgs[buildseed].discard(src)

            self._not_build_srcs[seedname].add(src)
            self._sourcepkgs[seedname].add(src)

        self._all_srcs.add(src)
        self._build_srcs[seedname].add(src)

        self._add_dependency_tree(seedname, pkg,
                                  self._sources[src]["Build-Depends"],
                                  build_depend=True)
        self._add_dependency_tree(seedname, pkg,
                                  self._sources[src]["Build-Depends-Indep"],
                                  build_depend=True)

    def _rescue_includes(self, seedname, rescue_seedname, build_tree):
        """Automatically rescue packages matching certain patterns from
        other seeds."""

        if seedname not in self._seeds and seedname != "extra":
            return
        if rescue_seedname not in self._seeds and rescue_seedname != "extra":
            return

        # Find all the source packages.
        rescue_srcs = set()
        if rescue_seedname == "extra":
            rescue_seeds = self._structure.inner_seeds(seedname)
        else:
            rescue_seeds = [rescue_seedname]
        for seed in rescue_seeds:
            if build_tree:
                rescue_srcs |= self._build_srcs[seed]
            else:
                rescue_srcs |= self._not_build_srcs[seed]

        # For each source, add any binaries that match the include/exclude
        # patterns.
        for src in rescue_srcs:
            rescue = [p for p in self._sources[src]["Binaries"]
                        if p in self._packages]
            included = set()
            if (seedname in self._includes and
                rescue_seedname in self._includes[seedname]):
                for include in self._includes[seedname][rescue_seedname]:
                    included |= set(self._filter_packages(rescue, include))
            if (seedname in self._excludes and
                rescue_seedname in self._excludes[seedname]):
                for exclude in self._excludes[seedname][rescue_seedname]:
                    included -= set(self._filter_packages(rescue, exclude))
            for pkg in included:
                if pkg in self._all:
                    continue
                for lesserseed in self._structure.strictly_outer_seeds(seedname):
                    if pkg in self._seed[lesserseed]:
                        self._seed[lesserseed].remove(pkg)
                        logging.warning("Promoted %s from %s to %s due to "
                                        "%s-Includes",
                                        pkg, lesserseed, seedname,
                                        rescue_seedname.title())
                        break
                logging.debug("Rescued %s from %s to %s", pkg,
                              rescue_seedname, seedname)
                if build_tree:
                    self._build_depends[seedname].add(pkg)
                else:
                    self._depends[seedname].add(pkg)
                self._add_package(seedname, pkg, "Rescued from %s" % src,
                                  build_tree=build_tree)

    # Accessors.
    # ----------

    def get_source(self, pkg):
        return self._packages[pkg]["Source"]

    def is_essential(self, pkg):
        return self._packages[pkg].get("Essential", "no") == "yes"

    def get_seed(self, seedname):
        for pkg in self._seed[seedname]:
            yield pkg

    def get_seed_recommends(self, seedname):
        for pkg in self._seedrecommends[seedname]:
            yield pkg

    def get_depends(self, seedname):
        for pkg in self._depends[seedname]:
            yield pkg

    def get_build_depends(self, seedname):
        for pkg in self._build_depends[seedname]:
            yield pkg

    def get_supported(self):
        return self._supported

    def get_all(self):
        for pkg in self._all:
            yield pkg

    # Methods for writing output to files.
    # ------------------------------------

    def _write_list(self, whyname, filename, pkgset):
        pkglist = list(pkgset)
        pkglist.sort()

        pkg_len = len("Package")
        src_len = len("Source")
        why_len = len("Why")
        mnt_len = len("Maintainer")

        for pkg in pkglist:
            _pkg_len = len(pkg)
            if _pkg_len > pkg_len: pkg_len = _pkg_len

            _src_len = len(self._packages[pkg]["Source"])
            if _src_len > src_len: src_len = _src_len

            _why_len = len(self._why[whyname][pkg][0])
            if _why_len > why_len: why_len = _why_len

            _mnt_len = len(self._packages[pkg]["Maintainer"])
            if _mnt_len > mnt_len: mnt_len = _mnt_len

        size = 0
        installed_size = 0

        pkglist.sort()
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
                       why_len, self._why[whyname][pkg][0],
                       mnt_len, self._packages[pkg]["Maintainer"],
                       self._packages[pkg]["Size"],
                       self._packages[pkg]["Installed-Size"])
            print >>f, ("-" * (pkg_len + src_len + why_len + mnt_len + 9)) \
                  + "-+-" + ("-" * 15) + "-+-" + ("-" * 15) + "-"
            print >>f, "%*s | %15d | %15d" % \
                  ((pkg_len + src_len + why_len + mnt_len + 9), "",
                   size, installed_size)

    def _write_source_list(self, filename, srcset):
        srclist = list(srcset)
        srclist.sort()

        src_len = len("Source")
        mnt_len = len("Maintainer")

        for src in srclist:
            _src_len = len(src)
            if _src_len > src_len: src_len = _src_len

            _mnt_len = len(self._sources[src]["Maintainer"])
            if _mnt_len > mnt_len: mnt_len = _mnt_len

        srclist.sort()
        with codecs.open(filename, "w", "utf8", "replace") as f:
            fmt = "%-*s | %-*s"

            print >>f, fmt % (src_len, "Source", mnt_len, "Maintainer")
            print >>f, ("-" * src_len) + "-+-" + ("-" * mnt_len) + "-"
            for src in srclist:
                print >>f, fmt % (src_len, src, mnt_len,
                                  self._sources[src]["Maintainer"])

    def write_full_list(self, filename, seedname):
        self._write_list(seedname, filename,
                         set(self._seed[seedname]) |
                         set(self._seedrecommends[seedname]) |
                         set(self._depends[seedname]))

    def write_seed_list(self, filename, seedname):
        self._write_list(seedname, filename, self._seed[seedname])

    def write_seed_recommends_list(self, filename, seedname):
        self._write_list(seedname, filename, self._seedrecommends[seedname])

    def write_depends_list(self, filename, seedname):
        self._write_list(seedname, filename, self._depends[seedname])

    def write_build_depends_list(self, filename, seedname):
        self._write_list(seedname, filename, self._build_depends[seedname])

    def write_sources_list(self, filename, seedname):
        self._write_source_list(filename, self._sourcepkgs[seedname])

    def write_build_sources_list(self, filename, seedname):
        self._write_source_list(filename, self._build_sourcepkgs[seedname])

    def write_all_list(self, filename):
        all_bins = set()

        for seedname in self._structure.names:
            if seedname == "extra":
                continue

            all_bins.update(self._seed[seedname])
            all_bins.update(self._seedrecommends[seedname])
            all_bins.update(self._depends[seedname])
            all_bins.update(self._build_depends[seedname])

        self._write_list("all", filename, all_bins)

    def write_all_source_list(self, filename):
        all_srcs = set()

        for seedname in self._structure.names:
            if seedname == "extra":
                continue

            all_srcs.update(self._sourcepkgs[seedname])
            all_srcs.update(self._build_sourcepkgs[seedname])

        self._write_source_list(filename, all_srcs)

    def write_supported_list(self, filename):
        sup_bins = set()

        for seedname in self._structure.names:
            if seedname == "extra":
                continue

            if seedname == self._supported:
                sup_bins.update(self._seed[seedname])
                sup_bins.update(self._seedrecommends[seedname])
                sup_bins.update(self._depends[seedname])

            # Only include those build-dependencies that aren't already in
            # the dependency outputs for inner seeds of supported. This
            # allows supported+build-depends to be usable as an "everything
            # else" output.
            build_depends = dict.fromkeys(self._build_depends[seedname], True)
            for seed in self._structure.inner_seeds(self._supported):
                build_depends.update(dict.fromkeys(self._seed[seed], False))
                build_depends.update(dict.fromkeys(self._seedrecommends[seed], False))
                build_depends.update(dict.fromkeys(self._depends[seed], False))
            sup_bins.update([k for (k, v) in build_depends.iteritems() if v])

        self._write_list("all", filename, sup_bins)

    def write_supported_source_list(self, filename):
        sup_srcs = set()

        for seedname in self._structure.names:
            if seedname == "extra":
                continue

            if seedname == self._supported:
                sup_srcs.update(self._sourcepkgs[seedname])

            # Only include those build-dependencies that aren't already in
            # the dependency outputs for inner seeds of supported. This
            # allows supported+build-depends to be usable as an "everything
            # else" output.
            build_sourcepkgs = dict.fromkeys(self._build_sourcepkgs[seedname], True)
            for seed in self._structure.inner_seeds(self._supported):
                build_sourcepkgs.update(dict.fromkeys(self._sourcepkgs[seed], False))
            sup_srcs.update([k for (k, v) in build_sourcepkgs.iteritems() if v])

        self._write_source_list(filename, sup_srcs)

    def write_all_extra_list(self, filename):
        self._write_list("all", filename, self._all)

    def write_all_extra_source_list(self, filename):
        self._write_source_list(filename, self._all_srcs)

    def write_rdepend_list(self, filename, pkg):
        with open(filename, "w") as f:
            print >>f, pkg
            self._write_rdepend_list(f, pkg, "", done=set())

    def _write_rdepend_list(self, f, pkg, prefix, stack=None, done=None):
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

        for seed in self._seeds:
            if pkg in self._seed[seed]:
                print >>f, prefix + "*", seed.title(), "seed"

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
                self._write_rdepend_list(f, dep, prefix + extra, stack, done)

    def write_provides_list(self, filename):
        provides = self._pkgprovides.keys()
        provides.sort()

        with open(filename, "w") as f:
            for prov in provides:
                print >>f, prov

                provlist = list(self._pkgprovides[prov])
                provlist.sort()
                for pkg in provlist:
                    print >>f, "\t%s" % (pkg,)
                print >>f

    def write_blacklisted(self, filename):
        """Write out the list of blacklisted packages we encountered"""

        with open(filename, 'w') as fh:
            sorted_blacklisted = list(self._blacklisted)
            sorted_blacklisted.sort()
            for pkg in sorted_blacklisted:
                blacklist = self._blacklist[pkg]
                fh.write('%s\t%s\n' % (pkg, blacklist))


def pretty_logging():
    logging.addLevelName(logging.DEBUG, '  ')
    logging.addLevelName(Germinator.PROGRESS, '')
    logging.addLevelName(logging.INFO, '* ')
    logging.addLevelName(logging.WARNING, '! ')
    logging.addLevelName(logging.ERROR, '? ')
