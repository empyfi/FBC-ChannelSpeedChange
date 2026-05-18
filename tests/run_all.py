"""Run every test_*.py in this directory with stdlib unittest.

Usage:
    python tests/run_all.py
"""

import os
import sys
import unittest


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=here, pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
