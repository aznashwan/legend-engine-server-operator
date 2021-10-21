# Copyright 2021 Canonical
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import Mock

from ops.model import ActiveStatus
from ops.testing import Harness

from charm import LegendEngineServerOperatorCharm


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(LegendEngineServerOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
