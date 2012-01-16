#! /usr/bin/env python
"""Integration tests for germinate."""

# Copyright (C) 2011 Canonical Ltd.
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
import subprocess
try:
    import unittest2 as unittest
except ImportError:
    import unittest

from germinate.tests.helpers import TestCase


class TestGerminate(TestCase):
    def setUp(self):
        top_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
        self.script = os.path.join(top_dir, "bin", "germinate")
        # TODO: Reliably integration-testing this script in all the various
        # different build layouts probably requires moving its contents into
        # a module.
        if not os.path.exists(self.script):
            self.skipTest("%s does not exist" % self.script)

    def runGerminate(self, *args):
        command = [self.script]
        command.extend(["-S", "file://%s" % self.seeds_dir])
        command.extend(["-m", "file://%s" % self.archive_dir])
        command.extend(args)
        with open("/dev/null", "w") as devnull:
            self.assertTrue(subprocess.call(command, stdout=devnull,
                                            cwd=self.out_dir) == 0)

    def parseOutput(self, output_name):
        output_dict = {}
        with open(os.path.join(self.out_dir, output_name)) as output:
            output.readline()
            output.readline()
            for line in output:
                if line.startswith("-"):
                    break
                fields = [field.strip() for field in line.split("|")]
                output_dict[fields[0]] = fields[1:]
        return output_dict

    def test_trivial(self):
        self.addSource("warty", "main", "hello", "1.0-1",
                       ["hello", "hello-dependency"])
        self.addPackage("warty", "main", "i386", "hello", "1.0-1",
                        fields={"Depends": "hello-dependency"})
        self.addPackage("warty", "main", "i386", "hello-dependency", "1.0-1")
        self.addSeed("ubuntu.warty", "supported")
        self.addSeedPackage("ubuntu.warty", "supported", "hello")
        self.runGerminate("-s", "ubuntu.warty", "-d", "warty", "-c", "main")

        supported = self.parseOutput("supported")
        self.assertTrue("hello" in supported)
        self.assertTrue("hello-dependency" in supported)

        all_ = self.parseOutput("supported")
        self.assertTrue("hello" in all_)
        self.assertTrue("hello-dependency" in all_)


if __name__ == "__main__":
    unittest.main()
