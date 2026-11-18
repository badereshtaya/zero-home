"""Tests for the Home-Assistant-independent core (``zero_home/link``).

The ``link`` package is imported as a top-level package so that the
integration's ``__init__.py`` (which needs Home Assistant) is not loaded.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "custom_components", "zero_home")
for p in (PKG, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
