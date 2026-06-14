# SPDX-License-Identifier: Apache-2.0
"""Killswitch module for surflare-watchdog.

Manages nftables rules to block non-VPN egress traffic.
"""
from __future__ import annotations

import subprocess

MARK_BLOCK = "0xff"


def deactivate_killswitch():
    """Remove the killswitch nftables rule."""
    subprocess.run(
        ["nft", "delete", "rule", "inet", "filter", "output",
         "handle", "1"],
        check=True,
    )
