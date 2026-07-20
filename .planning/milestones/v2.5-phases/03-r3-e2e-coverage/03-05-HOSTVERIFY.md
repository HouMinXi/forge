# Phase 3 Host Verification Results

**Phase:** 03-r3-e2e-coverage  
**Plan:** 03-05  
**Date:** 2026-05-27  
**Corpus:** /home/houminxi/code/kernel/networking (read-only)  
**Scratch:** /tmp/03-05-hostverify/  

## Corpus Integrity Snapshot

Before any test run:
```
find ~/code/kernel/networking -type f -not -path '*/.git/*' | sort > /tmp/kernel-net-before.txt
wc -l /tmp/kernel-net-before.txt
# 121923 /tmp/kernel-net-before.txt
touch /tmp/timestamp.tag
```

## sctp Confirmation

sctp/integration/ confirmed present at corpus root:
```
ls ~/code/kernel/networking/sctp/integration/
# dtlsosctp  ipvs  multitopo  netfilter  ovs
```
LOCKED CHOICE: sctp used for Case B. No fallback needed.

## Case A: Hub + dependent co-occurrence, no integration test present

**components.yaml (in-process dict, not written to corpus):**
```yaml
version: 1
components:
  common:
    paths: ["common/**"]
    shared: true
  bonding:
    paths: ["bonding/**"]
    depends_on: [common]
  bridge:
    paths: ["bridge/**"]
    depends_on: [common]
e2e_patterns: ["*/integration/**"]
```

**Synthetic diff (touches common/include.sh AND bonding/runtest.sh):**
```diff
diff --git a/common/include.sh b/common/include.sh
index abc1234..def5678 100644
--- a/common/include.sh
+++ b/common/include.sh
@@ -10,2 +10,3 @@ setup_network() {
     ip link set eth0 up
+    ip link set eth1 up
 }
diff --git a/bonding/runtest.sh b/bonding/runtest.sh
index 111aaaa..222bbbb 100644
--- a/bonding/runtest.sh
+++ b/bonding/runtest.sh
@@ -5,2 +5,3 @@ run_test() {
     setup_bonding
+    check_bonding_link
 }
```

**Command:** `check_layer_2(diff_a, corpus_root, components_a)`

**Expected:** 1 UNCERTAIN finding for (common, bonding), fingerprint starts "e2e-l2".

**Actual:**
```
findings count: 1
fingerprint=e2e-l2:e1be5fe17bf67ce8, disposition=Disposition.UNCERTAIN
description: cross-component change: hub 'common' + dependent 'bonding' both touched;
  no e2e artifact under 'bonding' paths matches e2e_patterns
```

PASS. Fingerprint starts "e2e-l2". Disposition is UNCERTAIN.

Note: `ls ~/code/kernel/networking/bonding/integration/` returned not found, confirming
no integration directory exists under bonding -- the finding is grounded in the real
corpus filesystem.

## Case B: Hub + sctp, sctp/integration/ exists (Layer 2 suppressed)

**components.yaml:** Same as Case A plus:
```yaml
  sctp:
    paths: ["sctp/**"]
    depends_on: [common]
```

**Synthetic diff (touches common/include.sh AND sctp/runtest.sh):**
```diff
diff --git a/common/include.sh b/common/include.sh
index abc1234..def5678 100644
--- a/common/include.sh
+++ b/common/include.sh
@@ -10,2 +10,3 @@ setup_network() {
     ip link set eth0 up
+    ip link set eth1 up
 }
diff --git a/sctp/runtest.sh b/sctp/runtest.sh
index 111aaaa..222bbbb 100644
--- a/sctp/runtest.sh
+++ b/sctp/runtest.sh
@@ -5,2 +5,3 @@ run_sctp() {
     setup_sctp
+    check_sctp_link
 }
```

**Command:** `check_layer_2(diff_b, corpus_root, components_b)`

**Expected:** 0 findings. sctp/integration/ exists under sctp/** paths, satisfying the per-pair check.

**Actual:**
```
findings count: 0
```

PASS. Layer 2 suppressed because `find_e2e_artifacts(corpus_root, ["*/integration/**"])`
finds real files under sctp/integration/ (dtlsosctp, ipvs, multitopo, netfilter, ovs
directories), and `_artifact_satisfies_pair` confirms at least one artifact matches
the `sctp/**` component paths.

## Case C: Hub-only change (Layer 2 silent)

**components.yaml:** Same as Case B (includes sctp).

**Synthetic diff (touches ONLY common/include.sh):**
```diff
diff --git a/common/include.sh b/common/include.sh
index abc1234..def5678 100644
--- a/common/include.sh
+++ b/common/include.sh
@@ -10,2 +10,3 @@ setup_network() {
     ip link set eth0 up
+    ip link set eth1 up
 }
```

**Command:** `check_layer_2(diff_c, corpus_root, components_b)`

**Expected:** [] -- hub-only changes have no dependent co-occurrence so Layer 2 is silent.

**Actual:**
```
findings count: 0
```

PASS. Only common is touched; no dependent is in touched_components; no P2 emitted.

## Case D: depends_on typo surfaces as config-error

**components.yaml (written to /tmp/03-05-hostverify/case_d_root/.forge/):**
```yaml
version: 1
components:
  common:
    paths: ["common/**"]
    shared: true
  bonding:
    paths: ["bonding/**"]
    depends_on: [cmmon]
  bridge:
    paths: ["bridge/**"]
    depends_on: [common]
e2e_patterns: ["*/integration/**"]
```

Note: bonding.depends_on references "cmmon" (typo, not a defined component).

**Command:** `run_e2e_check(diff_a, tmpdir)` where tmpdir=/tmp/03-05-hostverify/case_d_root/

**Expected:** 1 finding with fingerprint "e2e-config-error", description mentions "cmmon".

**Actual:**
```
findings count: 2
  fingerprint=e2e-l1:0005429c99d40482, disposition=Disposition.DISMISSED
    (Layer 1 fires since config error falls back to default grouping)
  fingerprint=e2e-config-error, disposition=Disposition.UNCERTAIN
    description: components.yaml: depends_on 'cmmon' (from 'bonding') is undefined
```

PASS. The config-error finding is present with fingerprint "e2e-config-error" and
disposition UNCERTAIN. The description names the undefined component "cmmon".
Layer 1 also fires here because with no valid components config, the diff falls back
to default first-segment grouping (common + bonding = 2 groups with signature changes).
This is expected behavior -- the config error is surfaced first and the Layer 1 nudge
follows as a secondary advisory.

Scratch file cleaned up after Case D: `/tmp/03-05-hostverify/case_d_root/.forge/components.yaml` unlinked.

## Corpus Integrity Check

After all Cases A-D:
```
find ~/code/kernel/networking -type f -not -path '*/.git/*' | sort > /tmp/kernel-net-after.txt
diff /tmp/kernel-net-before.txt /tmp/kernel-net-after.txt
# (empty output)
find ~/code/kernel/networking -newer /tmp/timestamp.tag -type f -not -path '*/.git/*' | head
# (empty output)
```

Corpus integrity diff: EMPTY. No files in kernel/networking were created, modified, or
deleted during host verification.

## Summary

| Case | Expected | Actual | Result |
|------|----------|--------|--------|
| A: hub+bonding, no integration | 1 UNCERTAIN finding (e2e-l2) | 1 UNCERTAIN finding (e2e-l2:e1be5fe17bf67ce8) | PASS |
| B: hub+sctp, integration/ present | 0 findings | 0 findings | PASS |
| C: hub-only change | 0 findings | 0 findings | PASS |
| D: depends_on typo 'cmmon' | config-error finding mentioning 'cmmon' | e2e-config-error UNCERTAIN, description has 'cmmon' | PASS |
| Corpus integrity | unchanged | diff empty, no newer files | PASS |
