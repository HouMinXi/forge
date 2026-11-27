#!/bin/sh
# Offline replay with a local, clean checkout of the pinned #552 merge.
set -eu
test "$#" -eq 1 || { echo "usage: sh replay_552.sh SAMPLE_CHECKOUT" >&2; exit 2; }
sample=$(cd "$1" && pwd -P)
here=$(CDPATH='' cd "$(dirname "$0")" && pwd -P)
src=$(CDPATH='' cd "$here/../../../src" && pwd -P)
python=${PYTHON:-python3}
python=$("$python" -c 'import sys; print(sys.executable)')
base=affe2e5b44e65dcc52169ab5f98c0e13b77b2fd3
candidate=3fae8be7a6080ad645d09ec3e142081a678e9c8b
test "$(git -C "$sample" rev-parse HEAD)" = "$candidate"
test "$(git -C "$sample" rev-parse HEAD^1)" = "$base"
test -z "$(git -C "$sample" status --porcelain --untracked-files=all)"
work=$(mktemp -d "${TMPDIR:-/tmp}/forge-r3-552.XXXXXXXX")
printf 'Evidence directory: %s\n' "$work"
mkdir "$work/plugin"
cp "$src/code_forge/e2e_record.py" "$work/plugin/forge_replay_record.py"
export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
unset PYTEST_ADDOPTS PYTEST_PLUGINS
source=src/code_forge/machine.py
observer=$(sha256sum "$here/../r3-target-line/target_hits.py" | cut -d ' ' -f 1)
cd "$sample"
for case in changed same; do
    run=$("$python" -c 'import uuid; print(uuid.uuid4().hex)')
    node="tests/test_contract_flag.py::test_working_contract_clean_credit[$case]"
    record="$work/$case.run.json"
    packet="$work/$case.packet.json"
    PYTHONPATH="$sample/src:$work/plugin" FORGE_E2E_RECORD="$record" FORGE_E2E_EXECUTION_ID="$run" \
        "$python" -B "$here/packet.py" --root "$sample" --source "$source" --file "$source" \
        --line 1880 --node "$node" --output "$packet" --run-id "$run" -- \
        "$node" -p forge_replay_record --rootdir "$sample" --confcutdir "$sample" -q \
        > "$work/$case.pytest.out"
    packet_sha=$(sed -n "s/^forge-r3-packet run_id=$run sha256=//p" "$work/$case.pytest.out")
    record_sha=$(sha256sum "$record" | cut -d ' ' -f 1)
    hits=0
    test "$case" != changed || hits=1
    inspect() {
        PYTHONPATH="$src" "$python" -B "$here/inspect_change.py" \
            --root "$sample" --base "$base" --candidate "$candidate" \
            --expect-target-file "$source" --expect-line 1880 --expect-node "$node" \
            --record "$record" --record-sha256 "$record_sha" --packet "$packet" \
            --expect-run-id "$run" --expect-packet-sha256 "$packet_sha" \
            --expect-observer-sha256 "$observer" --expect-call passed --expect-exit 0 "$@"
    }
    inspect --expect-hits "$hits" > "$work/$case.inspect.json"
    cat "$work/$case.inspect.json"
    if test "$case" = same; then
        status=0
        inspect --expect-hits 1 > "$work/same.false-claim.json" || status=$?
        test "$status" -eq 1
        cat "$work/same.false-claim.json"
    fi
done
test -z "$(git status --porcelain --untracked-files=all)"
