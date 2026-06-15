# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026, Minxi Hou <houminxi@gmail.com>
"""Enable ``python3 -m code_forge`` invocation."""

import sys

from code_forge.cli import main

if __name__ == "__main__":
    sys.exit(main())
