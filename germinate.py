#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# arch-tag: e7f7b26b-95bc-4432-86e6-832a7cc5ac01
"""Update list files from the Wiki.
"""

# Copyright (c) 2004 Canonical Ltd.
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
import gzip
import os
import shutil
import sys
import urllib
import string
import getopt
import re


# Where do we get up-to-date seeds from?
SEEDS = "http://people.ubuntu.com/~cjwatson/seeds/"
RELEASE = "hoary"

# If we need to download Packages.gz and/or Sources.gz, where do we get
# them from?
MIRROR = "http://archive.ubuntu.com/ubuntu/"
DIST = ["hoary"]
COMPONENTS = ["main"]
ARCH = "i386"

CHECK_IPV6 = False

# If we need to download a new IPv6 dump, where do we get it from?
IPV6DB = "http://debdev.fabbione.net/stat/"

class Germinator:
    def __init__(self):
        self.packages = {}
        self.packagetype = {}
        self.provides = {}
        self.sources = {}
        self.pruned = {}

        self.seeds = []
        self.seed = {}
        self.substvars = {}
        self.depends = {}
        self.build_depends = {}

        self.sourcepkgs = {}
        self.build_sourcepkgs = {}

        self.pkgprovides = {}

        self.all = []
        self.not_build = []

        self.all_srcs = []
        self.not_build_srcs = []

        self.why = {}
        self.seeded = []

        self.hints = {}

        self.blacklist = {}
        self.blacklisted = []

        self.di_kernel_versions = []

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
            self.pruned[pkg] = False

            self.packages[pkg]["Maintainer"] = p.Section.get("Maintainer", "")

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

            self.sources[src]["Maintainer"] = p.Section.get("Maintainer", "")
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

    def newSeed(self, seedname):
        self.seeds.append(seedname)
        self.seed[seedname] = []
        self.depends[seedname] = []
        self.build_depends[seedname] = []
        self.sourcepkgs[seedname] = []
        self.build_sourcepkgs[seedname] = []

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
                    print "? Undefined seed substvar:", name
            else:
                for substpieces in substituted:
                    substpieces.append(piece)

        substpkgs = []
        for substpieces in substituted:
            substpkgs.append(string.join(substpieces, ""))
        return substpkgs

    def plantSeed(self, seedname):
        """Add a seed."""
        if seedname in self.seeds:
            return

        self.newSeed(seedname)
        seedpkgs = []

        print "Downloading", seedname, "list ..."
        url = SEEDS + RELEASE + "/" + seedname
        f = urllib.urlopen(url)
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
                values = value.strip(" ").split()
                if name == "kernel-version":
                    # Allows us to pick the right modules later
                    print "! Allowing d-i kernel versions:", values
                    self.di_kernel_versions.extend(values)
                self.substvars[name] = values
                continue

            archspec = []
            startarchspec = pkg.find("[")
            if startarchspec != -1:
                endarchspec = pkg.find("]")
                if endarchspec == -1:
                    print "? Broken architecture specification:", pkg
                else:
                    archspec = pkg[startarchspec + 1:endarchspec].split()
                    pkg = pkg[:startarchspec - 1]
                    if ARCH not in archspec:
                        continue

            if pkg.find(" ") != -1:
                pkg = pkg[:pkg.find(" ")]

            seedpkgs.extend(self.substituteSeedVars(pkg))

        for pkg in seedpkgs:
            if pkg in self.hints and self.hints[pkg] != seedname:
                print "! Taking the hint:", pkg
                continue

            if pkg in self.packages:
                # Ordinary package
                if pkg not in self.seeded:
                    self.seed[seedname].append(pkg)
                    self.seeded.append(pkg)
                else:
                    print "! Duplicated seed:", pkg

            elif pkg in self.provides:
                # Virtual package, include everything
                print "* Virtual", seedname, "package:", pkg
                for vpkg in self.provides[pkg]:
                    if vpkg not in self.seeded:
                        print "  - " + vpkg
                        self.seed[seedname].append(vpkg)
                        self.seeded.append(vpkg)

            else:
                # No idea
                print "? Unknown", seedname, "package:", pkg
        f.close()

        for pkg in self.hints:
            if self.hints[pkg] == seedname and pkg not in self.seeded:
                if pkg in self.packages:
                    self.seed[seedname].append(pkg)
                    self.seeded.append(pkg)
                else:
                    print "? Unknown hinted package:", pkg

    def prune(self):
        """Remove packages that are inapplicable for some reason, such as
           being for the wrong d-i kernel version."""
        for pkg in self.packages:
            kernver = self.packages[pkg]["Kernel-Version"]
            if kernver != "" and kernver not in self.di_kernel_versions:
                self.pruned[pkg] = True

    def grow(self):
        """Grow the seeds."""
        for seedname in self.seeds:
            print "Resolving", seedname, "dependencies ..."
            for pkg in self.seed[seedname]:
                self.addPackage(seedname, pkg, seedname.title() + " seed")

    def addExtras(self):
        """Add packages generated by the sources but not in any seed."""
        self.newSeed("extra")

        print "Identifying extras ..."
        for srcname in self.all_srcs:
            for pkg in self.sources[srcname]["Binaries"]:
                if pkg not in self.packages:
                    continue
                if self.pruned[pkg]:
                    continue
                if pkg in self.all:
                    continue

                if pkg in self.hints and self.hints[pkg] != "extra":
                    print "! Taking the hint:", pkg
                    continue

                self.seed["extra"].append(pkg)
                self.addPackage("extra", pkg, "Generated by " + srcname,
                                second_class=True)

    def allowedDependency(self, pkg, depend, build_depend):
        """Is pkg allowed to satisfy a (build-)dependency using depend?
           Note that depend must be a real package."""
        if depend not in self.packages:
            print "! allowedDependency called with virtual package", depend
            return False
        if self.pruned[depend]:
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
                           self.allowedDependency(pkg, dep[0], False):
                            self.addReverse(dep[0], field, pkg)

        for src in self.all_srcs:
            for field in "Build-Depends", "Build-Depends-Indep":
                for deplist in self.sources[src][field]:
                    for dep in deplist:
                        if dep[0] in self.all and \
                           self.allowedDependency(src, dep[0], True):
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
                        if d in self.packages and self.allowedDependency(pkg, d, build_depend) ]
        elif depend in self.packages and \
             self.allowedDependency(pkg, depend, build_depend):
            trylist = [ depend ]
        else:
            return False

        for trydep in trylist:
            if with_build:
                if trydep in self.all:
                    return True
            else:
                if trydep in self.not_build:
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
           self.allowedDependency(pkg, depend, build_depend):
            virtual = None
            trylist = [ depend ]
        elif depend in self.provides:
            virtual = depend
            trylist = [ d for d in self.provides[depend]
                        if d in self.packages and self.allowedDependency(pkg, d, build_depend) ]
        else:
            print "? Unknown dependency", depend, "by", pkg
            return False

        # Last ditch effort to satisfy this by promoting lesser seeds to
        # higher dependencies
        found = False
        for trydep in trylist:
            seedidx = self.seeds.index(seedname) + 1
            for lesserseed in self.seeds[seedidx:len(self.seeds)]:
                if trydep in self.seed[lesserseed]:
                    if second_class:
                        # I'll get you next time, Gadget!
                        return False
                    self.seed[lesserseed].remove(trydep)
                    print "! Promoted", trydep, "from", lesserseed, "to", \
                          seedname, "to satisfy", pkg

                    depend = trydep
                    found = True
                    break
            if found: break

        dependlist = [depend]
        if virtual is not None and not found:
            reallist = [ d for d in self.provides[virtual]
                         if d in self.packages and self.allowedDependency(pkg, d, build_depend) ]
            if len(reallist):
                depend = reallist[0]
                # If this one was a d-i kernel module, pick all the modules
                # for other allowed kernel versions too.
                if self.packages[depend]["Kernel-Version"] != "":
                    dependlist = [ d for d in reallist
                                   if self.packages[d]["Kernel-Version"] in self.di_kernel_versions ]
                else:
                    dependlist = [depend]
                print "* Chose", string.join(dependlist, ", "), "out of", virtual, "to satisfy", pkg
            else:
                print "? Nothing to choose out of", virtual, "to satisfy", pkg
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
                            print "* Chose", dep[0], "to satisfy", pkg
                        break
                else:
                    if len(deplist) > 1:
                        print "? Nothing to choose to satisfy", pkg

    def addPackage(self, seedname, pkg, why,
                   second_class=False,
                   build_tree=False):
        """Add a package and its dependency trees."""
        if self.pruned[pkg]:
            print "! Pruned seed package:", pkg
            return
        if build_tree: second_class=True
        if pkg not in self.all:
            self.all.append(pkg)
        elif not build_tree:
            for buildseed in self.seeds:
                if pkg in self.build_depends[buildseed]:
                    self.build_depends[buildseed].remove(pkg)
        if pkg not in self.not_build and not build_tree:
            self.not_build.append(pkg)

        self.why[pkg] = why

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
            print "? Missing source package:", src, "(for", pkg + ")"
            return

        if second_class and src in self.all_srcs:
            return
        elif src in self.not_build_srcs:
            return

        if build_tree:
            self.all_srcs.append(src)
            self.build_sourcepkgs[seedname].append(src)
            if src in self.blacklist and src not in self.blacklisted:
                self.blacklisted.append(src)

        else:
            if src in self.all_srcs:
                for buildseed in self.seeds:
                    if src in self.build_sourcepkgs[buildseed]:
                        self.build_sourcepkgs[buildseed].remove(src)
            else:
                self.all_srcs.append(src)

            self.not_build_srcs.append(src)
            self.sourcepkgs[seedname].append(src)

        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends"],
                               build_depend=True)
        self.addDependencyTree(seedname, pkg,
                               self.sources[src]["Build-Depends-Indep"],
                               build_depend=True)


def open_tag_file(filename, dist, component, ftppath):
    """Download an apt tag file if needed, then open it."""
    if os.path.exists(filename):
        return open(filename, "r")

    print "Downloading", filename, "file ..."
    url = MIRROR + "dists/" + dist + "/" + component + "/" + ftppath
    gzip_fn = None
    try:
        gzip_fn = urllib.urlretrieve(url, filename + ".gz")[0]

        # apt_pkg is weird and won't accept GzipFile
        print "Decompressing", filename, "file ..."
        gzip_f = gzip.GzipFile(filename=gzip_fn)
        f = open(filename, "w")
        for line in gzip_f:
            print >>f, line,
        f.close()
        gzip_f.close()
    finally:
        if gzip_fn is not None:
            os.unlink(gzip_fn)

    return open(filename, "r")

def open_ipv6_tag_file(filename):
    """Download the daily IPv6 db dump if needed, and open it."""
    if os.path.exists(filename):
        return open(filename, "r")

    print "Downloading", filename, "file ..."
    url = IPV6DB + filename + ".gz"
    gzip_fn = None
    try:
        gzip_fn = urllib.urlretrieve(url, filename + ".gz")[0]
        print "Decompressing", filename, "file ..."
        gzip_f = gzip.GzipFile(filename=gzip_fn)
        f = open(filename, "w")
        for line in gzip_f:
            print >>f, line,
        f.close()
        gzip_f.close()
    finally:
        if gzip_fn is not None:
            os.unlink(gzip_fn)

    return open(filename, "r")

def open_blacklist(filename):
    try:
        url = SEEDS + RELEASE + "/" + filename
        print "Downloading", url, "..."
        return urllib.urlopen(url)
    except IOError:
        return None

def write_list(filename, g, pkglist):
    pkg_len = len("Package")
    src_len = len("Source")
    why_len = len("Why")
    mnt_len = len("Maintainer")

    for pkg in pkglist:
        _pkg_len = len(pkg)
        if _pkg_len > pkg_len: pkg_len = _pkg_len

        _src_len = len(g.packages[pkg]["Source"])
        if _src_len > src_len: src_len = _src_len

        _why_len = len(g.why[pkg])
        if _why_len > why_len: why_len = _why_len

        _mnt_len = len(g.packages[pkg]["Maintainer"])
        if _mnt_len > mnt_len: mnt_len = _mnt_len

    size = 0
    installed_size = 0

    pkglist.sort()
    f = open(filename, "w")
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
        size += g.packages[pkg]["Size"]
        installed_size += g.packages[pkg]["Installed-Size"]
        print >>f, "%-*s | %-*s | %-*s | %-*s | %15d | %15d" % \
              (pkg_len, pkg,
               src_len, g.packages[pkg]["Source"],
               why_len, g.why[pkg],
               mnt_len, g.packages[pkg]["Maintainer"],
               g.packages[pkg]["Size"],
               g.packages[pkg]["Installed-Size"])
    print >>f, ("-" * (pkg_len + src_len + why_len + mnt_len + 9)) + "-+-" \
          + ("-" * 15) + "-+-" + ("-" * 15) + "-"
    print >>f, "%*s | %15d | %15d" % \
          ((pkg_len + src_len + why_len + mnt_len + 9), "",
           size, installed_size)

    f.close()

def write_source_list(filename, g, srclist):
    global CHECK_IPV6

    src_len = len("Source")
    mnt_len = len("Maintainer")
    ipv6_len = len("IPv6 status")

    for src in srclist:
        _src_len = len(src)
        if _src_len > src_len: src_len = _src_len

        _mnt_len = len(g.sources[src]["Maintainer"])
        if _mnt_len > mnt_len: mnt_len = _mnt_len

        if CHECK_IPV6:
            _ipv6_len = len(g.sources[src]["IPv6"])
            if _ipv6_len > ipv6_len: ipv6_len = _ipv6_len

    srclist.sort()
    f = open(filename, "w")

    format = "%-*s | %-*s"
    header_args = [src_len, "Source", mnt_len, "Maintainer"]
    separator = ("-" * src_len) + "-+-" + ("-" * mnt_len) + "-"
    if CHECK_IPV6:
        format += " | %-*s"
        header_args.extend((ipv6_len, "IPv6 status"))
        separator += "+-" + ("-" * ipv6_len) + "-"

    print >>f, format % tuple(header_args)
    print >>f, separator
    for src in srclist:
        args = [src_len, src, mnt_len, g.sources[src]["Maintainer"]]
        if CHECK_IPV6:
            args.extend((ipv6_len, g.sources[src]["IPv6"]))
        print >>f, format % tuple(args)

    f.close()

def write_rdepend_list(filename, g, pkg):
    f = open(filename, "w")
    print >>f, pkg
    _write_rdepend_list(f, g, pkg, "", done=[])
    f.close()

def _write_rdepend_list(f, g, pkg, prefix, stack=None, done=None):
    if stack is None:
        stack = []
    else:
        stack = list(stack)
        if pkg in stack:
            print >>f, prefix + "! loop"
            return
    stack.append(pkg)

    if done is None:
        done = []
    elif pkg in done:
        print >>f, prefix + "! skipped"
        return
    done.append(pkg)

    for seed in g.seeds:
        if pkg in g.seed[seed]:
            print >>f, prefix + "*", seed.title(), "seed"

    if "Reverse-Depends" not in g.packages[pkg]:
        return

    for field in ("Pre-Depends", "Depends",
                  "Build-Depends", "Build-Depends-Indep"):
        if field not in g.packages[pkg]["Reverse-Depends"]:
            continue

        i = 0
        print >>f, prefix + "*", "Reverse", field + ":"
        for dep in g.packages[pkg]["Reverse-Depends"][field]:
            i += 1
            print >>f, prefix + " +- " + dep
            if field.startswith("Build-"):
                continue

            if i == len(g.packages[pkg]["Reverse-Depends"][field]):
                extra = "    "
            else:
                extra = " |  "
            _write_rdepend_list(f, g, dep, prefix + extra, stack, done)

def write_prov_list(filename, provdict):
    provides = provdict.keys()
    provides.sort()

    f = open(filename, "w")
    for prov in provides:
        print >>f, prov

        provlist = provdict[prov]
        provlist.sort()
        for pkg in provlist:
            print >>f, "\t%s" % (pkg,)
        print >>f
    f.close()


def usage(f):
        print >>f, """Usage: germinate.py [options]

Options:

  -h, --help            Print this help message.
  -s, --seed-dist=DIST  Fetch seeds for distribution DIST (default: %s).
  -m, --mirror=MIRROR   Get package lists from MIRROR
                        (default: %s).
  -d, --dist=DIST       Operate on distribution DIST (default: %s).
  -a, --arch=ARCH       Operate on architecture ARCH (default: %s).
  -c, --components=COMPS
                        Operate on components COMPS (default: %s).
  -i, --ipv6            Check IPv6 status of source packages.
  --no-rdepends         Disable reverse-dependency calculations.
""" % (RELEASE, MIRROR, string.join(DIST, ","), ARCH,
       string.join(COMPONENTS, ","))


def main():
    global RELEASE, MIRROR, DIST, ARCH, COMPONENTS, CHECK_IPV6
    want_rdepends = True

    g = Germinator()

    try:
        opts, args = getopt.getopt(sys.argv[1:], "hs:m:d:c:a:i",
                                   ["help",
                                    "seed-dist=",
                                    "mirror=",
                                    "dist=",
                                    "components=",
                                    "arch=",
                                    "ipv6",
                                    "no-rdepends"])
    except getopt.GetoptError:
        usage(sys.stderr)
        sys.exit(2)

    for option, value in opts:
        if option in ("-h", "--help"):
            usage(sys.stdout)
            sys.exit()
        elif option in ("-s", "--seed-dist"):
            RELEASE = value
        elif option in ("-m", "--mirror"):
            MIRROR = value
            if not MIRROR.endswith("/"):
                MIRROR += "/"
        elif option in ("-d", "--dist"):
            DIST = value.split(",")
        elif option in ("-c", "--components"):
            COMPONENTS = value.split(",")
        elif option in ("-a", "--arch"):
            ARCH = value
        elif option in ("-i", "--ipv6"):
            CHECK_IPV6 = True
        elif option == "--no-rdepends":
            want_rdepends = False

    apt_pkg.InitConfig()
    apt_pkg.Config.Set("APT::Architecture", ARCH)

    for dist in DIST:
        for component in COMPONENTS:
            g.parsePackages(open_tag_file("%s_%s_Packages" % (dist, component),
                                          dist, component,
                                          "binary-" + ARCH + "/Packages.gz"),
                            "deb")
            g.parseSources(open_tag_file("%s_%s_Sources" % (dist, component),
                                         dist, component,
                                         "source/Sources.gz"))
            instpackages = ""
            try:
                instpackages = open_tag_file("%s_%s_InstallerPackages" % (dist, component),
                                             dist, component,
                                             "debian-installer/binary-" + ARCH +
                                                "/Packages.gz")
            except IOError:
                # can live without these
                print "Missing installer Packages file for", component, \
                      "(ignoring)"
            else:
                g.parsePackages(instpackages, "udeb")

    if CHECK_IPV6:
        g.parseIPv6(open_ipv6_tag_file("dailydump"))

    if os.path.isfile("hints"):
        g.parseHints(open("hints"))

    blacklist = open_blacklist("blacklist")
    if blacklist is not None:
        g.parseBlacklist(blacklist)

    for seedname in ("base", "desktop", "ship", "installer", "supported"):
        g.plantSeed(seedname)
    g.prune()
    g.grow()
    g.addExtras()
    if want_rdepends:
        g.reverseDepends()

    for seedname in ("base", "desktop", "ship", "installer", "supported", "extra"):
        write_list(seedname, g, g.seed[seedname] + g.depends[seedname])
        write_list(seedname + ".seed", g, g.seed[seedname])
        write_list(seedname + ".depends", g, g.depends[seedname])
        write_list(seedname + ".build-depends", g, g.build_depends[seedname])

        if seedname != "extra":
            write_source_list(seedname + ".sources",
                              g, g.sourcepkgs[seedname])
        write_source_list(seedname + ".build-sources",
                          g, g.build_sourcepkgs[seedname])

    all = []
    sup = []
    all_srcs = []
    sup_srcs = []
    for seedname in ("base", "desktop", "ship", "installer", "supported"):
        all += g.seed[seedname]
        all += g.depends[seedname]
        all += g.build_depends[seedname]
        all_srcs += g.sourcepkgs[seedname]
        all_srcs += g.build_sourcepkgs[seedname]

        if seedname == "supported":
            sup += g.seed[seedname]
            sup += g.depends[seedname]
            sup_srcs += g.sourcepkgs[seedname]
        sup += g.build_depends[seedname]
        sup_srcs += g.build_sourcepkgs[seedname]

    write_list("all", g, all)
    write_source_list("all.sources", g, all_srcs)

    write_list("supported+build-depends", g, sup)
    write_source_list("supported+build-depends.sources", g, sup_srcs)

    write_list("all+extra", g, g.all)
    write_source_list("all+extra.sources", g, g.all_srcs)

    write_prov_list("provides", g.pkgprovides)

    if os.path.exists("rdepends"):
        shutil.rmtree("rdepends")
    if want_rdepends:
        os.mkdir("rdepends")
        os.mkdir(os.path.join("rdepends", "ALL"))
        for pkg in g.all:
            dirname = os.path.join("rdepends", g.packages[pkg]["Source"])
            if not os.path.exists(dirname):
                os.mkdir(dirname)

            write_rdepend_list(os.path.join(dirname, pkg), g, pkg)
            os.symlink(os.path.join("..", g.packages[pkg]["Source"], pkg),
                       os.path.join("rdepends", "ALL", pkg))

    g.writeBlacklisted("blacklisted")

if __name__ == "__main__":
    main()
