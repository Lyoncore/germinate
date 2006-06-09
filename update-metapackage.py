#!/usr/bin/env python
# -*- coding: UTF-8 -*-

# Copyright (c) 2004, 2005, 2006 Canonical Ltd.
# Copyright (c) 2006 Gustavo Franco
#
# This file is part of Germinate.
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

# TODO:
# - Exclude essential packages from dependencies

import sys
import urllib2
import urlparse
import gzip
import re
import os
import logging
import ConfigParser
import apt_pkg
from Germinate import Germinator
import Germinate.Archive

try:
    set # introduced in 2.4
except NameError:
    import sets
    set = sets.Set

if not os.path.exists('debian/control'):
    raise RuntimeError('must be run from the top level of a source package')
this_source = None
control = open('debian/control')
for line in control:
    if line.startswith('Source:'):
        this_source = line[7:].strip()
        break
    elif line == '':
        break
if this_source is None:
    raise RuntimeError('cannot find Source: in debian/control')
if not this_source.endswith('-meta'):
    raise RuntimeError('source package name must be *-meta')
metapackage = this_source[:-5]

print "[info] Initializing %s-* package lists update..." % metapackage
    
config = ConfigParser.SafeConfigParser()
config_file = open('update.cfg')
config.readfp(config_file)
config_file.close()

if len(sys.argv) > 1:
    dist = sys.argv[1]
else:
    dist = config.get('DEFAULT', 'dist')
        
seeds = config.get(dist, 'seeds').split()
architectures = config.get(dist, 'architectures').split()
try:
    archive_base_default = config.get(dist, 'archive_base/default')
except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
    archive_base_default = None

archive_base = {}
for arch in architectures:
    try:
        archive_base[arch] = config.get(dist, 'archive_base/%s' % arch)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
        if archive_base_default is not None:
            archive_base[arch] = archive_base_default
        else:
            raise RuntimeError('no archive_base configured for %s' % arch)

seed_base = "%s/%s/" % (config.get(dist, 'seed_base'), dist)
seed_entry = re.compile(' *\* *(?P<package>\S+) *(\[(?P<arches>[^]]*)\])? *(#.*)?')
components = config.get(dist, 'components').split()

debootstrap_version_file = 'debootstrap-version'
metapackages = map(lambda seed: '%s-%s' % (metapackage, seed), seeds)
seed_package_blacklist = set(metapackages)

def get_debootstrap_version():
    version = os.popen("dpkg-query -W --showformat '${Version}' debootstrap").read()
    if not version:
        raise RuntimeError('debootstrap does not appear to be installed')

    return version

def debootstrap_packages(arch):
    debootstrap = os.popen('debootstrap --arch %s --print-debs %s debootstrap-dir %s' % (arch,dist,archive_base[arch]))
    packages = debootstrap.read().split()
    if debootstrap.close():
        raise RuntimeError('Unable to retrieve package list from debootstrap')
    
    
    # sometimes debootstrap gives empty packages / multiple separators
    packages = filter(None, packages)
    
    packages.sort()

    return packages

def check_debootstrap_version():
    if os.path.exists(debootstrap_version_file):
        old_debootstrap_version = open(debootstrap_version_file).read().strip()
        debootstrap_version = get_debootstrap_version()
        failed = os.system("dpkg --compare-versions '%s' ge '%s'" % (debootstrap_version,
                                                                     old_debootstrap_version))
        if failed:
            raise RuntimeError('Installed debootstrap is older than in the previous version! (%s < %s)' % (
                debootstrap_version,
                old_debootstrap_version
                ))

def update_debootstrap_version():
    open(debootstrap_version_file, 'w').write(get_debootstrap_version() + '\n')

def open_seed(seed_name):
    url = urlparse.urljoin(seed_base, seed_name)
    print "[info] Fetching %s" % url
    req = urllib2.Request(url)
    req.add_header('Cache-Control', 'no-cache')
    req.add_header('Pragma', 'no-cache')
    return urllib2.urlopen(req)

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(levelname)s%(message)s'))
logger.addHandler(handler)

check_debootstrap_version()

additions = {}
removals = {}
apt_pkg.InitConfig()
for architecture in architectures:
    print "[%s] Downloading available package lists..." % architecture
    apt_pkg.Config.Set("APT::Architecture", architecture)
    germinator = Germinator()
    Germinate.Archive.TagFile(archive_base[architecture], archive_base_default).feed(
        germinator, [dist], components, architecture, cleanup=True)
    debootstrap_base = set(debootstrap_packages(architecture))

    print "[%s] Loading seed lists..." % architecture
    (seed_names, seed_inherit) = germinator.parseStructure(open_seed("STRUCTURE"))
    for seed_name in seeds:
        germinator.plantSeed(open_seed(seed_name), architecture, seed_name,
                             list(seed_inherit[seed_name]))

    print "[%s] Merging seeds with available package lists..." % architecture
    for seed_name in seeds:
        output_filename = '%s-%s' % (seed_name,architecture)
        old_list = None
        if os.path.exists(output_filename):
            old_list = set(map(str.strip,open(output_filename).readlines()))
            os.rename(output_filename, output_filename + '.old')

        new_list = []
        for package in germinator.seed[seed_name]:
            if package in seed_package_blacklist:
                continue
            if seed_name == 'minimal' and package not in debootstrap_base:
                print "%s/%s: Skipping package %s (package not in debootstrap)" % (seed_name,architecture,package)
            else:
                new_list.append(package)

        new_list.sort()
        output = open(output_filename, 'w')
        for package in new_list:
            output.write(package)
            output.write('\n')
        output.close()
        

        # Calculate deltas
        if old_list is not None:
            merged = {}
            for package in new_list:
                merged.setdefault(package, 0)
                merged[package] += 1
            for package in old_list:
                merged.setdefault(package, 0)
                merged[package] -= 1

            mergeditems = merged.items()
            mergeditems.sort()
            for package, value in mergeditems:
                #print package, value
                if value == 1:
                    additions.setdefault(package,[])
                    additions[package].append(output_filename)
                elif value == -1:
                    removals.setdefault(package,[])
                    removals[package].append(output_filename)

if additions or removals:
    os.system("dch -i 'Refreshed dependencies'")
    changes = []
    for package, files in additions.items():
        changes.append('Added %s to %s' % (package, ', '.join(files)))
    for package, files in removals.items():
        changes.append('Removed %s from %s' % (package, ', '.join(files)))
    for change in changes:
        print change
        os.system("dch -a '%s'" % change)
    update_debootstrap_version()
else:
    print "No changes found"
