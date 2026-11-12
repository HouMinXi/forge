"""Independent stock-runner fixed-child capability observation, never qualification."""

import argparse
import errno
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 65536
MARKER = b"forge-stock-control-v1\n"
CHILD = r"""import fcntl,json,os,re,socket,struct,subprocess,tempfile,sys
from pathlib import Path
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
stock={'cache_tag':sys.implementation.cache_tag,
 'profile':Path('/proc/self/attr/current').read_text().strip(),
 'status':{key:status[key].strip() for key in ('CapEff','CapPrm','CapAmb','NoNewPrivs')},
 'namespaces':{key:os.readlink('/proc/self/ns/'+key) for key in ('user','mnt','pid','net','ipc','uts')}}
parent=json.loads(sys.argv[1])
safe=(stock['cache_tag']=='cpython-314' and stock['profile'] in ('bwrap//&unpriv_bwrap (enforce)','unpriv_bwrap//&bwrap (enforce)')
 and all(stock['status'][key]=='0'*16 for key in ('CapEff','CapPrm','CapAmb')) and stock['status']['NoNewPrivs']=='1'
 and type(parent) is dict and set(parent)==set(stock['namespaces'])
 and all(re.fullmatch(key+r':\[\d+\]',value) is not None and value!=parent[key] for key,value in stock['namespaces'].items()))
flags=None;code=None;marker=False;stderr_empty=False
if safe:
    try:
        if socket.if_nameindex()==[(1,'lo')]:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                flags=struct.unpack_from('H',fcntl.ioctl(sock.fileno(),0x8913,struct.pack('256s',b'lo')),16)[0]
    except OSError:
        pass
if safe and type(flags) is int and flags & 8 and flags & 1:
    try:
      with tempfile.TemporaryFile(dir='/tmp') as out, tempfile.TemporaryFile(dir='/tmp') as err:
        done=subprocess.run(['/usr/bin/strace','-qq','-e','trace=none','-o','/dev/null','--','/usr/bin/python3','-I','-B','-c',"import os;os.write(1,b'forge-stock-control-v1\\n')"],stdin=subprocess.DEVNULL,stdout=out,stderr=err,timeout=5,check=False,env={'PATH':'/usr/bin:/bin','HOME':'/tmp','TMPDIR':'/tmp','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1'})
        out.seek(0);err.seek(0)
        marker=out.read(1025)==b'forge-stock-control-v1\n'
        stderr_empty=err.read(1025)==b''
        code=done.returncode
    except (OSError,subprocess.SubprocessError):
        pass
print(json.dumps({'stock':stock,'loopback_flags':flags,'trace':{'returncode':code,'marker':marker,'stderr_empty':stderr_empty}}))"""


def helper(name):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location("_stock_control_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def empty():
    return dict(
        schema="forge-stock-capture-control-v1",
        environment="INDEPENDENT_UBUNTU_26_04",
        required_runtime="SYSTEM_CPYTHON_314",
        status="UNSUPPORTED",
        stage="PREREQUISITE",
        error="NOT_OBSERVED",
        stock_safety="NOT_VERIFIED",
        loopback="NOT_OBSERVED",
        strace="NOT_OBSERVED",
        cleanup="NOT_ASSESSED",
        control_returncode=None,
        qualification="NOT_ASSESSED",
        causal_attribution="NOT_ASSESSED",
        raw="NOT_PUBLISHED",
    )


def no_file_caps(path):
    try:
        os.getxattr(path, "security.capability", follow_symlinks=False)
    except OSError as error:
        if error.errno == errno.ENODATA:
            return
        raise
    # Linux xattrs are kernel-bounded; any capability attribute is rejected.
    raise ValueError("file capability attribute not permitted")


def command(stock, parent):
    return [
        str(stock.BWRAP),
        "--unshare-all",
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",  # noqa: S108 - fresh per-control tmpfs, never host writable storage
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "HOME",
        "/tmp",  # noqa: S108 - fresh per-control tmpfs, never host writable storage
        "--setenv",
        "TMPDIR",
        "/tmp",  # noqa: S108 - fresh per-control tmpfs, never host writable storage
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--",
        "/usr/bin/python3",
        "-I",
        "-B",
        "-c",
        CHILD,
        json.dumps(parent, sort_keys=True),
    ]


def category(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return "TIMEOUT"
    if isinstance(error, (FileNotFoundError, PermissionError)):
        return "UNAVAILABLE"
    if isinstance(error, KeyboardInterrupt):
        return "INTERRUPTED"
    return "INVALID" if isinstance(error, (ValueError, TypeError, KeyError)) else "EXECUTION_ERROR"


def observe(output):
    result = empty()
    capture = helper("capture_installed_cli")
    stock = helper("ci_stock_runner")
    ownership = helper("cli_capture_launch_contrast")
    try:
        output = capture.new_private_directory(output)
        capture.sanitize_controller(output)
        capture.apply_limits()
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        bound = LIMIT if hard == resource.RLIM_INFINITY else min(LIMIT, hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (bound, bound))
        capture.require(
            sys.flags.isolated
            and sys.implementation.cache_tag == "cpython-314"
            and Path(sys.executable).resolve() == Path("/usr/bin/python3").resolve(),
            "required_runtime",
        )
        capture.require(os.getuid() != 0 and os.getuid() == os.geteuid(), "ordinary user required")
        before = stock.security_state()
        for path in (stock.BWRAP, Path("/usr/bin/strace"), Path("/usr/bin/python3").resolve()):
            stock.root_file(path)
            no_file_caps(path)
        parent = {name: os.readlink("/proc/self/ns/" + name) for name in stock.NAMESPACES}
        origin = ROOT / "src/code_forge"
        sources = {
            name: capture.regular_bytes(origin / name, 1024 * 1024)
            for name in ("_mutation_imports.py", "_mutation_process.py")
        }
        owner = capture.load_owner(sources, origin)
        result["stage"] = "LAUNCH"
        try:
            done = owner(
                command(stock, parent),
                timeout=10,
                cwd=str(output),
                env=capture.safe_environment(output),
                memory_limit_bytes=1024**3,
            )
        except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - fixed diagnostic outcome only
            result["cleanup"] = ownership.cleanup(
                getattr(error, "ownership", getattr(error, "report", None))
            )
            raise
        result["cleanup"] = ownership.cleanup(getattr(done, "ownership", None))
        capture.require(type(done.returncode) is int and -127 <= done.returncode <= 255, "return code")
        result["control_returncode"] = done.returncode
        result["stage"] = "CLEANUP"
        capture.require(result["cleanup"] == "COMPLETE", "cleanup")
        result["stage"] = "HOST_STATE"
        capture.require(stock.security_state() == before, "host security state changed")
        result["stage"] = "LAUNCH"
        capture.require(done.returncode == 0, "child exit")
        result["stage"] = "CHILD"
        capture.require(
            type(done.stdout) is bytes
            and len(done.stdout) <= 8192
            and done.stdout.endswith(b"\n")
            and done.stderr == b"",
            "child output",
        )
        child = json.loads(done.stdout, object_pairs_hook=capture.no_duplicate_keys)
        capture.require(
            type(child) is dict and set(child) == {"stock", "loopback_flags", "trace"}, "child schema"
        )
        stock.verify_child(child["stock"], parent)
        result["stock_safety"] = "VERIFIED"
        result["stage"] = "LOOPBACK"
        flags = child["loopback_flags"]
        capture.require(type(flags) is int and 0 <= flags <= 65535 and flags & 8, "loopback flags")
        result["loopback"] = "UP" if flags & 1 else "DOWN"
        capture.require(result["loopback"] == "UP", "loopback down")
        result["stage"] = "STRACE"
        trace = child["trace"]
        capture.require(
            type(trace) is dict and set(trace) == {"returncode", "marker", "stderr_empty"},
            "trace schema",
        )
        capture.require(
            type(trace["returncode"]) is int
            and trace["returncode"] == 0
            and trace["marker"] is True
            and trace["stderr_empty"] is True,
            "fixed trace child",
        )
        result.update(status="OBSERVED", stage="COMPLETE", error="NONE", strace="FIXED_CHILD_COMPLETED")
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - no messages or raw outputs published
        result["error"] = category(error)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args()
    result = observe(args.output)
    capture = helper("capture_installed_cli")
    capture.checked_directory(args.summary.parent, private=True)
    pending = args.summary.with_name(".summary.pending")
    capture.private_bytes(pending, (json.dumps(result, sort_keys=True) + "\n").encode())
    os.replace(pending, args.summary)
    return 0 if result["status"] == "OBSERVED" else 130 if result["error"] == "INTERRUPTED" else 78


if __name__ == "__main__":
    raise SystemExit(main())
