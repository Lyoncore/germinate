#! /usr/bin/env python

import os
import re
import subprocess
import sys

from distutils.command.build import build
from distutils.command.clean import clean
from setuptools import setup, Command, find_packages
from setuptools.command.install import install
from setuptools.command.test import test


# We probably ought to use debian.changelog, but let's avoid that dependency
# unless and until Germinate needs it somewhere else.
changelog_heading = re.compile(r'\w[-+0-9a-z.]* \(([^\(\) \t]+)\)')

with open('debian/changelog') as changelog:
    line = changelog.readline()
    match = changelog_heading.match(line)
    if match is None:
        raise ValueError(
            "Failed to parse first line of debian/changelog: '%s'" % line)
    germinate_version = match.group(1)


class build_extra(build):
    def __init__(self, dist):
        build.__init__(self, dist)

        self.user_options.extend([('pod2man', None, 'use pod2man')])

    def initialize_options(self):
        build.initialize_options(self)
        self.pod2man = False

    def finalize_options(self):
        def has_pod2man(command):
            return self.pod2man == 'True'

        build.finalize_options(self)
        self.sub_commands.append(('build_pod2man', has_pod2man))


class build_pod2man(Command):
    description = "build POD manual pages"

    user_options = [('pod-files=', None, 'POD files to build')]

    def initialize_options(self):
        self.pod_files = []

    def finalize_options(self):
        pass

    def run(self):
        for pod_file in self.distribution.scripts:
            if not pod_file.startswith('debhelper/'):
                continue
            if os.path.exists('%s.1' % pod_file):
                continue
            self.spawn(['pod2man', '-c', 'Debhelper', '-r', germinate_version,
                        pod_file, '%s.1' % pod_file])


class test_extra(test):
    def run(self):
        # Only useful for Python 2 right now.
        if sys.version_info[0] < 3:
            self.spawn(['./run-pychecker'])

        test.run(self)


class install_extra(install):
    def run(self):
        install.run(self)

        self.spawn(['sed', '-i', 's/@VERSION@/%s/' % germinate_version,
                    os.path.join(self.install_lib, 'germinate', 'version.py')])


class clean_extra(clean):
    def run(self):
        clean.run(self)

        for path, dirs, files in os.walk('.'):
            for i in reversed(range(len(dirs))):
                if dirs[i].startswith('.') or dirs[i] == 'debian':
                    del dirs[i]
                elif dirs[i] == '__pycache__' or dirs[i].endswith('.egg-info'):
                    self.spawn(['rm', '-r', os.path.join(path, dirs[i])])
                    del dirs[i]

            for f in files:
                f = os.path.join(path, f)
                if f.endswith('.pyc'):
                    self.spawn(['rm', f])
                elif f.startswith('./debhelper') and f.endswith('.1'):
                    self.spawn(['rm', f])


perl_vendorlib = subprocess.Popen(
    ['perl', '-MConfig', '-e', 'print $Config{vendorlib}'],
    stdout=subprocess.PIPE, universal_newlines=True).communicate()[0]
if not perl_vendorlib:
    raise ValueError("Failed to get $Config{vendorlib} from perl")
perllibdir = '%s/Debian/Debhelper/Sequence' % perl_vendorlib


setup(
    name='germinate',
    version=germinate_version,
    description='Expand dependencies in a list of seed packages',
    author='Scott James Remnant',
    author_email='scott@ubuntu.com',
    maintainer='Colin Watson',
    maintainer_email='cjwatson@ubuntu.com',
    url='https://wiki.ubuntu.com/Germinate',
    license='GNU GPL',
    packages=find_packages(),
    scripts=[
        'bin/germinate',
        'bin/germinate-pkg-diff',
        'bin/germinate-update-metapackage',
        'debhelper/dh_germinate_clean',
        'debhelper/dh_germinate_metapackage',
        ],
    data_files=[
        (perllibdir, ['debhelper/germinate.pm']),
        ('/usr/share/man/man1', [
            'man/germinate.1',
            'man/germinate-pkg-diff.1',
            'man/germinate-update-metapackage.1',
            'debhelper/dh_germinate_clean.1',
            'debhelper/dh_germinate_metapackage.1',
            ])],
    cmdclass={
        'build': build_extra,
        'build_pod2man': build_pod2man,
        'test': test_extra,
        'install': install_extra,
        'clean': clean_extra,
        },
    test_suite='germinate.tests',
    # python-apt doesn't build an egg, so we can't use this.
    #install_requires=['apt>=0.7.93'],
    #tests_require=['apt>=0.7.93'],
    )
