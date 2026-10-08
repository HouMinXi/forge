"""Pure/mocked original-payload host-view attribution, never a live sandbox."""
from __future__ import annotations

import copy
import os

import pytest

from test_ci_probes import boundary, host_mapping
from forge_ci import probes
from forge_ci.payload import ProbeError


def check(record):
    probes.validate_original_host_mapping(record['original_host_mapping'], record['payload']['initial'],
                                          record['caller'], record['witness_mapping'], record['cgroup_path'])


def test_reader_namespace_is_the_actual_calling_task(monkeypatch):
    seen = []
    monkeypatch.setattr(probes.os, 'readlink', lambda path: seen.append(path) or 'user:[1]')
    assert probes._observer_reader()['userns'] == 'user:[1]'
    assert seen == ['/proc/thread-self/ns/user']


def test_two_level_mapping_requires_real_host_reader_evidence():
    record = boundary()
    assert record['payload']['initial']['identity_mapping']['uid_map_raw'] == '1001 0 1\n'
    check(record)
    for kind in ('uid', 'gid'):
        missing = copy.deepcopy(record)
        missing['original_host_mapping'].pop(kind + '_map_raw')
        with pytest.raises(ProbeError):
            check(missing)


@pytest.mark.parametrize('kind', ['uid', 'gid'])
@pytest.mark.parametrize('raw', ['1001 0 1\n', '1001 1002 1\n', '1001 1001 2\n',
                                 '1001 1001 1\n0 0 1\n', '', True, '١٠٠١ 1001 1\n',
                                 '1002 1001 1\n', '0 1001 1\n', '1001 1001 1 extra\n'])
def test_host_maps_reject_root_foreign_empty_wide_or_ambiguous(kind, raw):
    record = boundary()
    record['original_host_mapping'][kind + '_map_raw'] = raw
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('phase', ['reader_before', 'reader_after'])
@pytest.mark.parametrize('field,value', [('pid', 999), ('pid', True), ('uid', 0), ('euid', 1002),
                                        ('gid', 0), ('egid', 1002), ('userns', 'user:[11]')])
def test_reader_cannot_drift_from_admitted_caller(phase, field, value):
    record = boundary()
    record['original_host_mapping'][phase][field] = value
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('phase', ['process_before', 'process_after'])
@pytest.mark.parametrize('field,value', [('host_pid', 451), ('namespace_pid', 3), ('host_pid', True),
                                        ('nspid', [450, 3]), ('nspid', [True, 2]),
                                        ('userns', 'user:[99]'), ('pidns', 'pid:[14]'),
                                        ('stat_raw', '450 (python3) S 449 0\n')])
def test_process_pid_namespace_and_incarnation_are_bound(phase, field, value):
    record = boundary()
    record['original_host_mapping'][phase][field] = value
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('mutation', ['start', 'parent', 'saved_uid', 'filesystem_gid', 'caps', 'nnp',
                                    'status_pid', 'status_nspid', 'parent_pid', 'parent_nspid',
                                    'witness_parent', 'witness_pid', 'witness_nspid', 'cgroup_path',
                                    'missing_process', 'extra_record', 'extra_reader', 'pidfd_field'])
def test_host_mapping_rejects_inconsistent_metadata(mutation):
    record = boundary()
    value = record['original_host_mapping']
    after = value['process_after']
    if mutation == 'start':
        after['stat_raw'] = after['stat_raw'].replace('1234', '1235')
    elif mutation == 'parent':
        after['stat_raw'] = after['stat_raw'].replace('S 449 ', 'S 448 ')
        after['status_raw'] = after['status_raw'].replace('PPid:\t449', 'PPid:\t448')
    elif mutation == 'saved_uid':
        after['status_raw'] = after['status_raw'].replace('Uid:\t1001\t1001\t1001\t1001', 'Uid:\t1001\t1001\t0\t1001')
    elif mutation == 'filesystem_gid':
        after['status_raw'] = after['status_raw'].replace('Gid:\t1001\t1001\t1001\t1001', 'Gid:\t1001\t1001\t1001\t0')
    elif mutation == 'caps':
        after['status_raw'] = after['status_raw'].replace('CapEff:\t0000000000000000', 'CapEff:\t0000000000200000')
    elif mutation == 'nnp':
        after['status_raw'] = after['status_raw'].replace('NoNewPrivs:\t1', 'NoNewPrivs:\t0')
    elif mutation == 'status_pid':
        after['status_raw'] = after['status_raw'].replace('Pid:\t450\n', 'Pid:\t451\n')
    elif mutation == 'status_nspid':
        after['status_raw'] = after['status_raw'].replace('NSpid:\t450\t2', 'NSpid:\t450\t3')
    elif mutation == 'parent_pid':
        value['parent_status_raw'] = value['parent_status_raw'].replace('Pid:\t449\n', 'Pid:\t448\n')
    elif mutation == 'parent_nspid':
        value['parent_status_raw'] = value['parent_status_raw'].replace('NSpid:\t449\t1', 'NSpid:\t449\t2')
    elif mutation == 'witness_parent':
        record['witness_mapping']['status_raw'] = record['witness_mapping']['status_raw'].replace('PPid:\t450', 'PPid:\t451')
    elif mutation == 'witness_pid':
        record['witness_mapping']['host_pid'] = 501
    elif mutation == 'witness_nspid':
        record['witness_mapping']['status_raw'] = record['witness_mapping']['status_raw'].replace('NSpid:\t500\t5', 'NSpid:\t500\t6')
    elif mutation == 'cgroup_path':
        value['cgroup_path'] = '/other'
    elif mutation == 'missing_process':
        value.pop('process_before')
    elif mutation == 'extra_record':
        value['extra'] = True
    elif mutation == 'extra_reader':
        value['reader_before']['extra'] = True
    else:
        value['pidfd']['extra'] = True
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('phase', ['cgroup_procs_before_raw', 'cgroup_procs_after_raw'])
@pytest.mark.parametrize('raw', ['449\n450\n', '450\n500\n', '449\n500\n', '449\n450\n500\n500\n',
                                 '', '0\n449\n450\n500\n', 'bad\n', True, '\n'.join(map(str, range(1, 66)))])
def test_membership_is_bounded_and_all_three_processes_remain_owned(phase, raw):
    record = boundary()
    record['original_host_mapping'][phase] = raw
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('key', ['supported', 'alive_before', 'alive_after'])
@pytest.mark.parametrize('value', [False, 1, None])
def test_pidfd_receipt_must_be_exact_live_support(key, value):
    record = boundary()
    record['original_host_mapping']['pidfd'][key] = value
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('field,value', [('pid', True), ('ppid', True), ('ppid', 1.0), ('uid', 1001.0)])
def test_original_snapshot_numeric_identity_is_strict(field, value):
    record = boundary()
    record['payload']['initial'][field] = value
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('field,value', [('nspid', [999, 777]), ('nspid', [500, 5.0]),
                                       ('nspid', None), ('host_pid', 500.0), ('namespace_pid', 5.0)])
def test_witness_parsed_and_recorded_numeric_identity_must_agree(field, value):
    record = boundary()
    record['witness_mapping'][field] = value
    with pytest.raises(ProbeError):
        check(record)


@pytest.mark.parametrize('state', ['Z', 'X', 'x'])
def test_dead_process_stat_cannot_claim_live_pidfd(state):
    record = boundary()
    for phase in ('process_before', 'process_after'):
        target = record['original_host_mapping'][phase]
        target['stat_raw'] = target['stat_raw'].replace(') S ', ') ' + state + ' ')
    with pytest.raises(ProbeError):
        check(record)


def fake_live_tree(tmp_path, monkeypatch):
    record = boundary()
    cg = tmp_path / 'cgroup'
    cg.mkdir()
    record['cgroup_path'] = str(cg)
    value = record['original_host_mapping'] = host_mapping(record['caller'], str(cg))
    (cg / 'cgroup.procs').write_text(value['cgroup_procs_before_raw'])
    proc = tmp_path / 'proc'
    for pid, raw in [(449, value['parent_status_raw']), (450, value['process_before']['status_raw']),
                     (500, record['witness_mapping']['status_raw'])]:
        path = proc / str(pid)
        path.mkdir(parents=True)
        (path / 'status').write_text(raw)
    path = proc / '450'
    (path / 'stat').write_text(value['process_before']['stat_raw'])
    for kind in ('uid', 'gid'):
        (path / (kind + '_map')).write_text(value[kind + '_map_raw'])
    (path / 'ns').mkdir()
    (path / 'ns/user').symlink_to('user:[11]')
    (path / 'ns/pid').symlink_to('pid:[13]')
    monkeypatch.setattr(probes, '_observer_reader', lambda: copy.deepcopy(value['reader_before']))
    read_fd, write_fd = os.pipe()
    opened = []
    def pidfd_open(pid, flags):
        assert pid == 450 and flags == 0
        fd = os.dup(read_fd)
        opened.append(fd)
        return fd
    monkeypatch.setattr(probes.os, 'pidfd_open', pidfd_open)
    return record, cg, proc, read_fd, write_fd, opened


@pytest.mark.parametrize('failure', [None, 'missing_pidfd', 'dead_before', 'dead_after', 'changed_namespace',
                                   'left_cgroup', 'reader_drift', 'missing_map', 'unowned_parent'])
def test_live_capture_keeps_exact_private_pidfd_and_rejects_races(tmp_path, monkeypatch, failure):
    record, cg, proc, read_fd, write_fd, opened = fake_live_tree(tmp_path, monkeypatch)
    original = probes._observer_process
    calls = []
    def observed(*args):
        value = original(*args)
        calls.append(1)
        if len(calls) == 2:
            if failure == 'dead_after':
                os.write(write_fd, b'x')
            elif failure == 'changed_namespace':
                value['userns'] = 'user:[99]'
            elif failure == 'left_cgroup':
                (cg / 'cgroup.procs').write_text('449\n500\n')
        return value
    monkeypatch.setattr(probes, '_observer_process', observed)
    if failure == 'missing_pidfd':
        monkeypatch.delattr(probes.os, 'pidfd_open')
    elif failure == 'dead_before':
        os.write(write_fd, b'x')
    elif failure == 'missing_map':
        (proc / '450/uid_map').unlink()
    elif failure == 'unowned_parent':
        (cg / 'cgroup.procs').write_text('450\n500\n')
    elif failure == 'reader_drift':
        counter = []
        def reader():
            value = dict(record['original_host_mapping']['reader_before'])
            counter.append(1)
            if len(counter) == 2:
                value['userns'] = 'user:[99]'
            return value
        monkeypatch.setattr(probes, '_observer_reader', reader)
    try:
        def run():
            return probes.observe_original_host_mapping(cg, record['payload']['initial'], record['caller'],
                                                        record['witness_mapping'], proc_root=proc)
        if failure:
            with pytest.raises((ProbeError, FileNotFoundError)):
                run()
        else:
            assert run() == record['original_host_mapping']
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)
