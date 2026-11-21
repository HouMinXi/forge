import hashlib, json, pathlib, subprocess, sys
import pytest

from code_forge import e2e_check

source = pathlib.Path(e2e_check.__file__).resolve()
test = pathlib.Path("tests/test_e2e_check.py").resolve()
target = "tests/test_e2e_check.py::TestGroupSourceFiles::test_two_top_dirs_produce_two_groups"
path = pathlib.Path.cwd().resolve()
assert source == path / "src/code_forge/e2e_check.py"
line = next(n for n, s in enumerate(source.read_text().splitlines(), 1) if "groups.setdefault(group_key, []).append(fpath)" in s)

def digest(f):
    return hashlib.sha256(f.read_bytes()).hexdigest()

def trace(frame, event, arg):
    if event == "call":
        if frame.f_code is e2e_check.group_source_files.__code__:
            return trace
        return None
    if event == "line" and frame.f_lineno == line:
        trace_events.append(dict(event="line", node=active_node, phase="call", file=source.relative_to(path).as_posix(), line=line, group_key=frame.f_locals["group_key"]))
    return trace

trace_events = []
active_node = None
reports = []
class Recorder:
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        global active_node
        assert item.nodeid == target
        active_node = item.nodeid
        previous = sys.gettrace()
        sys.settrace(trace)
        try:
            yield
        finally:
            sys.settrace(previous)
            active_node = None

    def pytest_runtest_logreport(self, report):
        reports.append({"node": report.nodeid, "phase": report.when, "outcome": report.outcome, "failure": str(report.longrepr) if report.failed else None})

revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
record = {"revision": revision, "phase":sys.argv[1], "node":target, "source_sha256":digest(source), "test_sha256":digest(test), "source_line":line, "python":sys.version, "pytest":pytest.__version__}
args = ["-q", "-c", "/dev/null", "--rootdir", str(path), "--confcutdir=tests", "-p", "no:cacheprovider", target]
record["pytest_args"] = args
rc = int(pytest.main(args, plugins=[Recorder()]))
assert record["source_sha256"] == digest(source) and record["test_sha256"] == digest(test)
record.update(exit=rc, reports=reports, trace_events=trace_events)
pathlib.Path(sys.argv[2]).write_text(json.dumps(record, indent=2) + "\n")
sys.exit(rc)
