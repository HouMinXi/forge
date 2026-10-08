"""Literal setup-first policy bootstrap and root-sealed read-only observer.

This module is embedded as reviewed literal workflow code before any action. It
uses only the trusted system interpreter/stdlib and absolute system tools. The
sealed observer is generated without bootstrap/install/parser/probe entrypoints.
No ImageVersion or provider-Python distribution/cache identity is an admission.
"""
from __future__ import annotations
import ast
import base64
from contextlib import contextmanager
import copy
import datetime
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import selectors
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

SPEC_SHA256 = "29dd5de977134160282c6faaf780fcb37f8b92222e074cc4ad2662a0a95f13d3"
INVENTORY_SHA256 = "f92fbbd668859e0b2bce4cc4ebecab30db93a07bac082f40e6eeb7135c6c4c33"
INCLUDE_SHA256 = "1e4710f11c1d7d0fd63c49e225d00e23e691d6d0a2b285e3074c6607d2ad2fad"
FEATURES_SHA256 = "ca6d62da14ed52bcf639eff823bf10e8b35eda891850c9a3f6bb0bd4f8c2b3e5"
PATH_CERTIFICATE_SHA256 = "b84f1e5388334a09441d2800cae6f346161c0728bf633274ee038eb052c0dfc7"
VENDOR_PROFILE_SHA256 = "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9"
PROFILE_MEMBER = "apparmor-profiles/usr/share/apparmor/extra-profiles/bwrap-userns-restrict"
STAGE_ROOT = Path("/var/lib/forge-qualification")
APPARMOR_ROOT = "/sys/kernel/security/apparmor"
POLICY_ROOT = APPARMOR_ROOT + "/policy"
PROFILE_ROOT = "/etc/apparmor.d"
PRODUCTION_PATH = "/usr/bin:/bin"
PARSER = "/usr/sbin/apparmor_parser"
INCLUDE_BASE = PROFILE_ROOT
SYSTEM_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C", "HOME": "/nonexistent"}
MAX_JSON = 8 * 1024 * 1024
MAX_API = 1024 * 1024
MAX_FILE = 16 * 1024 * 1024
MAX_FILES = 20000
MAX_READ = MAX_FILE
MAX_ID = 2**63 - 1
PARSER_LIMIT = MAX_API
AUDIT_LIMIT = 65536
INFO_LIMIT = 4096
MAX_COMMAND_OUTPUT = 8 * 1024 * 1024
CONFIG_KEYS = {"schema_version", "nonce", "seed_sha", "source_sha256", "repository", "publisher", "authorized_retrier"}
BINDING_KEYS = {"nonce", "control_sha", "source_sha256", "run_id", "run_attempt", "job", "boot_id"}
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}\Z")
_ENDPOINT = re.compile(r"https://api\.github\.com/repos/HouMinXi/forge/actions/runs/[1-9][0-9]{0,18}/attempts/[1-9][0-9]{0,18}\Z")
_AUDIT_FIELD = re.compile(r'(?<!\S)([a-zA-Z_][a-zA-Z_0-9]*)=("(?:[^"\\]|\\.)*"|[^\s]+)')
_AUDIT_EVENT = re.compile(r"\baudit\((\d+)\.(\d+):(\d+)\)")
ARCHIVES = {
    "apparmor-profiles_4.0.1really4.0.1-0ubuntu0.24.04.8_all.deb": "4e7d728322f899a7a06e71bedd4f4bd1f20c21f0b3361120f34cf5c0feec849e",
    "apparmor_4.0.1really4.0.1-0ubuntu0.24.04.8_amd64.deb": "190fa2ae7b76a52982bd796fbd25a067f7c42b8ec75c3797bfa67b694bf43297",
    "bubblewrap_0.9.0-1ubuntu0.3_amd64.deb": "2461f1beee9cb04c8942739fe1a2b37e7b7c2a3d518f0779dc75f9245baa3094",
}

class SetupError(RuntimeError):
    """Missing, changed, partial or unreviewed evidence requires STOP."""


def need(condition, message):
    if not condition:
        raise SetupError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha(value, label):
    need(type(value) is str and _SHA256.fullmatch(value) is not None and value != "0" * 64, "invalid " + label + " digest")
    return value


def decode(raw, label):
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise SetupError("invalid UTF-8: " + label) from exc


EXPECTED_INCLUDES = {'abi/4.0': {'bytes': 2205,
             'includes': [],
             'matches_vendor_package': True,
             'metadata': {'gid': 0, 'mode': 420, 'size': 2205, 'target': '', 'type': 'f', 'uid': 0},
             'sha256': 'e510bb8f6788b45e48de2f859a6f94a7b8416cbac5a1051814cfce925fa911bd',
             'type': 'file'},
 'tunables/alias': {'bytes': 734,
                    'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/alias.d'}],
                    'matches_vendor_package': True,
                    'metadata': {'gid': 0, 'mode': 420, 'size': 734, 'target': '', 'type': 'f', 'uid': 0},
                    'sha256': 'e7b8b278db3f7aca74e0176f22cbd3adc3c89212cc9e66a90a2787b3ceee8030',
                    'type': 'file'},
 'tunables/etc': {'bytes': 1256,
                  'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/etc.d'}],
                  'matches_vendor_package': True,
                  'metadata': {'gid': 0, 'mode': 420, 'size': 1256, 'target': '', 'type': 'f', 'uid': 0},
                  'sha256': '4211a2db7a81c3edea6a657f8eacdae4322235c1d766ac95a592cb6f1b43f7ee',
                  'type': 'file'},
 'tunables/global': {'bytes': 897,
                     'includes': [{'kind': 'include', 'optional': False, 'path': 'tunables/home'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/multiarch'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/proc'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/alias'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/kernelvars'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/system'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/xdg-user-dirs'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/share'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/etc'},
                                  {'kind': 'include', 'optional': False, 'path': 'tunables/run'},
                                  {'kind': 'include', 'optional': True, 'path': 'tunables/global.d'}],
                     'matches_vendor_package': True,
                     'metadata': {'gid': 0, 'mode': 420, 'size': 897, 'target': '', 'type': 'f', 'uid': 0},
                     'sha256': '2c1d72013b53bf103fb8abe2199ce5775f8493df1acd3ef497046156c09fdf7c',
                     'type': 'file'},
 'tunables/home': {'bytes': 974,
                   'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/home.d'}],
                   'matches_vendor_package': True,
                   'metadata': {'gid': 0, 'mode': 420, 'size': 974, 'target': '', 'type': 'f', 'uid': 0},
                   'sha256': '806a4e80696e3870d46d1ae99ac7fe7099680b39040c2183094a44671d83218e',
                   'type': 'file'},
 'tunables/home.d': {'members': ['site.local', 'ubuntu'], 'type': 'directory'},
 'tunables/home.d/site.local': {'bytes': 634,
                                'includes': [],
                                'matches_vendor_package': True,
                                'metadata': {'gid': 0,
                                             'mode': 420,
                                             'size': 634,
                                             'target': '',
                                             'type': 'f',
                                             'uid': 0},
                                'sha256': 'f6f9d209f8015196f2e391ea02debcebecbf8022ececfee3a08e3973c3bb06d4',
                                'type': 'file'},
 'tunables/home.d/ubuntu': {'bytes': 337,
                            'includes': [],
                            'matches_vendor_package': False,
                            'metadata': {'gid': 0,
                                         'mode': 420,
                                         'size': 337,
                                         'target': '',
                                         'type': 'f',
                                         'uid': 0},
                            'sha256': 'e1b3a24d2ffdcf2b02f8726941f3c6c32dee3c9222553959a335147494d74a2d',
                            'type': 'file'},
 'tunables/kernelvars': {'bytes': 1511,
                         'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/kernelvars.d'}],
                         'matches_vendor_package': True,
                         'metadata': {'gid': 0, 'mode': 420, 'size': 1511, 'target': '', 'type': 'f', 'uid': 0},
                         'sha256': 'a2fa754e93a435722ea4b8b03e2bdf52bf4c029c66397b773f5e4321d6f66ea5',
                         'type': 'file'},
 'tunables/multiarch': {'bytes': 607,
                        'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/multiarch.d'}],
                        'matches_vendor_package': True,
                        'metadata': {'gid': 0, 'mode': 420, 'size': 607, 'target': '', 'type': 'f', 'uid': 0},
                        'sha256': '0d35e0680f4dd9085a1c64911be21e95a65d12db045bde4b4ecae22915ce53ff',
                        'type': 'file'},
 'tunables/multiarch.d': {'members': ['site.local'], 'type': 'directory'},
 'tunables/multiarch.d/site.local': {'bytes': 645,
                                     'includes': [],
                                     'matches_vendor_package': True,
                                     'metadata': {'gid': 0,
                                                  'mode': 420,
                                                  'size': 645,
                                                  'target': '',
                                                  'type': 'f',
                                                  'uid': 0},
                                     'sha256': '731ad52936633b9a0c738605a2a08aad2357df91ca8fbe3bd2c3427d7aac240b',
                                     'type': 'file'},
 'tunables/proc': {'bytes': 548,
                   'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/proc.d'}],
                   'matches_vendor_package': True,
                   'metadata': {'gid': 0, 'mode': 420, 'size': 548, 'target': '', 'type': 'f', 'uid': 0},
                   'sha256': 'd6a8ac6910ecdde3930916dcb44d7b3d3bf1cfe5a8f54728f1f599e32a26ece7',
                   'type': 'file'},
 'tunables/run': {'bytes': 129,
                  'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/run.d'}],
                  'matches_vendor_package': True,
                  'metadata': {'gid': 0, 'mode': 420, 'size': 129, 'target': '', 'type': 'f', 'uid': 0},
                  'sha256': 'cbe2d9c99a82124d6206bfdd377895fa3dec48ee255923d2c31b71be53ab9bdd',
                  'type': 'file'},
 'tunables/share': {'bytes': 929,
                    'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/share.d'}],
                    'matches_vendor_package': True,
                    'metadata': {'gid': 0, 'mode': 420, 'size': 929, 'target': '', 'type': 'f', 'uid': 0},
                    'sha256': '5389ddceeb78d2012f528037e5c8205535f145c906cf6266ba400d9bafea26b7',
                    'type': 'file'},
 'tunables/system': {'bytes': 5055,
                     'includes': [{'kind': 'include', 'optional': True, 'path': 'tunables/system.d'}],
                     'matches_vendor_package': True,
                     'metadata': {'gid': 0, 'mode': 420, 'size': 5055, 'target': '', 'type': 'f', 'uid': 0},
                     'sha256': '84dfeb5a2239e9cb7626b8008275c5aa0602d671303f4b43b7db0eff4688b828',
                     'type': 'file'},
 'tunables/xdg-user-dirs': {'bytes': 844,
                            'includes': [{'kind': 'include',
                                          'optional': True,
                                          'path': 'tunables/xdg-user-dirs.d'}],
                            'matches_vendor_package': True,
                            'metadata': {'gid': 0,
                                         'mode': 420,
                                         'size': 844,
                                         'target': '',
                                         'type': 'f',
                                         'uid': 0},
                            'sha256': '1d92b108e04a4067333224c4dbfd19df8ca0f0deef7a28b153995e22fe8cedb7',
                            'type': 'file'},
 'tunables/xdg-user-dirs.d': {'members': ['site.local'], 'type': 'directory'},
 'tunables/xdg-user-dirs.d/site.local': {'bytes': 730,
                                         'includes': [],
                                         'matches_vendor_package': False,
                                         'metadata': {'gid': 0,
                                                      'mode': 420,
                                                      'size': 730,
                                                      'target': '',
                                                      'type': 'f',
                                                      'uid': 0},
                                         'sha256': 'd9705b1ab368e32f4cce94f2242c6947b8624985b9c6de2ab4ee01b398dc4703',
                                         'type': 'file'}}

EXPECTED_FEATURES = {'capability': {'sha256': '5834271f7eb4b582fe6f774362e96036c0611f258c37c3afa4eb5bd7771ad415',
                'text': '0xffffff\n'},
 'caps/extended': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                   'text': 'yes\n'},
 'caps/mask': {'sha256': 'c06f42f0fc566a2107c41ed323b942726a95f0d339f70ac6133745cdcde6ac98',
               'text': 'chown dac_override dac_read_search fowner fsetid kill setgid setuid setpcap '
                       'linux_immutable net_bind_service net_broadcast net_admin net_raw ipc_lock ipc_owner '
                       'sys_module sys_rawio sys_chroot sys_ptrace sys_pacct sys_admin sys_boot sys_nice '
                       'sys_resource sys_time sys_tty_config mknod lease audit_write audit_control setfcap '
                       'mac_override mac_admin syslog wake_alarm block_suspend audit_read perfmon bpf '
                       'checkpoint_restore\n'},
 'dbus/mask': {'sha256': '2a67ff346df16472b8323a4a4a933248400b4ee4ae0282c686f418344e1217a0',
               'text': 'acquire send receive\n'},
 'domain/attach_conditions/xattr': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                    'text': 'yes\n'},
 'domain/change_hat': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                       'text': 'yes\n'},
 'domain/change_hatv': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'domain/change_onexec': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                          'text': 'yes\n'},
 'domain/change_profile': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                           'text': 'yes\n'},
 'domain/computed_longest_left': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                  'text': 'yes\n'},
 'domain/disconnected.ipc': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                             'text': 'yes\n'},
 'domain/disconnected.path': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                              'text': 'yes\n'},
 'domain/fix_binfmt_elf_mmap': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                'text': 'yes\n'},
 'domain/interruptible': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                          'text': 'yes\n'},
 'domain/kill.signal': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'domain/post_nnp_subset': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                            'text': 'yes\n'},
 'domain/stack': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                  'text': 'yes\n'},
 'domain/unconfined_allowed_children': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                        'text': 'yes\n'},
 'domain/version': {'sha256': '44804414f85bef9588f60086587fd6e8871b39123c831ec129624f4d81a95fea',
                    'text': '1.2\n'},
 'file/mask': {'sha256': 'cbd2a2269b7dfb84517af0dd16c389d4c23ccc85363142d47bca6c064aaf4767',
               'text': 'create read write exec append mmap_exec link lock\n'},
 'io_uring/mask': {'sha256': '892f792284ef994d7ce1d23b348ae6a59f9481e5db36bff488562d5d1776c0e6',
                   'text': 'sqpoll override_creds\n'},
 'ipc/posix_mqueue': {'sha256': 'f1317122940d8528a120d5dedc274f7552576186380d1b4d13568e510e05a058',
                      'text': 'create read write open delete setattr getattr label\n'},
 'mount/mask': {'sha256': '3b0e3c91e1d7ef96b74b2dd751a6f5d908e9173fe9cf8f53423ecbd988f84f78',
                'text': 'mount umount pivot_root\n'},
 'mount/move_mount': {'sha256': '0ae8c7a3736beb576d4667f7b3bd08d66232d936fdd2c7386f66e2695af5a211',
                      'text': 'detached\n'},
 'namespaces/mask': {'sha256': '9513df1bb68dc257a9a12db6b9ff4bd46b379113ca37438a2426577e362318d8',
                     'text': 'userns_create\n'},
 'namespaces/pivot_root': {'sha256': '564739ea8fa5926d4fa5c9734fed462061960a22e6b8d5c06e94969d97891bf2',
                           'text': 'no\n'},
 'namespaces/profile': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'namespaces/userns_create': {'sha256': '9f83fde8274f44af39aee5ddd43a2449cb55d44aed6c7c0d4d258ff3ff20c15a',
                              'text': 'pciu&\n'},
 'network/af_mask': {'sha256': '35f9776774ef167b621c8503433c42c525cdc1d2ce4564edd1705af1c2b69dfa',
                     'text': 'unspec unix inet ax25 ipx appletalk netrom bridge atmpvc x25 inet6 rose netbeui '
                             'security key netlink packet ash econet atmsvc rds sna irda pppox wanpipe llc ib '
                             'mpls can tipc bluetooth iucv rxrpc isdn phonet ieee802154 caif alg nfc vsock kcm '
                             'qipcrtr smc xdp mctp\n'},
 'network/af_unix': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                     'text': 'yes\n'},
 'network_v8/af_inet': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'network_v8/af_mask': {'sha256': '35f9776774ef167b621c8503433c42c525cdc1d2ce4564edd1705af1c2b69dfa',
                        'text': 'unspec unix inet ax25 ipx appletalk netrom bridge atmpvc x25 inet6 rose '
                                'netbeui security key netlink packet ash econet atmsvc rds sna irda pppox '
                                'wanpipe llc ib mpls can tipc bluetooth iucv rxrpc isdn phonet ieee802154 caif '
                                'alg nfc vsock kcm qipcrtr smc xdp mctp\n'},
 'network_v9/af_mask': {'sha256': '35f9776774ef167b621c8503433c42c525cdc1d2ce4564edd1705af1c2b69dfa',
                        'text': 'unspec unix inet ax25 ipx appletalk netrom bridge atmpvc x25 inet6 rose '
                                'netbeui security key netlink packet ash econet atmsvc rds sna irda pppox '
                                'wanpipe llc ib mpls can tipc bluetooth iucv rxrpc isdn phonet ieee802154 caif '
                                'alg nfc vsock kcm qipcrtr smc xdp mctp\n'},
 'network_v9/af_unix': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'policy/metadata_tagging_version': {'sha256': 'd3be288e15b62b674565673d060497778f555961ec3a96b76b95c2e9de486a1c',
                                     'text': '0x000001\n'},
 'policy/notify/user': {'sha256': '32eb036edc03dec9be16fa14786fd8f489cf489825f4430c72603121e193eed9',
                        'text': 'file tags\n'},
 'policy/notify_versions/v3': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                               'text': 'yes\n'},
 'policy/notify_versions/v5': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                               'text': 'yes\n'},
 'policy/outofband': {'sha256': 'd3be288e15b62b674565673d060497778f555961ec3a96b76b95c2e9de486a1c',
                      'text': '0x000001\n'},
 'policy/permstable32': {'sha256': '2a16aef21e26f40fa3bc2b908a2d61b2d02dbe8b6ca1c962deff3cfa2e4e05f9',
                         'text': 'allow deny subtree cond kill complain prompt audit quiet hide xindex tag '
                                 'label\n'},
 'policy/permstable32_version': {'sha256': '1209168d884dedd7760cacddfbf1f58bbbed9a444898d32a3a0b059bfebfdc33',
                                 'text': '0x000003\n'},
 'policy/set_load': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                     'text': 'yes\n'},
 'policy/state32': {'sha256': 'd3be288e15b62b674565673d060497778f555961ec3a96b76b95c2e9de486a1c',
                    'text': '0x000001\n'},
 'policy/unconfined_restrictions/change_profile': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                                   'text': 'yes\n'},
 'policy/unconfined_restrictions/io_uring': {'sha256': '4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865',
                                             'text': '1\n'},
 'policy/unconfined_restrictions/userns': {'sha256': '4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865',
                                           'text': '1\n'},
 'policy/versions/v5': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'policy/versions/v6': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'policy/versions/v7': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'policy/versions/v8': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'policy/versions/v9': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                        'text': 'yes\n'},
 'ptrace/mask': {'sha256': '2ab6e50862ca213c87a1bdbf63c5ce8b40914a6e35f98cf436a3e61cd78c60fa',
                 'text': 'read trace\n'},
 'query/label/data': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                      'text': 'yes\n'},
 'query/label/multi_transaction': {'sha256': '5040625b1fb6fa4af07226683f6e6003b29e5e70b16f8cfb24be7a752393f0ee',
                                   'text': 'yes\n'},
 'query/label/perms': {'sha256': '987b6a95dc59553cce4ca635e0a23c5e34b80e6c7587946b7a53946a8b277cd1',
                       'text': 'allow deny audit quiet\n'},
 'rlimit/mask': {'sha256': 'a66cb18a6c584924d6e1cded01ac3fb646e131a840c4f9707578e8b719c50f33',
                 'text': 'cpu fsize data stack core rss nproc nofile memlock as locks sigpending msgqueue nice '
                         'rtprio rttime\n'},
 'signal/mask': {'sha256': 'a8417e3f71e56a56d7302730fb3d424b84e9879f536350b156fef893c25da321',
                 'text': 'hup int quit ill trap abrt bus fpe kill usr1 segv usr2 pipe alrm term stkflt chld '
                         'cont stop stp ttin ttou urg xcpu xfsz vtalrm prof winch io pwr sys emt lost\n'}}

ABSENT_OPTIONAL = ['local/bwrap-userns-restrict', 'local/unpriv_bwrap', 'tunables/alias.d', 'tunables/etc.d', 'tunables/global.d', 'tunables/kernelvars.d', 'tunables/proc.d', 'tunables/run.d', 'tunables/share.d', 'tunables/system.d']

CERTIFIED_PATHS = ['/bin/bash',
 '/bin/bwrap',
 '/bin/dash',
 '/bin/date',
 '/bin/ip',
 '/bin/python3',
 '/bin/python3.12',
 '/bin/sh',
 '/bin/sleep',
 '/bin/true',
 '/opt/hostedtoolcache/Python/3.12.14/x64/bin/python',
 '/opt/hostedtoolcache/Python/3.12.14/x64/bin/python3.12',
 '/sbin/ip',
 '/usr/bin/bash',
 '/usr/bin/bwrap',
 '/usr/bin/dash',
 '/usr/bin/date',
 '/usr/bin/ip',
 '/usr/bin/python3',
 '/usr/bin/python3.12',
 '/usr/bin/sh',
 '/usr/bin/sleep',
 '/usr/bin/true',
 '/usr/sbin/ip']

def keys(value: Any, keys: set[str], label: str) -> dict:
    need(type(value) is dict and set(value) == keys, f"invalid {label} fields")
    return value


def _id(value: Any, label: str, *, native: bool = False) -> int:
    if native:
        need(type(value) is str and re.fullmatch(r"[1-9][0-9]{0,18}", value) is not None,
              f"invalid native {label}")
        value = int(value)
    need(type(value) is int and 0 < value <= MAX_ID, f"invalid {label}")
    return value


def checked_sha(value: Any, label: str, *, sha256: bool = False) -> str:
    pattern = _SHA256 if sha256 else _SHA1
    need(type(value) is str and pattern.fullmatch(value) is not None, f"invalid {label}")
    need(value != "0" * len(value), f"empty {label}")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise SetupError("nonfinite JSON value")


def _bounded_structure(value: Any, depth: int = 0) -> None:
    need(depth <= 24, "JSON nesting limit exceeded")
    if type(value) is dict:
        need(len(value) <= 4096, "JSON object bound exceeded")
        for key, item in value.items():
            need(len(key) <= 4096, "JSON key bound exceeded")
            _bounded_structure(item, depth + 1)
    elif type(value) is list:
        need(len(value) <= 20000, "JSON array bound exceeded")
        for item in value:
            _bounded_structure(item, depth + 1)
    elif type(value) is str:
        need(len(value) <= 65536, "JSON string bound exceeded")


def parse_json(raw: bytes, *, limit: int = MAX_JSON) -> dict:
    need(type(raw) is bytes and 0 < len(raw) <= limit, "JSON byte bound exceeded")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        _bounded_structure(value)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise SetupError("malformed JSON") from exc
    need(type(value) is dict, "JSON root is not an object")
    return value


def read_regular(path: Path, *, limit: int = MAX_FILE) -> bytes:
    """No symlink or FIFO reads, bounded even if the file changes while reading."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "nonregular input file")
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise SetupError("cannot read required file") from exc
    need(len(raw) <= limit, "input file bound exceeded")
    return raw


def _identity(value: Any, label: str, *, owner: bool = False, strict: bool = False) -> dict:
    if strict:
        keys(value, {"id", "login", "type"}, label)
    need(type(value) is dict, f"missing {label}")
    _id(value.get("id"), label + " id")
    need(type(value.get("login")) is str and _LOGIN.fullmatch(value["login"]) is not None,
          f"invalid {label} login")
    need(type(value.get("type")) is str and value["type"] in ({"User", "Organization"} if owner else {"User"}),
          f"invalid {label} type")
    return {key: value[key] for key in ("id", "login", "type")}


def _repository(value: Any, label: str, *, strict: bool = False) -> dict:
    if strict:
        keys(value, {"id", "name", "full_name", "owner"}, label)
    need(type(value) is dict, f"missing {label}")
    _id(value.get("id"), label + " id")
    need(type(value.get("name")) is str and _NAME.fullmatch(value["name"]) is not None,
          f"invalid {label} name")
    owner = _identity(value.get("owner"), label + " owner", owner=True, strict=strict)
    need(value.get("full_name") == owner["login"] + "/" + value["name"],
          f"inconsistent {label} full name")
    return {"id": value["id"], "name": value["name"], "full_name": value["full_name"], "owner": owner}


def _native_context(launch: dict, context: Mapping[str, str]) -> dict:
    repository, publisher = launch["repository"], launch["publisher"]
    expected = {
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF_TYPE": "branch", "GITHUB_REF": launch["ref"],
        "GITHUB_REPOSITORY": repository["full_name"], "GITHUB_REPOSITORY_OWNER": repository["owner"]["login"],
        "GITHUB_ACTOR": publisher["login"], "GITHUB_TRIGGERING_ACTOR": launch["authorized_retrier"]["login"],
        "GITHUB_WORKFLOW_REF": repository["full_name"] + "/" + launch["workflow_path"] + "@" + launch["ref"],
        "GITHUB_RUN_NUMBER": "1", "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_API_URL": "https://api.github.com",
    }
    for key, value in expected.items():
        need(type(context.get(key)) is str and context[key] == value, "wrong native " + key)
    for key, value in {
        "GITHUB_REPOSITORY_ID": repository["id"], "GITHUB_REPOSITORY_OWNER_ID": repository["owner"]["id"],
        "GITHUB_ACTOR_ID": publisher["id"],
    }.items():
        need(_id(context.get(key), key, native=True) == value, "wrong native " + key)
    sha = checked_sha(context.get("GITHUB_SHA"), "native SHA")
    need(context.get("GITHUB_WORKFLOW_SHA") == sha, "workflow SHA differs from event SHA")
    job = context.get("GITHUB_JOB")
    need(type(job) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,99}", job) is not None,
          "invalid native job")
    return {"sha": sha, "run_id": _id(context.get("GITHUB_RUN_ID"), "run id", native=True),
            "run_attempt": _id(context.get("GITHUB_RUN_ATTEMPT"), "run attempt", native=True), "job": job}


def attempt_url(launch: dict, native: dict) -> str:
    return ("https://api.github.com/repos/" + launch["repository"]["full_name"]
            + f"/actions/runs/{native['run_id']}/attempts/{native['run_attempt']}")


def validate_attempt(launch: dict, native: dict, attempt: dict) -> dict:
    need(type(attempt) is dict, "missing run-attempt metadata")
    for key, expected in (("id", native["run_id"]), ("run_attempt", native["run_attempt"]), ("run_number", 1)):
        need(_id(attempt.get(key), "REST " + key) == expected, "wrong REST " + key)
    workflow_id = _id(attempt.get("workflow_id"), "REST workflow id")
    prefix = "https://api.github.com/repos/" + launch["repository"]["full_name"]
    need(attempt.get("workflow_url") == prefix + f"/actions/workflows/{workflow_id}", "wrong workflow identity URL")
    need(attempt.get("url") == prefix + f"/actions/runs/{native['run_id']}", "wrong run identity URL")
    path, ref = launch["workflow_path"], launch["ref"]
    need(attempt.get("path") in (path, path + "@" + ref, path + "@" + ref.removeprefix("refs/heads/")),
          "wrong REST workflow path/ref")
    need(attempt.get("event") == "push" and attempt.get("head_branch") == ref.removeprefix("refs/heads/"),
          "wrong REST event/branch")
    need(attempt.get("head_sha") == native["sha"], "wrong REST head SHA")
    need(type(attempt.get("head_commit")) is dict and attempt["head_commit"].get("id") == native["sha"],
          "wrong REST head commit")
    need(attempt.get("status") == "in_progress" and attempt.get("conclusion") is None,
          "run attempt is not live")
    need(type(attempt.get("pull_requests")) is list and not attempt["pull_requests"],
          "control run has pull requests")
    for key in ("repository", "head_repository"):
        need(_repository(attempt.get(key), "REST " + key) == launch["repository"], "wrong REST " + key)
        need(attempt[key].get("private") is False, "run repository is not public")
    need(_identity(attempt.get("actor"), "REST actor") == launch["publisher"], "wrong REST original actor")
    need(_identity(attempt.get("triggering_actor"), "REST triggering actor") == launch["authorized_retrier"],
          "unauthorized triggering actor")
    return {"workflow_id": workflow_id, "endpoint": attempt_url(launch, native)}


@contextmanager
def _network_deadline(seconds: float):
    """Linux CI main thread only: bound DNS, TLS, headers and trickling bodies."""
    need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "network deadline already active")
    previous = signal.getsignal(signal.SIGALRM)

    def expired(signum, frame):
        raise SetupError("public metadata deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def fetch_public_attempt(url: str) -> dict:
    """One exact public HTTPS GET. No proxy, token, redirect or retry fallback."""
    need(type(url) is str and _ENDPOINT.fullmatch(url) is not None, "invalid metadata endpoint")
    connection = http.client.HTTPSConnection("api.github.com", timeout=10, context=ssl.create_default_context())
    try:
        with _network_deadline(20):
            connection.request("GET", url.removeprefix("https://api.github.com"), headers={
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "forge-ci-launch-identity/1", "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            need(response.status == 200, "public run-attempt metadata HTTP failure")
            need(response.getheader("Content-Type", "").split(";", 1)[0].strip() == "application/json",
                  "public metadata is not JSON")
            need(response.getheader("Content-Encoding", "identity") == "identity", "encoded metadata rejected")
            declared = response.getheader("Content-Length")
            if declared is not None:
                need(re.fullmatch(r"[0-9]{1,8}", declared) is not None and int(declared) <= MAX_API,
                      "public metadata length bound exceeded")
            raw = response.read(MAX_API + 1)
            need(len(raw) <= MAX_API, "public metadata byte bound exceeded")
            if declared is not None:
                need(len(raw) == int(declared), "truncated public metadata")
            return parse_json(raw, limit=MAX_API)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # Never render request headers or an exception that could contain secrets.
        raise SetupError("public run-attempt metadata unavailable") from exc
    finally:
        connection.close()


def _children(tree: dict[str, dict], path: str) -> list[str]:
    return sorted(p for p in tree if str(PurePosixPath(p).parent) == path)


def _field(reader: PolicyReader, path: str) -> str:
    value = decode(reader.read(path, limit=65536), path).rstrip("\n")
    if not value or "\n" in value or "\x00" in value:
        raise SetupError(f"missing or ambiguous scalar: {path}")
    return value


def parse_loaded_profiles(raw: bytes) -> list[dict[str, str]]:
    items = []
    seen = set()
    for line in decode(raw, "loaded profile listing").splitlines():
        match = re.fullmatch(r"(.+) \((enforce|complain|unconfined|kill|prompt|mixed)\)", line)
        if not match or match[1] in seen:
            raise SetupError("malformed or duplicate loaded profile listing")
        seen.add(match[1])
        items.append({"qualified_name": match[1], "mode": match[2]})
    return sorted(items, key=lambda item: item["qualified_name"])


def semantic_inventory(namespaces: list[str], profiles: list[dict[str, Any]], *,
                       preserve_opaque: bool = False) -> dict[str, Any]:
    """No kernel directory IDs, inodes, boot IDs, collection paths, or ordering."""
    canonical = []
    seen = set()
    if len(namespaces) != len(set(namespaces)) or "" not in namespaces:
        raise SetupError("duplicate or missing root policy namespace")
    for profile in profiles:
        key = (profile["namespace"], profile["name"])
        if key in seen or key[0] not in namespaces:
            raise SetupError("duplicate profile identity or unknown namespace")
        seen.add(key)
        if (not preserve_opaque
                and profile["attachment"].strip().lower() in {"<unknown>", "unknown", "<opaque>", ""}):
            raise SetupError(f"opaque attachment for {key}")
        canonical.append({key: profile[key] for key in (
            "namespace", "name", "mode", "attachment", "metadata")})
    canonical.sort(key=lambda item: (item["namespace"], item["name"]))
    return {"schema": 1, "namespaces": sorted(namespaces), "profiles": canonical}


def collect_kernel_scope(reader: PolicyReader) -> dict[str, Any]:
    # Both policy's magic symlink and the loaded-profile listing are relative
    # to the task's AppArmor namespace. An unconfined label alone is insufficient.
    values = {name: _field(reader, APPARMOR_ROOT + "/." + name)
              for name in ("ns_level", "ns_name", "stacked", "ns_stacked")}
    if values["ns_level"] != "0" or values["stacked"] != "no" or values["ns_stacked"] != "no":
        raise SetupError("AppArmor inventory is not root-namespace, unstacked host scope")
    return values


def collect_kernel_inventory(reader: PolicyReader) -> dict[str, Any]:
    scope = collect_kernel_scope(reader)
    listing_before = reader.read(APPARMOR_ROOT + "/profiles")
    tree = reader.tree(POLICY_ROOT)
    profiles: list[dict[str, Any]] = []
    namespaces: list[str] = []
    revisions: dict[str, bytes] = {}
    consumed: set[str] = set()

    def metadata_bytes(path: str, visited: set[str] | None = None) -> bytes:
        visited = set() if visited is None else visited
        if path in visited or len(visited) > 8:
            raise SetupError(f"cyclic kernel metadata link: {path}")
        visited.add(path)
        entry = tree.get(path)
        if not entry:
            raise SetupError(f"missing kernel metadata: {path}")
        if entry["type"] == "l":
            target = entry["target"]
            resolved = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not resolved.startswith(POLICY_ROOT + "/"):
                raise SetupError(f"escaped kernel metadata link: {path}")
            return metadata_bytes(resolved, visited)
        if entry["type"] != "f":
            raise SetupError(f"unsupported kernel metadata type: {path}")
        consumed.add(path)
        return reader.read(path)

    def walk_profiles(directory: str, namespace: str, parent_name: str = "") -> None:
        if tree.get(directory, {}).get("type") != "d":
            raise SetupError(f"missing profile directory: {directory}")
        for path in _children(tree, directory):
            if tree[path]["type"] != "d":
                raise SetupError(f"unexpected profile tree entry: {path}")
            values = {}
            for field in ("name", "mode", "attach"):
                if tree.get(path + "/" + field, {}).get("type") != "f":
                    raise SetupError(f"missing authoritative profile {field}: {path}")
                values[field] = _field(reader, path + "/" + field)
                consumed.add(path + "/" + field)
            if values["mode"] not in {"enforce", "complain", "unconfined", "kill", "prompt", "mixed"}:
                raise SetupError(f"unknown profile mode: {values['mode']}")
            metadata = {}
            for leaf in sorted(tree):
                if not leaf.startswith(path + "/"):
                    continue
                rel = leaf[len(path) + 1:]
                if rel.split("/")[0] == "profiles" or rel in {"name", "mode", "attach"}:
                    continue
                if tree[leaf]["type"] == "d":
                    continue
                data = metadata_bytes(leaf)
                # Preserve conditional/xattr/alias metadata verbatim, when exposed.
                metadata[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                if len(data) <= 65536 and b"\x00" not in data:
                    try:
                        metadata[rel]["text"] = data.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
            # The kernel name file exposes base.name, not base.hname. Rebuild
            # the hierarchy from the authoritative nested profiles directories.
            local_name = values["name"]
            if "//" in local_name:
                raise SetupError(f"ambiguous local kernel profile name: {local_name}")
            full_name = parent_name + "//" + local_name if parent_name else local_name
            profile = {"namespace": namespace, "name": full_name, "local_name": local_name,
                       "mode": values["mode"], "attachment": values["attach"],
                       "metadata": metadata, "kernel_path": path}
            profiles.append(profile)
            nested = path + "/profiles"
            if nested in tree:
                walk_profiles(nested, namespace, full_name)

    def walk_namespace(path: str, namespace: str) -> None:
        namespaces.append(namespace)
        revision = path + "/revision"
        if tree.get(revision, {}).get("type") != "f":
            raise SetupError(f"missing policy revision: {path}")
        revisions[revision] = reader.read(revision)
        walk_profiles(path + "/profiles", namespace)
        nested = path + "/namespaces"
        if tree.get(nested, {}).get("type") != "d":
            raise SetupError(f"missing namespace inventory: {path}")
        for child in _children(tree, nested):
            if tree[child]["type"] != "d":
                raise SetupError(f"unexpected namespace entry: {child}")
            name = PurePosixPath(child).name
            if ":" in name or "\n" in name:
                raise SetupError("ambiguous namespace identity")
            walk_namespace(child, namespace + "//" + name if namespace else name)

    walk_namespace(POLICY_ROOT, "")
    # Detect unparsed profile directories, including unexpected hierarchy shapes.
    for path in tree:
        if PurePosixPath(path).name == "attach" and path not in consumed:
            raise SetupError(f"unaccounted attachment: {path}")
    listing_after = reader.read(APPARMOR_ROOT + "/profiles")
    if listing_before != listing_after or tree != reader.tree(POLICY_ROOT):
        raise SetupError("kernel policy inventory changed during collection")
    for path, before in revisions.items():
        if before != reader.read(path):
            raise SetupError("kernel namespace revision changed during collection")
    if scope != collect_kernel_scope(reader):
        raise SetupError("AppArmor namespace/stack scope changed during inventory")
    # Preserve the complete inventory for review, then retain the same STOP
    # decision below. An opaque display is never accepted as no attachment.
    semantic = semantic_inventory(namespaces, profiles, preserve_opaque=True)
    semantic["scope"] = scope
    actual = sorted((p["qualified_name"], p["mode"]) for p in parse_loaded_profiles(listing_before))
    expected = sorted(((f":{p['namespace']}://" if p["namespace"] else "") + p["name"], p["mode"])
                      for p in profiles)
    if actual != expected:
        raise SetupError("authoritative profile tree and loaded-profile listing disagree")
    conflicts = [p["name"] for p in profiles if p["name"].split("//")[0] in {"bwrap", "unpriv_bwrap"}]
    result = {"semantic": semantic, "semantic_sha256": digest(semantic), "scope": scope, "raw_profiles": profiles,
            "raw_tree": tree, "loaded_profiles": parse_loaded_profiles(listing_before),
            "conflicting_names": conflicts, "attachment_review": "REQUIRED",
            "reviewed_inventory_sha256": None,
            "conditional_semantics": "Raw metadata exported; no attachment expression has been approved."}
    opaque = [{"namespace": p["namespace"], "name": p["name"], "attachment": p["attachment"]}
              for p in profiles if p["attachment"].strip().lower() in {"<unknown>", "unknown", "<opaque>", ""}]
    if opaque:
        result["unresolved_attachments"] = opaque
    return result


def validate_kernel(value: dict, *, before: dict | None = None, compiled: bytes | None = None) -> dict:
    semantic = value.get("semantic")
    keys(semantic, {"schema", "scope", "namespaces", "profiles"}, "kernel semantic inventory")
    need(type(semantic["schema"]) is int and semantic["schema"] == 1, "invalid kernel schema")
    need(semantic["scope"] == {"ns_level": "0", "ns_name": "root", "stacked": "no", "ns_stacked": "no"},
         "non-root or stacked AppArmor namespace")
    need(value.get("scope") == semantic["scope"], "kernel scope disagreement")
    need(digest(semantic) == value.get("semantic_sha256"), "kernel semantic digest is not computed from inventory")
    # Reconstruct identities and listing independently; no self-reported absence.
    reconstructed = semantic_inventory(semantic["namespaces"], semantic["profiles"], preserve_opaque=True)
    reconstructed["scope"] = semantic["scope"]
    need(canonical(reconstructed) == canonical(semantic), "noncanonical kernel semantic inventory")
    expected_listing = sorted([
        {"qualified_name": (f":{p['namespace']}://" if p["namespace"] else "") + p["name"], "mode": p["mode"]}
        for p in semantic["profiles"]
    ], key=lambda p: p["qualified_name"])
    need(canonical(value.get("loaded_profiles")) == canonical(expected_listing), "kernel listing disagreement")
    reserved = [p for p in semantic["profiles"] if p["name"].split("//")[0] in {"bwrap", "unpriv_bwrap"}]
    if before is None:
        need(not reserved and value.get("conflicting_names") == [], "reserved profile already exists")
        need(digest(semantic) == INVENTORY_SHA256, "kernel inventory lacks the exact independent review")
    else:
        need(len(reserved) == 2 and {(p["namespace"], p["name"], p["mode"]) for p in reserved}
             == {( "", "bwrap", "enforce"), ("", "unpriv_bwrap", "enforce")},
             "add did not create exactly two enforcing root profiles")
        need(type(compiled) is bytes and 0 < len(compiled) <= 1024 * 1024,
             "missing or invalid Gate-owned compiled policy bytes")
        # The reviewed parser writes one complete top-level profile buffer at a
        # time, ordered bwrap then unpriv_bwrap, identically for stdout and add.
        # The collector resolves raw_data through each authoritative kernel
        # profile directory; directory IDs and display attachments are not keys.
        offset = 0
        seen = set()
        for name in ("bwrap", "unpriv_bwrap"):
            profile = next(p for p in reserved if p["name"] == name)
            raw = keys(profile["metadata"].get("raw_data"), {"sha256", "bytes"}, "new profile raw policy")
            checksum = sha(raw["sha256"], "new profile raw policy")
            size = raw["bytes"]
            need(type(size) is int and 0 < size <= len(compiled) - offset
                 and checksum not in seen, "missing, duplicate or out-of-bounds profile load blob")
            need(hashlib.sha256(compiled[offset:offset + size]).hexdigest() == checksum,
                 "new profile raw policy differs from its authenticated no-load compilation segment")
            offset += size
            seen.add(checksum)
        need(offset == len(compiled), "unaccounted trailing compiled policy bytes")
        prior = copy.deepcopy(semantic)
        prior["profiles"] = [p for p in prior["profiles"] if p not in reserved]
        need(canonical(prior) == canonical(before), "prior policy or namespaces changed during add")
    return semantic


def production_probe_argv() -> list[str]:
    """Exact original linux-tests.yml preflight; no metadata instrumentation."""
    command = [
        "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-ipc",
        "--clearenv",
    ]
    for path in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc"):
        command.extend(("--ro-bind", path, path))
    command.extend(
        (
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",  # noqa: S108 - private sandbox tmpfs
            "--tmpfs",
            "/workspace",
            "--chdir",
            "/workspace",
            "--",
            "/usr/bin/true",
        )
    )
    return command


def parse_info_record(raw: bytes) -> dict:
    value = parse_json(raw, limit=INFO_LIMIT)
    if not isinstance(value, dict) or type(value.get("child-pid")) is not int or value["child-pid"] <= 0:
        raise SetupError("info FD needs one complete object with a positive integer child-pid")
    return value


def _utc_for_journal(value: int) -> str:
    stamp = datetime.datetime.fromtimestamp(value // 1_000_000_000, datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%d %H:%M:%S") + f".{value % 1_000_000_000 // 1000:06d} UTC"


def _audit_fields(raw: str) -> tuple[dict, int, int]:
    fields = {}
    for match in _AUDIT_FIELD.finditer(raw):
        key, value = match.groups()
        if key in fields:
            raise SetupError("duplicate audit field")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise SetupError("invalid quoted audit field") from exc
        fields[key] = value
    events = list(_AUDIT_EVENT.finditer(raw))
    if len(events) != 1:
        raise SetupError("missing or ambiguous audit timestamp")
    seconds, fraction, serial = events[0].groups()
    if len(fraction) > 9:
        raise SetupError("invalid audit timestamp precision")
    # Linux audit commonly reports milliseconds: its represented interval
    # must intersect the precise syscall interval, not be rounded to a guess.
    start_ns = int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0"))
    resolution = 10 ** (9 - len(fraction))
    return fields, start_ns, resolution


def validate_audit(
    records: list[str],
    *,
    pid: int,
    start_ns: int,
    end_ns: int,
    capability: int,
    capname: str,
    profiles: tuple[str, ...],
) -> str:
    if (
        type(pid) is not int
        or pid <= 0
        or type(start_ns) is not int
        or type(end_ns) is not int
        or not 0 < start_ns <= end_ns
        or end_ns - start_ns > 30_000_000_000
    ):
        raise SetupError("invalid or unbounded audit interval/PID")
    if (
        not isinstance(records, list)
        or not records
        or any(not isinstance(item, str) for item in records)
        or sum(len(item.encode()) for item in records) > AUDIT_LIMIT
    ):
        raise SetupError("missing or oversized filtered audit evidence")
    candidates = []
    for raw in records:
        if not isinstance(raw, str) or 'apparmor="DENIED"' not in raw:
            raise SetupError("audit input is not filtered AppArmor denial evidence")
        fields, event_ns, resolution = _audit_fields(raw)
        if fields.get("pid") != str(pid):
            continue
        if not event_ns <= end_ns or event_ns + resolution <= start_ns:
            raise SetupError("matching PID audit lies outside the operation interval")
        if fields.get("apparmor") != "DENIED" or fields.get("operation") != "capable":
            raise SetupError("competing audit denial for the witness PID")
        if (
            fields.get("profile") not in profiles
            or fields.get("capability") != str(capability)
            or fields.get("capname") != capname
        ):
            raise SetupError("competing capability/profile audit for the witness PID")
        candidates.append(raw)
    if len(candidates) != 1:
        raise SetupError("missing or ambiguous attributable capability denial")
    return candidates[0]


def validate_negative_audit(records: list[str], *, pid: int, start_ns: int, end_ns: int) -> None:
    """One net_admin refusal, with at most the reviewed bwrap setpcap companion.

    This is negative-control-specific. Each raw denial still passes the shared
    attribution validator; the post-load sys_admin witness uses it unchanged.
    """
    if (type(records) is not list or not 1 <= len(records) <= 2
            or any(type(raw) is not str for raw in records)
            or sum(len(raw.encode()) for raw in records) > AUDIT_LIMIT):
        raise SetupError("missing or oversized negative-control audit evidence")
    seen_caps = set()
    seen_events = set()
    ordered_events = {}
    required = {"type", "apparmor", "operation", "class", "profile", "pid", "comm", "capability", "capname"}
    for raw in records:
        fields, event_ns, _ = _audit_fields(raw)
        # Reject malformed fragments, additional records, and unknown fields;
        # a field-regex match alone is not permission to discard surrounding text.
        remainder = _AUDIT_FIELD.sub("", _AUDIT_EVENT.sub("", raw))
        if (re.sub(r"\s+", " ", remainder).strip() != "audit: :"
                or set(fields) != required
                or fields["type"] != "1400" or fields["class"] != "cap"):
            raise SetupError("malformed or unclassified negative-control audit record")
        if fields["pid"] != str(pid) or fields["comm"] != "bwrap":
            raise SetupError("missing or ambiguous negative child audit attribution")
        capability = fields["capability"]
        capname = {"12": "net_admin", "8": "setpcap"}.get(capability)
        if capname is None or fields["capname"] != capname or capability in seen_caps:
            raise SetupError("unexpected or duplicate negative-control capability denial")
        event = (event_ns, int(_AUDIT_EVENT.search(raw).group(3)))
        if event[1] in seen_events:
            raise SetupError("ambiguous negative-control audit event identity")
        validate_audit([raw], pid=pid, start_ns=start_ns, end_ns=end_ns,
                       capability=int(capability), capname=capname, profiles=("unprivileged_userns",))
        seen_caps.add(capability)
        seen_events.add(event[1])
        ordered_events[capability] = event
    if "12" not in seen_caps:
        raise SetupError("missing required negative-control net_admin denial")
    if "8" in ordered_events and not ordered_events["8"] < ordered_events["12"]:
        raise SetupError("setpcap audit event does not precede net_admin refusal")


def validate_negative_control(record: dict, audit_records=None) -> None:
    if record.get("error") or type(record.get("returncode")) is not int or record["returncode"] != 1:
        raise SetupError("negative control did not demonstrate the expected refusal")
    if record.get("stderr") != "bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted\n":
        raise SetupError("negative control lacks expected RTM_NEWADDR EPERM refusal")
    try:
        raw = bytes.fromhex(record["info_raw_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SetupError("missing raw info FD record") from exc
    info = parse_info_record(raw)
    if record.get("info_bytes") != len(raw) or record.get("info") != info:
        raise SetupError("inconsistent info FD evidence")
    original = production_probe_argv()
    replica = record.get("argv", [])
    try:
        index = replica.index("--info-fd")
        if not replica[index + 1].isdigit() or int(replica[index + 1]) < 3:
            raise SetupError("info FD is not a separate private descriptor")
        without_info = replica[:index] + replica[index + 2 :]
    except (ValueError, IndexError) as exc:
        raise SetupError("missing info FD instrumentation") from exc
    if without_info != original or record.get("original_argv") != original:
        raise SetupError("negative replica changed production flags")
    validate_negative_audit(
        record.get("audit") if audit_records is None else audit_records,
        pid=info["child-pid"],
        start_ns=record["started"]["utc_ns"],
        end_ns=record["ended"]["utc_ns"],
    )


def parser_argv(operation: str, profile: Path, config: Path) -> list[str]:
    """Fixed finite operations; this is not a general privileged command runner."""
    need(operation in {"preprocess", "compile", "load"}, "unknown parser operation")
    need(profile.is_absolute() and profile.name == "bwrap-userns-restrict", "invalid vendor profile path")
    need(config.is_absolute() and config.name == "parser.conf", "invalid parser configuration path")
    options = ["--config-file=" + str(config), "--base=" + INCLUDE_BASE, "--Include=" + INCLUDE_BASE,
               "--skip-cache", "--warn=all", "--Werror", "--abort-on-error", "--jobs=0"]
    if operation != "preprocess":
        options.append("--Werror=no-rule-not-enforced")
    if operation == "preprocess":
        options += ["--skip-kernel-load", "--preprocess"]
    elif operation == "compile":
        options += ["--skip-kernel-load", "--stdout"]
    else:
        options += ["--add"]
    prefix = ["/usr/bin/timeout", "--signal=KILL", "20s", PARSER]
    return [*prefix, *options, "--", str(profile)]


def _raw_stream(record: dict, name: str, limit: int) -> bytes:
    encoded = record.get(name + "_hex")
    need(type(encoded) is str and len(encoded) <= 2 * limit, "missing or oversized raw " + name)
    try:
        raw = bytes.fromhex(encoded)
    except ValueError as exc:
        raise SetupError("invalid raw " + name) from exc
    need(raw.hex() == encoded and len(raw) <= limit, "noncanonical raw " + name)
    # Binary compiler stdout is preserved, not parsed via this display string.
    need(record.get(name) == raw.decode("utf-8", errors="replace"), "display/raw disagreement: " + name)
    return raw


def _times(started: Any, ended: Any, limit: float) -> None:
    for value in (started, ended):
        need(type(value) is dict and set(value) == {"utc_ns", "monotonic_ns"}, "invalid operation time fields")
        need(all(type(item) is int and item > 0 for item in value.values()), "invalid operation time")
    need(started["utc_ns"] <= ended["utc_ns"], "operation UTC time reversed")
    elapsed = ended["monotonic_ns"] - started["monotonic_ns"]
    need(0 <= elapsed <= int(limit * 1_000_000_000), "operation duration outside bound")


def validate_parser(record: dict, argv: list[str], operation: str) -> tuple[bytes, bytes]:
    need(type(record) is dict and record.get("argv") == argv, "parser argv changed")
    need(not record.get("error") and type(record.get("returncode")) is int and record["returncode"] == 0,
         "parser failed, timed out or returned incomplete evidence")
    _times(record.get("started"), record.get("ended"), 30)
    stdout = _raw_stream(record, "stdout", PARSER_LIMIT)
    stderr = _raw_stream(record, "stderr", PARSER_LIMIT)
    need(len(stdout) + len(stderr) <= PARSER_LIMIT, "parser output exceeded combined bound")
    if operation == "preprocess":
        need(not stderr, "preprocessor emitted warnings or other stderr")
    else:
        # The parser category also covers other rule classes. Only these two
        # exact nonfatal diagnostics from the authenticated vendor path qualify.
        expected = [f"Warning from profile {name} ({argv[-1]}): io_uring rules not enforced\n".encode("utf-8")
                    for name in ("bwrap", "unpriv_bwrap")]
        need(sorted(stderr.splitlines(keepends=True)) == sorted(expected),
             "missing, duplicate or unreviewed parser diagnostic")
    if operation != "load":
        need(bool(stdout), "missing preprocessed/compiled policy output")
    if operation != "compile":
        try:
            text = stdout.decode("utf-8")
        except UnicodeError as exc:
            raise SetupError("parser text is not valid UTF-8") from exc
        if operation == "load":
            need(not re.search(r"warning|error|failed|not enforced|downgrad", text, re.IGNORECASE),
                 "load output reported a warning or failure")
    return stdout, stderr



NATIVE_KEYS = (
    "GITHUB_EVENT_NAME", "GITHUB_REF_TYPE", "GITHUB_REF", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER",
    "GITHUB_REPOSITORY_ID", "GITHUB_REPOSITORY_OWNER_ID", "GITHUB_ACTOR", "GITHUB_ACTOR_ID", "GITHUB_TRIGGERING_ACTOR",
    "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA", "GITHUB_SHA", "GITHUB_RUN_NUMBER", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT",
    "GITHUB_JOB", "GITHUB_SERVER_URL", "GITHUB_API_URL", "GITHUB_EVENT_PATH", "RUNNER_OS", "RUNNER_ARCH",
    "RUNNER_ENVIRONMENT", "ImageOS", "ImageVersion", "FORGE_RUNNER_UID", "FORGE_RUNNER_GID",
)
SYSTEM_LINKS = {"/bin": "usr/bin", "/sbin": "usr/sbin", "/lib": "usr/lib", "/lib64": "usr/lib64",
                "/bin/sh": "dash", "/usr/bin/sh": "dash", "/bin/python3": "python3.12",
                "/usr/bin/python3": "python3.12", "/sbin/ip": "/bin/ip", "/usr/sbin/ip": "/bin/ip"}
PROVIDER_ROOT = "/opt/hostedtoolcache/Python/3.12.14/x64"
COMMAND_LIMIT = 8 * 1024 * 1024


def stamp():
    return {"utc_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def validate_config(value):
    keys(value, CONFIG_KEYS, "literal setup configuration")
    need(type(value["schema_version"]) is int and value["schema_version"] == 1, "wrong setup schema")
    need(type(value["nonce"]) is str and re.fullmatch(r"[0-9a-f]{32}", value["nonce"])
         and value["nonce"] != "0" * 32, "invalid setup nonce")
    checked_sha(value["seed_sha"], "seed")
    checked_sha(value["source_sha256"], "source", sha256=True)
    repository = {"id": 1258832822, "name": "forge", "full_name": "HouMinXi/forge",
                  "owner": {"id": 19586012, "login": "HouMinXi", "type": "User"}}
    need(value["repository"] == repository and value["publisher"] == repository["owner"]
         and value["authorized_retrier"] == repository["owner"], "unreviewed owner/repository")
    return {**value, "ref": "refs/heads/ci/qualify-apparmor-" + value["nonce"],
            "workflow_path": ".github/workflows/qualify-apparmor-" + value["nonce"] + ".yml"}


def validate_initial_identity(config, context, event):
    contract = validate_config(config)
    native = _native_context(contract, context)
    need(context.get("RUNNER_OS") == "Linux" and context.get("RUNNER_ARCH") == "X64"
         and context.get("RUNNER_ENVIRONMENT") == "github-hosted" and context.get("ImageOS") == "ubuntu24",
         "wrong hosted platform")
    need(type(event) is dict, "missing native event")
    need(all(type(event.get(k)) is bool and event[k] is False for k in ("created", "deleted", "forced")),
         "not an inert-seed transition")
    need(event.get("ref") == contract["ref"] and event.get("before") == config["seed_sha"]
         and event.get("after") == native["sha"] and native["sha"] != config["seed_sha"], "wrong seed/control transition")
    need(_repository(event.get("repository"), "event repository") == config["repository"]
         and _identity(event.get("sender"), "event sender") == config["publisher"], "wrong native event identity")
    return native



def fetch_public_commit(control_sha: str) -> dict:
    """One exact public HTTPS GET. No proxy, token, redirect or retry fallback."""
    checked_sha(control_sha, "control")
    url = "https://api.github.com/repos/HouMinXi/forge/git/commits/" + control_sha
    connection = http.client.HTTPSConnection("api.github.com", timeout=10, context=ssl.create_default_context())
    try:
        with _network_deadline(20):
            connection.request("GET", url.removeprefix("https://api.github.com"), headers={
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "forge-ci-launch-identity/1", "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            need(response.status == 200, "public run-attempt metadata HTTP failure")
            need(response.getheader("Content-Type", "").split(";", 1)[0].strip() == "application/json",
                  "public metadata is not JSON")
            need(response.getheader("Content-Encoding", "identity") == "identity", "encoded metadata rejected")
            declared = response.getheader("Content-Length")
            if declared is not None:
                need(re.fullmatch(r"[0-9]{1,8}", declared) is not None and int(declared) <= MAX_API,
                      "public metadata length bound exceeded")
            raw = response.read(MAX_API + 1)
            need(len(raw) <= MAX_API, "public metadata byte bound exceeded")
            if declared is not None:
                need(len(raw) == int(declared), "truncated public metadata")
            return parse_json(raw, limit=MAX_API)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        # Never render request headers or an exception that could contain secrets.
        raise SetupError("public run-attempt metadata unavailable") from exc
    finally:
        connection.close()


def live_identity(config, native):
    contract = validate_config(config)
    result = fetch_public_attempt(attempt_url(contract, native))
    validate_attempt(contract, native, result)
    commit = fetch_public_commit(native["sha"])
    need(commit.get("sha") == native["sha"] and type(commit.get("parents")) is list
         and len(commit["parents"]) == 1 and commit["parents"][0].get("sha") == config["seed_sha"],
         "live control commit is not the exact seed child")
    return {"sha256": digest(result), "commit_sha256": digest(commit), "checked": stamp()}


def stage_path(binding):
    need(type(binding) is dict and BINDING_KEYS <= set(binding), "missing setup binding")
    need(type(binding["nonce"]) is str and re.fullmatch(r"[0-9a-f]{32}", binding["nonce"])
         and binding["nonce"] != "0" * 32, "invalid stage nonce")
    for key in ("run_id", "run_attempt"):
        need(type(binding[key]) is int and binding[key] > 0, "invalid stage run identity")
    return STAGE_ROOT / binding["nonce"] / f"{binding['run_id']}-{binding['run_attempt']}"


def observer_argv(binding):
    return ["/usr/bin/sudo", "-n", "--", "/usr/bin/timeout", "--signal=KILL", "25s", "/usr/bin/env", "-i",
            *(key + "=" + value for key, value in SYSTEM_ENV.items()),
            "/usr/bin/python3", "-B", "-I", "-S", str(stage_path(binding) / "observer.py"), "observe"]


def root_directory(path, *, create=False, exclusive=False):
    need(path.is_absolute() and path.resolve(strict=False) == path, "noncanonical root staging path")
    for parent in path.parents:
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "root staging has writable/unowned ancestor")
    if create:
        path.mkdir(mode=0o700, exist_ok=not exclusive)
    details = path.lstat()
    need(stat.S_ISDIR(details.st_mode) and details.st_uid == details.st_gid == 0
         and stat.S_IMODE(details.st_mode) == 0o700, "staging is not private root-owned directory")


def sealed_write(path, raw):
    need(type(raw) is bytes and len(raw) <= MAX_JSON, "sealed output bound exceeded")
    root_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def sealed_read(path, limit=MAX_JSON):
    root_directory(path.parent)
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0 and info.st_nlink == 1
         and stat.S_IMODE(info.st_mode) == 0o400, "unsealed setup input")
    return read_regular(path, limit=limit)


class PublicEvidence:
    def __init__(self, enabled=True):
        self.enabled, self.total, self.seen = enabled, 0, set()

    def emit(self, kind, value):
        if not self.enabled:
            return
        raw = canonical({"kind": kind, "time": stamp(), "value": value})
        self.total += len(raw)
        need(len(raw) <= 24 * 1024 * 1024 and self.total <= 128 * 1024 * 1024, "public setup evidence bound exceeded")
        print(raw.decode(), flush=True)

    def data(self, path, raw):
        identity = {"source": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        key = (str(path), identity["sha256"])
        if key not in self.seen:
            self.seen.add(key)
            self.emit("raw-data", {**identity, "base64": base64.b64encode(raw).decode()})


class PolicyReader:
    """Read-only bounded policy observation; never imports candidate code."""
    def __init__(self, evidence):
        self.evidence, self.total = evidence, 0

    def read(self, path, *, limit=MAX_READ):
        if path.startswith(POLICY_ROOT + "/") and path.endswith("/revision"):
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                raw = os.read(fd, 129)
            finally:
                os.close(fd)
            need(re.fullmatch(rb"[0-9]+\n", raw) is not None, "ambiguous policy revision")
        else:
            raw = read_regular(Path(path), limit=limit)
        self.total += len(raw)
        need(self.total <= 128 * 1024 * 1024, "policy read budget exceeded")
        self.evidence.data(path, raw)
        return raw

    def tree(self, path):
        result = {}
        def visit(current, root=False):
            info = current.stat() if root and str(current) == POLICY_ROOT else current.lstat()
            mode = info.st_mode
            kind = "d" if stat.S_ISDIR(mode) else "f" if stat.S_ISREG(mode) else "l" if stat.S_ISLNK(mode) else "unknown"
            need(kind != "unknown" and len(result) < MAX_FILES and len(current.parts) <= 48, "unknown or oversized policy tree")
            result[str(current)] = {"type": kind, "target": os.readlink(current) if kind == "l" else "",
                                    "mode": stat.S_IMODE(mode), "uid": info.st_uid, "gid": info.st_gid, "size": info.st_size}
            if kind == "d":
                for child in sorted(current.iterdir()):
                    visit(child)
        visit(Path(path), root=True)
        self.evidence.emit("filesystem-tree", {"root": path, "tree": result})
        return result


def observe_features(reader):
    root = APPARMOR_ROOT + "/features"
    tree = reader.tree(root)
    files = {name[len(root) + 1:] for name, value in tree.items() if value["type"] == "f"}
    need(files == set(EXPECTED_FEATURES) and len(files) == 57
         and all(value["type"] in {"f", "d"} for value in tree.values()), "relevant feature path set changed")
    result = {}
    for relative in sorted(files):
        raw = reader.read(root + "/" + relative, limit=65536)
        result[relative] = {"sha256": hashlib.sha256(raw).hexdigest(), "text": decode(raw, relative)}
    need(digest(result) == FEATURES_SHA256, "relevant kernel feature bytes changed")
    return result


def observe_includes(reader):
    root = Path(PROFILE_ROOT)
    result = copy.deepcopy(EXPECTED_INCLUDES)
    need(len(result) == 19 and sum(x["type"] == "file" for x in result.values()) == 16, "invalid finite include contract")
    for relative, entry in result.items():
        path = root / relative
        need(path.resolve(strict=True) == path, "symlinked effective include")
        info = path.lstat()
        if entry["type"] == "directory":
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "writable include directory")
            entry["members"] = sorted(p.name for p in path.iterdir())
        else:
            need(stat.S_ISREG(info.st_mode), "nonregular effective include")
            raw = reader.read(str(path), limit=1024 * 1024)
            entry.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            entry["metadata"] = {"type": "f", "target": "", "mode": stat.S_IMODE(info.st_mode),
                                 "uid": info.st_uid, "gid": info.st_gid, "size": info.st_size}
    need(digest(result) == INCLUDE_SHA256, "effective include bytes, metadata or membership changed")
    for relative in ABSENT_OPTIONAL:
        need(not os.path.lexists(root / relative), "optional local/tunable override appeared")
    relevant = {"bwrap", "unpriv_bwrap", "bwrap-userns-restrict"}
    for directory in ("local", "disable", "force-complain"):
        parent = root / directory
        if not parent.exists():
            continue
        need(parent.resolve(strict=True) == parent and parent.is_dir(), "ambiguous policy override directory")
        for item in parent.rglob("*"):
            need(not any(part in relevant for part in item.relative_to(parent).parts)
                 and (not item.is_symlink() or Path(os.readlink(item)).name not in relevant), "relevant policy override present")
    return result


def ordinary_executable(path, *, provider_uid=0):
    requested = Path(path)
    canonical_path = requested.resolve(strict=True)
    name = requested.name
    expected = (PROVIDER_ROOT + "/bin/python3.12" if path.startswith(PROVIDER_ROOT + "/") else
                "/usr/bin/" + {"sh": "dash", "python3": "python3.12"}.get(name, name))
    need(str(canonical_path) == expected, "finite executable canonical path changed: " + path)
    links = []
    for component in (requested, *requested.parents):
        if component.is_symlink():
            target = os.readlink(component)
            allowed = "python3.12" if str(component) == PROVIDER_ROOT + "/bin/python" else SYSTEM_LINKS.get(str(component))
            need(target == allowed, "finite executable symlink changed: component=" + repr(str(component))
                 + "; expected=" + repr(allowed) + "; observed=" + repr(target))
            links.append({"path": str(component), "target": target})
    info = canonical_path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid in {0, provider_uid} and not info.st_mode & 0o6022,
         "privileged/writable or wrong-owner finite executable")
    with canonical_path.open("rb") as stream:
        need(stream.read(4) == b"\x7fELF", "unreviewed executable shebang")
    try:
        caps = os.getxattr(canonical_path, "security.capability")
    except OSError as exc:
        need(exc.errno == errno.ENODATA, "cannot establish executable capability absence")
    else:
        need(not caps, "finite executable has file capabilities")
    return {"path": path, "canonical": expected, "uid": info.st_uid, "gid": info.st_gid,
            "mode": stat.S_IMODE(info.st_mode), "symlinks": links, "elf": True, "file_capabilities": False}


def finite_paths(*, include_provider=False, runner_uid=0):
    need(digest(CERTIFIED_PATHS) == PATH_CERTIFICATE_SHA256 and len(CERTIFIED_PATHS) == 24, "finite path certificate changed")
    result = {}
    for path in CERTIFIED_PATHS:
        provider = path.startswith(PROVIDER_ROOT + "/")
        if provider and not include_provider:
            continue
        result[path] = ordinary_executable(path, provider_uid=runner_uid if provider else 0)
    return result


def host_prerequisites():
    need(sys.platform == "linux" and os.uname().machine == "x86_64"
         and os.uname().release == "6.17.0-1022-azure", "unreviewed kernel/architecture")
    os_release = Path("/etc/os-release")
    if os_release.is_symlink():
        need(os_release.resolve(strict=True) == Path("/usr/lib/os-release"), "unreviewed OS release alias")
        os_release = Path("/usr/lib/os-release")
    info = os_release.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "untrusted OS release metadata")
    release = dict(line.split("=", 1) for line in read_regular(os_release, limit=65536).decode().splitlines() if "=" in line)
    need(release.get("ID", "").strip('"') == "ubuntu" and release.get("VERSION_ID", "").strip('"') == "24.04", "wrong Ubuntu platform")
    enabled = read_regular(Path("/sys/module/apparmor/parameters/enabled"), limit=32).decode().strip()
    restriction = read_regular(Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns"), limit=32).decode().strip()
    need(enabled == "Y" and restriction == "1", "AppArmor/global restriction changed")
    boot = read_regular(Path("/proc/sys/kernel/random/boot_id"), limit=64).decode().strip()
    need(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is not None, "invalid boot identity")
    return {"kernel": os.uname().release, "architecture": "x86_64", "os": "ubuntu-24.04",
            "apparmor_enabled": enabled, "userns_restriction": restriction, "boot_id": boot}


def check_vendor_inputs(stage):
    vendor = stage / "vendor"
    root_directory(vendor)
    paths = {"parser": (Path(PARSER), vendor / "apparmor/sbin/apparmor_parser"),
             "bwrap": (Path("/usr/bin/bwrap"), vendor / "bubblewrap/usr/bin/bwrap")}
    result = {}
    for name, (installed, packaged) in paths.items():
        for path in (installed, packaged):
            info = path.stat()
            need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o6022, "untrusted vendor executable metadata")
        raw = read_regular(installed, limit=MAX_FILE)
        expected = read_regular(packaged, limit=MAX_FILE)
        need(raw == expected, "installed " + name + " differs from authenticated package")
        result[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    profile = vendor / PROFILE_MEMBER
    need(profile.resolve(strict=True) == profile, "vendor profile symlink")
    raw = read_regular(profile, limit=65536)
    need(len(raw) == 1936 and hashlib.sha256(raw).hexdigest() == VENDOR_PROFILE_SHA256, "vendor profile changed")
    result["profile"] = {"sha256": VENDOR_PROFILE_SHA256, "bytes": len(raw)}
    for name, expected in ARCHIVES.items():
        raw = read_regular(vendor / name, limit=MAX_FILE)
        need(hashlib.sha256(raw).hexdigest() == expected, "vendor archive changed")
    need(sealed_read(stage / "parser.conf") == b"", "private empty parser configuration changed")
    return result


def observe_state(stage, evidence):
    reader = PolicyReader(evidence)
    result = {"host": host_prerequisites(), "vendor": check_vendor_inputs(stage),
              "features": observe_features(reader), "includes": observe_includes(reader),
              "paths": finite_paths(), "kernel": collect_kernel_inventory(reader)}
    evidence.emit("policy-observation", result)
    return result


def stable_state(value):
    return {key: value[key] for key in ("host", "vendor", "features", "includes", "paths")}


def require_clean_root():
    need(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0, "bootstrap/observer requires root system context")
    need(sys.executable == "/usr/bin/python3" and sys.flags.isolated == 1 and sys.flags.no_site == 1 and sys.flags.optimize == 0 and sys.flags.dont_write_bytecode == 1,
         "observer is not isolated system Python")
    need(all(os.environ.get(key) == value for key, value in SYSTEM_ENV.items()), "unclean system-tool environment")
    need(not any(key.startswith(("PYTHON", "LD_")) for key in os.environ), "inherited interpreter/loader activation")



def kill_owned_command(process) -> None:
    """Only this Popen's new session/process group; no name/global process kill."""
    # A successful wait has reaped the leader and released its numeric PID.
    # Never signal that potentially reused PGID after reaping. Do not poll here:
    # polling would itself reap an exited leader before the signal decision.
    if process.returncode is not None:
        return
    need(type(process.pid) is int and process.pid > 0, "missing owned phase process group")
    # A Python signal may interrupt wait after waitpid reaps but before Popen
    # assigns returncode. WNOWAIT checks child ownership without reaping an
    # exited leader; its PID stays reserved until we signal this owned group.
    # The controller is single-threaded here and starts no intervening child.
    need(all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")),
         "non-reaping child-ownership check is unavailable")
    try:
        observed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return  # Already reaped, including the interrupted returncode gap.
    need(observed is None or observed.si_pid == process.pid, "ambiguous phase child ownership")
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # It exited between timeout detection and the signal; still reap.
    process.wait(timeout=5)


def command(argv, timeout=30, *, user=None, group=None, extra_groups=None, pass_fds=(), limit=COMMAND_LIMIT):
    """Internal bounded subprocess primitive; no CLI or data-driven relay exists."""
    need(0 < timeout <= 240, "command deadline bound")
    record = {"argv": list(argv), "started": stamp()}
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
               "env": dict(SYSTEM_ENV), "pass_fds": pass_fds, "start_new_session": True}
    if user is not None:
        options.update(user=user, group=group, extra_groups=extra_groups)
    stdout, stderr = bytearray(), bytearray()
    with subprocess.Popen(argv, **options) as process:
        record["wrapper_pid"] = process.pid
        with selectors.DefaultSelector() as selector:
            for pipe, output in ((process.stdout, stdout), (process.stderr, stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, output)
            deadline = time.monotonic() + timeout
            try:
                while selector.get_map():
                    need(time.monotonic() <= deadline, "command timed out")
                    for key, _ in selector.select(0.1):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            key.data.extend(chunk)
                            need(len(stdout) + len(stderr) <= limit, "command output overflow")
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except BaseException as exc:  # noqa: BLE001 - preserve failure evidence and reap only the owned child
                kill_owned_command(process)
                try:
                    process.wait(timeout=5)
                except (ChildProcessError, subprocess.TimeoutExpired):
                    pass
                record["error"] = type(exc).__name__ + ": " + str(exc)
        record.update(returncode=process.returncode, stdout=bytes(stdout).decode("utf-8", errors="replace"),
                      stderr=bytes(stderr).decode("utf-8", errors="replace"), stdout_hex=bytes(stdout).hex(),
                      stderr_hex=bytes(stderr).hex(), ended=stamp())
    return record


def checked_command(argv, evidence, timeout=30):
    result = command(argv, timeout)
    evidence.emit("command", result)
    need(not result.get("error") and result["returncode"] == 0, "fixed system command failed")
    return bytes.fromhex(result["stdout_hex"])


def download_archive(name):
    need(name in ARCHIVES, "unreviewed archive name")
    directory = "b/bubblewrap" if name.startswith("bubblewrap_") else "a/apparmor"
    connection = http.client.HTTPSConnection("security.ubuntu.com", timeout=20, context=ssl.create_default_context())
    try:
        with _network_deadline(60):
            connection.request("GET", "/ubuntu/pool/main/" + directory + "/" + name,
                               headers={"Accept-Encoding": "identity", "User-Agent": "forge-setup-policy/1"})
            response = connection.getresponse()
            need(response.status == 200 and response.getheader("Content-Encoding", "identity") == "identity", "vendor HTTPS download failed")
            raw = response.read(MAX_FILE + 1)
            need(len(raw) <= MAX_FILE and hashlib.sha256(raw).hexdigest() == ARCHIVES[name], "unauthenticated vendor archive")
            return raw
    finally:
        connection.close()


def install_vendor(stage, evidence):
    vendor = stage / "vendor"
    root_directory(vendor, create=True, exclusive=True)
    for name, expected in ARCHIVES.items():
        raw = download_archive(name)
        sealed_write(vendor / name, raw)
        evidence.emit("vendor-archive", {"name": name, "sha256": expected, "bytes": len(raw)})
        package = name.split("_", 1)[0]
        metadata = checked_command(["/usr/bin/dpkg-deb", "--field", str(vendor / name), "Package", "Version", "Architecture"], evidence)
        need(("Package: " + package + "\n").encode() in metadata, "vendor package metadata mismatch")
        checked_command(["/usr/bin/dpkg-deb", "--extract", str(vendor / name), str(vendor / package)], evidence)
    # The only installed package is the exact approved bubblewrap prerequisite.
    checked_command(["/usr/bin/dpkg", "--install", str(vendor / "bubblewrap_0.9.0-1ubuntu0.3_amd64.deb")], evidence, timeout=120)
    versions = checked_command(["/usr/bin/dpkg-query", "-W", "-f=${Package} ${Version}\\n", "apparmor", "bubblewrap"], evidence)
    need(versions == b"apparmor 4.0.1really4.0.1-0ubuntu0.24.04.8\nbubblewrap 0.9.0-1ubuntu0.3\n", "installed package version changed")


RUNNER_TRAMPOLINE = r'''
import errno, json, os, sys
fd = int(sys.argv[1]); uid = int(sys.argv[2]); gid = int(sys.argv[3]); argv = sys.argv[4:]
status_raw = open('/proc/self/status').read()
status = dict((line.split(':',1)[0], line.split(':',1)[1].strip()) for line in status_raw.splitlines() if ':' in line)
assert os.getuid() == os.geteuid() == uid > 0 and os.getgid() == os.getegid() == gid > 0
assert open('/proc/self/attr/current').read().strip() == 'unconfined'
assert all(int(status[k],16) == 0 for k in ('CapEff','CapPrm','CapInh','CapAmb'))
assert status['NoNewPrivs'] == '0'
try:
    pending = open('/proc/self/attr/exec','rb').read(4097)
except OSError as error:
    assert error.errno == errno.EINVAL
    pending = b''
assert pending in (b'',b'\n')
record = dict(uid=uid,gid=gid,pid=os.getpid(),label='unconfined',status_raw=status_raw,exec_argv=argv,pending_exec_absent=True)
os.write(fd,json.dumps(record,sort_keys=True,separators=(',',':')).encode())
os.close(fd)
if argv:
    os.execve('/usr/bin/bwrap',argv,{'PATH':'/usr/bin:/bin'})
'''


def runner_probe(stage, uid, gid, evidence, *, negative=False, snapshot_only=False):
    original = production_probe_argv()
    with tempfile.TemporaryFile(mode="w+b", dir=stage) as caller, tempfile.TemporaryFile(mode="w+b", dir=stage) as info:
        argv = [] if snapshot_only else list(original)
        if negative:
            index = argv.index("--")
            argv[index:index] = ["--info-fd", str(info.fileno())]
        launch_argv = ["/usr/bin/python3", "-B", "-I", "-S", "-c", RUNNER_TRAMPOLINE, str(caller.fileno()), str(uid), str(gid), *argv]
        account = pwd.getpwuid(uid)
        result = command(launch_argv, 15, user=uid, group=gid, extra_groups=os.getgrouplist(account.pw_name, gid),
                         pass_fds=(caller.fileno(), info.fileno()))
        caller.seek(0)
        caller_raw = caller.read(65537)
        observed = parse_json(caller_raw, limit=65536)
        need(observed.get("uid") == uid and observed.get("gid") == gid and observed.get("pid") == result["wrapper_pid"]
             and observed.get("label") == "unconfined" and observed.get("exec_argv") == argv
             and observed.get("pending_exec_absent") is True, "ordinary-user probe context changed")
        result.update(launch_argv=result["argv"], argv=argv, caller=observed, original_argv=original)
        need(not result.get("error"), "ordinary-user probe did not complete")
        if negative:
            info.seek(0)
            raw = info.read(INFO_LIMIT + 1)
            result.update(info_raw_hex=raw.hex(), info_bytes=len(raw), info=parse_info_record(raw))
            since, until = result["started"]["utc_ns"], result["ended"]["utc_ns"]
            audit = checked_command(["/usr/bin/timeout", "--signal=KILL", "4s", "/usr/bin/journalctl", "-k", "--no-pager", "--output=json",
                                     "--since=" + _utc_for_journal(since - 1_000_000), "--until=" + _utc_for_journal(until + 1_000_000),
                                     '--grep=apparmor="DENIED".*(bwrap|unprivileged_userns|userns_create)'], evidence, timeout=5)
            records = []
            for line in audit.splitlines():
                entry = parse_json(line, limit=AUDIT_LIMIT)
                need(entry.get("_TRANSPORT") == "kernel" and type(entry.get("MESSAGE")) is str, "invalid kernel audit record")
                records.append(entry["MESSAGE"])
            result["audit"] = records
            evidence.emit("negative-probe", result)
            validate_negative_control(result)
        else:
            evidence.emit("runner-snapshot" if snapshot_only else "positive-probe", result)
            need(result["returncode"] == 0, "ordinary-user positive/snapshot probe failed")
        return result


def observer_source(source, config):
    """Seal only the fixed read-only observation call graph, never an early loader."""
    removed = {"bootstrap", "observer_source", "install_vendor", "download_archive", "runner_probe",
               "parser_argv", "validate_parser", "production_probe_argv", "parse_info_record", "validate_negative_control",
               "validate_negative_audit", "validate_audit", "_audit_fields", "_utc_for_journal", "checked_command", "command", "kill_owned_command", "cancellation_guard"}
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    discarded = set()
    for node in tree.body:
        remove = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in removed
        remove = remove or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "RUNNER_TRAMPOLINE" for t in node.targets))
        if remove:
            decorators = getattr(node, "decorator_list", ())
            first = min([node.lineno, *(d.lineno for d in decorators)])
            discarded.update(range(first - 1, node.end_lineno))
    retained = "".join(line for index, line in enumerate(lines) if index not in discarded)
    # CONFIG contains only strict strings, integers and dictionaries, so its
    # canonical JSON is also a deterministic Python literal on both interpreters.
    raw = (retained + "\n\nif __name__ == '__main__':\n    observer_main(" + canonical(config).decode() + ")\n").encode()
    need(b"--add" not in raw and b"--install" not in raw and b"subprocess.Popen(" not in raw, "observer retained a mutating command entrypoint")
    return raw


def observe_sealed(stage, config):
    require_clean_root()
    for path in (STAGE_ROOT, STAGE_ROOT / config["nonce"], stage):
        root_directory(path)
    raw = sealed_read(stage / "setup.json")
    seal = parse_json(raw)
    need(set(seal) == {"schema_version", "status", "binding", "config", "runner", "setup_module_sha256", "observer", "compiled",
                       "before", "after", "input_sha256", "started", "ended", "load_attempted", "positive_passed", "image_version"}, "invalid setup seal schema")
    need(type(seal["schema_version"]) is int and seal["schema_version"] == 1 and seal["status"] == "PASS" and seal["load_attempted"] is True
         and seal["positive_passed"] is True and seal["config"] == config, "setup transition was not sealed PASS")
    need(stage_path(seal["binding"]) == stage, "setup stage/binding mismatch")
    observer = sealed_read(stage / "observer.py")
    need(seal["observer"] == {"sha256": hashlib.sha256(observer).hexdigest(), "bytes": len(observer)}, "sealed observer changed")
    compiled = sealed_read(stage / "compile.stdout", limit=MAX_API)
    need(seal["compiled"] == {"sha256": hashlib.sha256(compiled).hexdigest(), "bytes": len(compiled)}, "sealed compiler bytes changed")
    current = observe_state(stage, PublicEvidence(enabled=False))
    need(current["host"]["boot_id"] == seal["binding"]["boot_id"], "setup boot changed")
    need(digest(stable_state(current)) == seal["input_sha256"], "privileged inputs changed after setup")
    semantic = validate_kernel(current["kernel"], before=seal["before"], compiled=compiled)
    need(canonical(semantic) == canonical(seal["after"]), "sealed post-setup policy changed")
    binding = seal["binding"]
    live = live_identity(config, {"sha": binding["control_sha"], "run_id": binding["run_id"], "run_attempt": binding["run_attempt"], "job": binding["job"]})
    return {"schema_version": 1, "status": "PASS", "binding": binding, "setup_receipt": seal,
            "setup_receipt_sha256": hashlib.sha256(raw).hexdigest(), "setup_policy_sha256": digest(semantic),
            "observed": stamp(), "live": live, "policy": semantic}


def observer_main(config):
    need(sys.argv[1:] == ["observe"], "read-only observer accepts only observe")
    need(Path(__file__).name == "observer.py", "wrong observer entrypoint")
    result = observe_sealed(Path(__file__).parent, config)
    raw = canonical(result)
    need(len(raw) <= MAX_JSON, "observer output bound exceeded")
    print(raw.decode(), flush=True)


@contextmanager
def cancellation_guard():
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    def cancelled(number, _frame):
        raise SetupError("setup cancelled by signal " + str(number))
    try:
        for number in previous:
            signal.signal(number, cancelled)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@cancellation_guard()
def bootstrap(config, source):
    evidence = PublicEvidence()
    started = stamp()
    stage = None
    load_attempted = False
    try:
        require_clean_root()
        validate_config(config)
        need(type(source) is str and 0 < len(source.encode()) <= 256 * 1024, "invalid literal bootstrap source")
        context = {key: os.environ.get(key) for key in NATIVE_KEYS}
        event_path = Path(context["GITHUB_EVENT_PATH"])
        need(event_path.is_absolute(), "invalid native event path")
        event = parse_json(read_regular(event_path, limit=MAX_API), limit=MAX_API)
        native = validate_initial_identity(config, context, event)
        uid, gid = int(context["FORGE_RUNNER_UID"]), int(context["FORGE_RUNNER_GID"])
        need(uid > 0 and gid > 0 and pwd.getpwuid(uid).pw_gid == gid, "invalid original runner identity")
        host = host_prerequisites()
        binding = {"nonce": config["nonce"], "control_sha": native["sha"], "source_sha256": config["source_sha256"],
                   "run_id": native["run_id"], "run_attempt": native["run_attempt"], "job": native["job"], "boot_id": host["boot_id"]}
        stage = stage_path(binding)
        for parent in (STAGE_ROOT, STAGE_ROOT / config["nonce"]):
            root_directory(parent, create=True)
        root_directory(stage, create=True, exclusive=True)
        evidence.emit("setup-start", {"binding": binding, "image_version": context.get("ImageVersion"), "started": started})
        live_identity(config, native)
        sealed_write(stage / "parser.conf", b"")
        observer = observer_source(source, config)
        observer_identity = sealed_write(stage / "observer.py", observer)
        runner_probe(stage, uid, gid, evidence, snapshot_only=True)
        install_vendor(stage, evidence)
        before = observe_state(stage, evidence)
        semantic_before = validate_kernel(before["kernel"])
        need(len(semantic_before["profiles"]) == 123, "wrong finite existing-profile count")
        runner_probe(stage, uid, gid, evidence, negative=True)
        profile = stage / "vendor" / PROFILE_MEMBER
        for operation in ("preprocess", "compile"):
            argv = parser_argv(operation, profile, stage / "parser.conf")
            result = command(argv, 25, limit=PARSER_LIMIT)
            evidence.emit(operation, result)
            stdout, stderr = validate_parser(result, argv, operation)
            sealed_write(stage / (operation + ".stdout"), stdout)
            sealed_write(stage / (operation + ".stderr"), stderr)
        compiled = sealed_read(stage / "compile.stdout", limit=MAX_API)
        recheck_started = time.monotonic()
        live_identity(config, native)
        recheck = observe_state(stage, evidence)
        need(canonical(recheck["kernel"]["semantic"]) == canonical(semantic_before)
             and canonical(stable_state(recheck)) == canonical(stable_state(before)), "policy/input changed before add")
        need(time.monotonic() - recheck_started <= 60, "pre-load freshness expired")
        need(sealed_read(stage / "compile.stdout", limit=MAX_API) == compiled, "compiler changed before add")
        load_attempted = True
        evidence.emit("load-started", {"binding": binding, "started": stamp(), "load_attempted": True})
        argv = parser_argv("load", profile, stage / "parser.conf")
        result = command(argv, 25, limit=PARSER_LIMIT)
        evidence.emit("load", result)
        validate_parser(result, argv, "load")
        after = observe_state(stage, evidence)
        need(canonical(stable_state(after)) == canonical(stable_state(before)), "privileged inputs changed during add")
        semantic_after = validate_kernel(after["kernel"], before=semantic_before, compiled=compiled)
        runner_probe(stage, uid, gid, evidence)
        seal = {"schema_version": 1, "status": "PASS", "binding": binding, "config": config, "runner": {"uid": uid, "gid": gid},
                "setup_module_sha256": hashlib.sha256(source.encode()).hexdigest(), "observer": observer_identity,
                "compiled": {"sha256": hashlib.sha256(compiled).hexdigest(), "bytes": len(compiled)},
                "before": semantic_before, "after": semantic_after, "input_sha256": digest(stable_state(after)),
                "started": started, "ended": stamp(), "load_attempted": True, "positive_passed": True,
                "image_version": context.get("ImageVersion")}
        sealed_write(stage / "setup.json", canonical(seal) + b"\n")
        evidence.emit("setup-pass", seal)
        return seal
    except BaseException as exc:
        evidence.emit("setup-stop", {"status": "STOP", "qualified": False, "load_attempted": load_attempted,
                                     "started": started, "ended": stamp(), "error": type(exc).__name__ + ": " + str(exc)})
        raise
