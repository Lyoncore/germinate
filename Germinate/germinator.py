# -*- coding: UTF-8 -*-
"""Expand seeds into dependency-closed lists of packages."""

# Copyright (c) 2004, 2005 Canonical Ltd.
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
# Software Foundation, 59 Temple Place - Suite 330, Boston, MA
# 02111-1307, USA.

import apt_pkg
import urllib
import re
import logging

PROGRESS = 15

logging.addLevelName(logging.DEBUG, '  ')
logging.addLevelName(PROGRESS, '')
logging.addLevelName(logging.INFO, '* ')
logging.addLevelName(logging.WARNING, '! ')
logging.addLevelName(logging.ERROR, '? ')

class Germinator:
    def __init__(self):
        self.packages = {}
        self.packagetype = {}
        self.provides = {}
        self.sources = {}
        self.pruned = {}

        self.seeds = []
        self.seed = {}
        self.seedinherit = {}
        self.substvars = {}
        self.depends = {}
        self.build_depends = {}

        self.sourcepkgs = {}
        self.build_sourcepkgs = {}

        self.pkgprovides = {}

        self.all = []
        self.build = {}
        self.not_build = {}

        self.all_srcs = []
        self.build_srcs = {}
        self.not_build_srcs = {}

        self.why = {}
        self.why["all"] = {}

        self.hints = {}

        self.blacklist = {}
        self.blacklisted = []

        self.di_kernel_versions = {}

    def debug(self, msg, *args, **kwargs):
        logging.debug(msg, *args, **kwargs)

    def progress(self, msg, *args, **kwargs):
        logging.getLogger().log(PROGRESS, msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        logging.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        logging.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        logging.error(msg, *args, **kwargs)

    def parseStructure(self, f):
        """Parse a seed structure file. This is an ordered sequence of lines
        as follows:

        SEED:[ INHERITED]

        INHERITED is a space-separated list of seeds from which SEED
        inherits. For example, "ship: base desktop" indicates that packages
        in the "ship" seed may depend on packages in the "base" or "desktop"
        seeds without requiring those packages to appear in the "ship"
        output. INHERITED may be empty.

        The lines should be topologically sorted with respect to
        inheritance, with inherited-from seeds at the start.

        Returns (ordered list of seed names, dict of SEED -> INHERITED)."""
        seednames = []
        seedinherit = {}

        for line in f:
            words = line.split()
            if words[0].endswith(':'):
                seed = words[0][:-1]
                seednames.append(seed)
                seedinherit[seed] = list(words[1:])
            else:
                self.error("Unparseable seed structure entry: %s", line)

        return (seednames, seedinherit)

    def parseHints(self, f):
        """Parse a hints file."""
        for line in f:
            if line.startswith("#") or not len(line.rstrip()): continue

            words = line.rstrip().split(None)
            if len(words) != 2:
                continue

            self.hints[words[1]] = words[0]
        f.close()

    def parsePackages(self, f, pkgtype):
        """Parse a Packages file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            pkg = p.Section["Package"]
            self.packages[pkg] = {}
            self.packagetype[pkg] = pkgtype
            self.pruned[pkg] = []

            self.packages[pkg]["Maintainer"] = \
                unicode(p.Section.get("Maintainer", ""), "utf8", "replace")

            for field in "Pre-Depends", "Depends", "Recommends", "Suggests":
                value = p.Section.get(field, "")
                self.packages[pkg][field] = apt_pkg.ParseDepends(value)

            for field in "Size", "Installed-Size":
                value = p.Section.get(field, "0")
                self.packages[pkg][field] = int(value)

            src = p.Section.get("Source", pkg)
            idx = src.find("(")
            if idx != -1:
                src = src[:idx].strip()
            self.packages[pkg]["Source"] = src

            provides = apt_pkg.ParseDepends(p.Section.get("Provides", ""))
            for prov in provides:
                if prov[0][0] not in self.provides:
                    self.provides[prov[0][0]] = []
                    if prov[0][0] in self.packages:
                        self.provides[prov[0][0]].append(prov[0][0])
                self.provides[prov[0][0]].append(pkg)
            self.packages[pkg]["Provides"] = provides

            if pkg in self.provides:
                self.provides[pkg].append(pkg)

            self.packages[pkg]["Kernel-Version"] = p.Section.get("Kernel-Version", "")
        f.close()

    def parseSources(self, f):
        """Parse a Sources file and get the information we need."""
        p = apt_pkg.ParseTagFile(f)
        while p.Step() == 1:
            src = p.Section["Package"]
            self.sources[src] = {}

            self.sources[src]["Maintainer"] = \
                unicode(p.Section.get("Maintainer", ""), "utf8", "replace")
            self.sources[src]["IPv6"] = "Unknown"

            for field in "Build-Depends", "Build-Depends-Indep":
                value = p.Section.get(field, "")
                self.sources[src][field] = apt_pkg.ParseSrcDepends(value)

            binaries = apt_pkg.ParseDepends(p.Section.get("Binary", src))
            self.sources[src]["Binaries"] = [ bin[0][0] for bin in binaries ]

        f.close()

    def parseIPv6(self, f):
        """Parse the IPv6 dailydump file and get the information we need."""
        for line in f:
            (src, info) = line.split(None, 1)
            if src in self.sources:
                self.sources[src]["IPv6"] = info.strip()
        f.close()

    def parseBlacklist(self, f):
        """Parse a blacklist file, used to indicate unwanted packages"""

        name = ''

        for line in f:
            line = line.strip()
            if line.startswith('# blacklist: '):
                name = line[13:]
            elif not line or line.startswith('#'):
                continue
            else:
                self.blacklist[line] = name
        f.close()

    def writeBlacklisted(self, filename):
        """Write out the list of blacklisted packages we encountered"""

        fh = open(filename, 'w')
        for pkg in self.blacklisted:
            blacklist = self.blacklist[pkg]
            fh.write('%s\t%s\n' % (pkg, blacklist))
        fh.close()

    def newSeed(self, seedname, seedinherit):
        self.seeds.append(seedname)
        self.seed[seedname] = []
        self.seedinherit[seedname] = seedinherit
        self.depends[seedname] = []
        self.build_depends[seedname] = []
        self.sourcepkgs[seedname] = []
        self.build_sourcepkgs[seedname] = []
        self.build[seedname] = []
        self.not_build[seedname] = []
        self.build_srcs[seedname] = []
        self.not_build_srcs[seedname] = []
        self.why[seedname] = {}
        self.di_kernel_versions[seedname] = []

    def substituteSeedVars(self, pkg):
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
                if name in self.substvars:
                    # Duplicate substituted once for each available substvar
                    # expansion.
                    newsubst = []
                    for value in self.substvars[name]:
                        for substpieces in substituted:
                            newsubstpieces = list(substpieces)
                            newsubstpieces.append(value)
                            newsubst.append(newsubstpieces)
                    substituted = newsubst
                else:
                    self.error("Undefined seed substvar: %s", name)
            else:
                for substpieces in substituted:
                    substpieces.append(piece)

        substpkgs = []
        for substpieces in substituted:
            substpkgs.append("".join(substpieces))
        return substpkgs

    def innerSeeds(self, seedname):
        """Return this seed and the seeds from which it inherits."""
        innerseeds = list(self.seedinherit[seedname])
        innerseeds.append(seedname)
        return innerseeds

    def strictlyOuterSeeds(self, seedname):
        """Return the seeds that inherit from this seed."""
        outerseeds = []
        for seed in self.seeds:
            if seedname in self.seedinherit[seed]:
                outerseeds.append(seed)
        return outerseeds

    def alreadySeeded(self, seedname, pkg):
        """Has pkg already been seeded in this seed or in one from
        which we inherit?"""

        for seed in self.innerSeeds(seedname):
            if pkg in self.seed[seed]:
                return True

        return False

    def plantSeed(self, seedurl, release, arch, seedname, seedinherit):
        """Add a seed."""
        if seedname in self.seeds:
            return

        self.newSeed(seedname, seedinherit)
        seedpkgs = []

        self.progress("Downloading %s list ...", seedname)
        url = seedurl + release + "/" + seedname
        urlopener = urllib.FancyURLopener()
        urlopener.addheader('Cache-Control', 'no-cache')
        urlopener.addheader('Pragma', 'no-cache')
        f = urlopener.open(url)
        for line in f:
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
                    self.warning("Allowing d-i kernel versions: %s", values)
                    self.di_kernel_versions[seedname].extend(values)
                self.substvars[name] = values
                continue

            archspec = []
            startarchspec = pkg.find("[")
            if startarchspec != -1:
                endarchspec = pkg.find("]")
                if endarchspec == -1:
                    self.error("Broken architecture specification: %s", pkg)
                else:
                    archspec = pkg[startarchspec + 1:endarchspec].split()
                    pkg = pkg[:startarchspec - 1]
                    if arch not in archspec:
                        continue

            pkg = pkg.split()[0]

            seedpkgs.extend(self.substituteSeedVars(pkg))

        for pkg in seedpkgs:
            if pkg in self.hints and self.hints[pkg] != seedname:
                self.warning("Taking the hint: %s", pkg)
                continue

            if pkg in self.packages:
                # Ordinary package
                if self.alreadySeeded(seedname, pkg):
                    self.warning("Duplicated seed: %s", pkg)
                elif seedname in self.pruned[pkg]:
                    self.warning("Pruned %s from %s", pkg, seedname)
                else:
                    self.seed[seedname].append(pkg)

            elif pkg in self.provides:
                # Virtual package, include everything
                self.info("Virtual %s package: %s", seedname, pkg)
                for vpkg in self.provides[pkg]:
                    if self.alreadySeeded(seedname, vpkg):
                        pass
                    elif seedname in self.pruned[vpkg]:
                        pass
                    else:
                        self.debug("- %s", vpkg)
                        self.seed[seedname].append(vpkg)

            else:
                # No idea
                self.error("Unknown %s package: %s", seedname, pkg)
        f.close()

        for pkg in self.hints:
            if self.hints[pkg] == seedname and not self.alreadySeeded(seedname, pkg):
                if pkg in self.packages:
                    self.seed[seedname].append(pkg)
                else:
                    self.error("Unknown hinted package: %s", pkg)

    def prune(self):
        """Remove packages that are inapplicable for some reason, such as
           being for the wrong d-i kernel version."""
        for pkg in self.packages:
            kernver = self.packages[pkg]["Kernel-Version"]
            if kernver != "":
                for seed in self.seeds:
                    if kernver not in self.di_kernel_versions[seed]:
                        self.pruned[pkg].append(seed)

    def grow(self):
        """Grow the seeds."""
        for seedname in self.seeds:
            self.progress("Resolving %s dependencies ...", seedname)
            for pkg in self.seed[seedname]:
                self.addPackage(seedname, pkg, seedname.title() + " seed")

    def addExtras(self):
        """Add packages generated by the sources but not in any seed."""
        self.newSeed("extra", self.seeds)

        self.progress("Identifying extras ...")
        for srcname in self.all_srcs:
            for pkg in self.sources[srcname]["Binaries"]:
                if pkg not in self.packages:
                    continue
                if pkg in self.all:
                    continue

                if pkg in self.hints and self.hints[pkg] != "extra":
                    self.warning("Taking the hint: %s", pkg)
                    continue

                self.seed["extra"].append(pkg)
                self.addPackage("extra", pkg, "Generated by " + srcname,
                                second_class=True)

    def allowedDependency(self, pkg, depend, seedname, build_depend):
        """Is pkg allowed to satisfy a (build-)dependency using depend
           within seedname? Note that depend must be a real package.
           
           If seedname is None, check whether the (build-)dependency is
           allowed within any seed."""
        if depend not in self.packages:
            self.warning("allowedDependency called with virtual package %s", depend)
            return False
        if seedname is not None and seedname in self.pruned[depend]:
            return False
        if build_depend:
            if self.packagetype[depend] == "deb":
                return True
            else:
                return False
        else:
            if self.packagetype[pkg] == self.packagetype[depend]:
                return True
            else:
                return False

    def addReverse(self, pkg, field, rdep):
        """Add a reverse dependency entry."""
        if "Reverse-Depends" not in self.packages[pkg]:
            self.packages[pkg]["Reverse-Depends"] = {}
        if field not in self.packages[pkg]["Reverse-Depends"]:
            self.packages[pkg]["Reverse-Depends"][field] = []

        self.packages[pkg]["Reverse-Depends"][field].append(rdep)

    def reverseDepends(self):
        """Calculate the reverse dependency relationships."""
        for pkg in self.all:
            for field in "Pre-Depends", "Depends":
                for deplist in self.packages[pkg][field]:
                    for dep in deplist:
                        if dep[0] in self.all and \
                           self.allowedDependency(pkg, dep[0], None, False):
                            self.addReverse(dep[0], field, pkg)

        for src in self.all_srcs:
            for field in "Build-Depends", "Build-Depends-Indep":
                for deplist in self.sources[src][field]:
                    for dep in deplist:
                        if dep[0] in self.all and \
                           self.allowedDependency(src, dep[0], None, True):
                            self.addReverse(dep[0], field, src)

        for pkg in self.all:
            if "Reverse-Depends" not in self.packages[pkg]:
                continue

            for field in ("Pre-Depends", "Depends",
                          "Build-Depends", "Build-Depends-Indep"):
                if field not in self.packages[pkg]["Reverse-Depends"]:
                    continue

                self.packages[pkg]["Reverse-Depends"][field].sort()

    def alreadySatisfied(self, seedname, pkg, depend, build_depend=False, with_build=False):
        """Work out whether a dependency has already been satisfied."""
        if depend in self.provides:
            trylist = [ d for d in self.provides[depend]
                        if d in self.packages and self.allowedDependency(pkg, d, seedname, build_depend) ]
        elif depend in self.packages and \
             self.allowedDependency(pkg, depend, seedname, build_depend):
            trylist = [ depend ]
        else:
            return False

        for trydep in trylist:
            if with_build:
                for seed in self.innerSeeds(seedname):
                    if trydep in self.build[seed]:
                        return True
            else:
                for seed in self.innerSeeds(seedname):
                    if trydep in self.not_build[seed]:
                        return True
            if trydep in self.seed[seedname]:
                return True
        else:
            return False

    def addDependency(self, seedname, pkg, depend, build_depend,
                      second_class, build_tree):
        """Add a single dependency. Returns True if a dependency was added,
           otherwise False."""
        if depend in self.packages and \
           self.allowedDependency(pkg, depend, seedname, build_depend):
            virtual = None
            trylist = [ depend ]
        elif depend in self.provides:
            virtual = depend
            trylist = [ d for d in self.provides[depend]
                        if d in self.packages and self.allowedDependency(pkg, d, seedname, build_depend) ]
        else:
            self.error("Unknown dependency %s by %s", depend, pkg)
            return False

        # Last ditch effort to satisfy this by promoting lesser seeds to
        # higher dependencies
        found = False
        for trydep in trylist:
            for lesserseed in self.strictlyOuterSeeds(seedname):
                if trydep in self.seed[lesserseed]:
                    if second_class:
                        # I'll get you next time, Gadget!
                        return False
                    self.seed[lesserseed].remove(trydep)
                    self.warning("Promoted %s from %s to %s to satisfy %s",
                                 trydep, lesserseed, seedname, pkg)

                    depend = trydep
                    found = True
                    break
            if found: break

        dependlist = [depend]
        if virtual is not None and not found:
            reallist = [ d for d in self.provides[virtual]
                         if d in self.packages and self.allowedDependency(pkg, d, seedname, build_depend) ]
            if len(reallist):
                depend = reallist[0]
                # If this one was a d-i kernel module, pick all the modules
                # for other allowed kernel versions too.
                if self.packages[depend]["Kernel-Version"] != "":
                    dependlist = [ d for d in reallist
                                   if self.packages[d]["Kernel-Version"] in self.di_kernel_versions[seedname] ]
                else:
                    dependlist = [depend]
                self.info("Chose %s out of %s to satisfy %s",
                          ", ".join(dependlist), virtual, pkg)
            else:
                self.error("Nothing to choose out of %s to satisfy %s",
                           virtual, pkg)
                return False

        if build_tree:
            for dep in dependlist:
                self.build_depends[seedname].append(dep)
            if build_depend:
                why = self.packages[pkg]["Source"] + " (Build-Depend)"
            else:
                why = pkg
        else:
            for dep in dependlist:
                self.depends[seedname].append(dep)
            why = pkg

        for dep in dependlist:
            self.addPackage(seedname, dep, why, build_tree, second_class)

        return True

    def addDependencyTree(self, seedname, pkg, depends,
                          build_depend=False,
                          second_class=False,
                          build_tree=False):
        """Add a package's dependency tree."""
        if build_depend: build_tree = True
        if build_tree: second_class = True
        for deplist in depends:
            for dep in deplist:
                if self.alreadySatisfied(seedname, pkg, dep[0], build_depend, second_class):
                    break
            else:
                for dep in deplist:
                    if self.addDependency(seedname, pkg, dep[0], build_depend,
                                          second_class, build_tree):
                        if len(deplist) > 1:
                            self.info("Chose %s to satisfy %s", dep[0], pkg)
                        break
                else:
                    if len(deplist) > 1:
                        self.error("Nothing to choose to satisfy %s", pkg)

    def rememberWhy(self, seedname, pkg, why, build_tree=False):
        """Remember why this package was added to the output for this seed."""
        if pkg in self.why[seedname]:
            (old_why, old_build_tree) = self.why[seedname][pkg];
            # Reasons from the dependency tree beat reasons from the
            # build-dependency tree; but pick the first of either type that
            # we see.
            if not old_build_tree:
                return
            if build_tree:
                return

        self.why[seedname][pkg] = (why, build_tree)

    def addPackage(self, seedname, pkg, why,
                   second_class=False,
                   build_tree=False):
        """Add a package and its dependency trees."""
        if seedname in self.pruned[pkg]:
            self.warning("Pruned %s from %s", pkg, seedname)
            return
        if build_tree: second_class=True

        if pkg not in self.all:
            self.all.append(pkg)
        elif not build_tree:
            for buildseed in self.innerSeeds(seedname):
                if pkg in self.build_depends[buildseed]:
                    self.build_depends[buildseed].remove(pkg)

        for seed in self.innerSeeds(seedname):
            if pkg in self.build[seed]:
                break
        else:
            self.build[seedname].append(pkg)

        if not build_tree:
            for seed in self.innerSeeds(seedname):
                if pkg in self.not_build[seed]:
                    break
            else:
                self.not_build[seedname].append(pkg)

        # Remember why the package was added to the output for this seed.
        # Also remember a reason for "all" too, so that an aggregated list
        # of all selected packages can be constructed easily.
        self.rememberWhy(seedname, pkg, why, build_tree)
        self.rememberWhy("all", pkg, why, build_tree)

        for prov in self.packages[pkg]["Provides"]:
            if prov[0][0] not in self.pkgprovides:
                self.pkgprovides[prov[0][0]] = []
            if pkg not in self.pkgprovides[prov[0][0]]:
                self.pkgprovides[prov[0][0]].append(pkg)

        self.addDependencyTree(seedname, pkg,
                               self.packages[pkg]["Pre-Depends"],
                               second_class=second_class,
                               build_tree=build_tree)

        self.addDependencyTree(seedname, pkg, self.packages[pkg]["Depends"],
                               second_class=second_class,
                               build_tree=build_tree)

        src = self.packages[pkg]["Source"]
        if src not in self.sources:
            self.error("Missing source package: %s (for %s)", src, pkg)
            return

        if second_class:
            for seed in self.innerSeeds(seedname):
                if src in self.build_srcs[seed]:
                    return
        else:
            for seed in self.innerSeeds(seedname):
                if src in self.not_build_srcs[seed]:
                    return

        if build_tree:
            self.build_sourcepkgs[seedname].append(src)
            if src in self.blacklist and src not in self.blacklisted:
                self.blacklisted.append(src)

        else:
            if src in self.all_srcs:
                for buildseed in self.seeds:
                    if src in self.build_sourcepkgs[buildseed]:
                        self.build_sourcepkgs[buildseed].remove(src)

            self.not_build_srcs[seedname].append(src)
            self.sourcepkgs[seedname].append(src)

        if src not in self.all_srcs:
            self.all_srcs.append(src)
        self.build_srcs[seedname].append(src)

        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends"],
                               build_depend=True)
        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends-Indep"],
                               build_depend=True)
