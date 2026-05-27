# Concurrency Testing Patterns

Test scenarios for race conditions, deadlocks, and lock contention.

## When to Test Concurrency

- Code uses locks (`flock`, `fcntl`, mutexes)
- Code modifies shared files (state files, logs, caches)
- Code spawns background processes
- Code uses signals (SIGINT, SIGTERM)
- Code has check-then-act patterns (TOCTOU)

## Race Condition Patterns

### 1. Check-Then-Act (TOCTOU)

**Vulnerable code**:
```bash
if [[ ! -f "$LOCKFILE" ]]; then
    touch "$LOCKFILE"  # Race: another process can create between check and touch
    # critical section
fi
```

**Test**:
```bash
# Spawn 10 concurrent instances
for i in {1..10}; do
    ./script.sh &
done
wait

# Verify only one lockfile created
ls -l "$LOCKFILE"
```

### 2. Read-Modify-Write

**Vulnerable code**:
```bash
count=$(cat counter.txt)
count=$((count + 1))
echo "$count" > counter.txt  # Race: another process can write between read and write
```

**Test**:
```bash
echo "0" > counter.txt
for i in {1..100}; do
    ./increment.sh &
done
wait

# Verify counter is 100 (not less due to lost updates)
cat counter.txt
```

### 3. File Append Without Lock

**Vulnerable code**:
```bash
echo "log entry" >> logfile.txt  # Race: interleaved writes
```

**Test**:
```bash
> logfile.txt
for i in {1..100}; do
    (echo "entry-$i" >> logfile.txt) &
done
wait

# Verify 100 complete lines (no partial/interleaved entries)
wc -l logfile.txt
grep -c "^entry-" logfile.txt
```

## Deadlock Patterns

### 1. Lock Ordering Violation

**Vulnerable code**:
```bash
# Process A
flock 9
flock 10

# Process B
flock 10  # Deadlock: A holds 9 waits for 10, B holds 10 waits for 9
flock 9
```

**Test**:
```bash
# Spawn two processes with opposite lock order
(flock 9; sleep 1; flock 10; echo "A done") &
(flock 10; sleep 1; flock 9; echo "B done") &

# Wait with timeout
timeout 5 wait || echo "DEADLOCK DETECTED"
```

### 2. Self-Deadlock

**Vulnerable code**:
```bash
flock 9
# ... code that calls itself recursively ...
flock 9  # Deadlock: trying to acquire same lock twice
```

**Test**:
```bash
# Call script recursively
DEPTH=0 ./script.sh
# Inside script: DEPTH=$((DEPTH + 1)); if [[ $DEPTH -lt 3 ]]; then ./script.sh; fi

# Verify completes without hanging
timeout 5 ./script.sh || echo "SELF-DEADLOCK DETECTED"
```

## Lock Contention Patterns

### 1. High Contention (Many Writers)

**Test**:
```bash
# Spawn 100 concurrent writers
for i in {1..100}; do
    ./write-with-lock.sh "data-$i" &
done
wait

# Verify all writes succeeded
wc -l output.txt  # Should be 100
```

### 2. Lock Timeout Fallback

**Test**:
```bash
# Hold lock indefinitely in background
(flock 9; sleep 60) &
HOLDER_PID=$!

# Try to acquire with timeout
flock -w 2 9 || echo "TIMEOUT - fallback triggered"

kill $HOLDER_PID
```

### 3. Non-Blocking Lock

**Test**:
```bash
# Hold lock in background
(flock 9; sleep 5) &

# Try non-blocking acquire
flock -n 9 && echo "ACQUIRED" || echo "BUSY - fallback triggered"
```

## Signal Handling Patterns

### 1. Interrupted System Call

**Vulnerable code**:
```bash
curl https://api.example.com  # SIGINT during download leaves partial file
```

**Test**:
```bash
# Start long-running operation
./script.sh &
PID=$!

# Send SIGINT after 1 second
sleep 1
kill -INT $PID

# Verify cleanup happened (temp files removed, locks released)
ls /tmp/script-*.tmp  # Should not exist
flock -n 9 && echo "Lock released" || echo "Lock still held"
```

### 2. Signal During Critical Section

**Test**:
```bash
# Send signal during file write
./script.sh &
PID=$!
sleep 0.5  # Wait until likely in critical section
kill -TERM $PID

# Verify file not corrupted
if [[ -f output.txt ]]; then
    # File should be valid JSON/complete lines/etc.
    jq . output.txt || echo "CORRUPTED"
fi
```

## Testing Tools

### Parallel Execution

```bash
# GNU parallel (if available)
parallel -j 10 ./script.sh ::: {1..100}

# xargs
seq 1 100 | xargs -P 10 -I {} ./script.sh {}

# Bash background jobs
for i in {1..100}; do ./script.sh & done; wait
```

### Stress Testing

```bash
# stress-ng (CPU/memory/I/O stress)
stress-ng --cpu 4 --io 2 --vm 1 --timeout 60s &
STRESS_PID=$!

# Run concurrent tests under stress
for i in {1..50}; do ./script.sh & done
wait

kill $STRESS_PID
```

### Race Detection

```bash
# Run same operation many times to increase race probability
for attempt in {1..1000}; do
    ./script.sh &
done
wait

# Check for corruption
if [[ $(wc -l < state.txt) -ne 1000 ]]; then
    echo "RACE CONDITION DETECTED"
fi
```

## Verification Checklist

After concurrent execution:

- [ ] State files have valid content (not corrupted)
- [ ] Log files have expected number of entries (no lost writes)
- [ ] No "text file busy" errors
- [ ] No orphaned lock files
- [ ] No zombie processes (`ps aux | grep defunct`)
- [ ] Exit codes are correct (not all failures)
- [ ] Temp files cleaned up (`ls /tmp/script-*`)

## Common Bugs Found

| Bug | Symptom | Root Cause |
|-----|---------|------------|
| Lost updates | Counter is 87 instead of 100 | Read-modify-write without lock |
| Corrupted files | JSON parse error | Interleaved writes |
| Orphaned locks | Second run hangs forever | Lock not released on error |
| Partial writes | File has incomplete lines | Signal during write |
| Deadlock | Process hangs indefinitely | Lock ordering violation |

## Example Test Script

```bash
#!/bin/bash
# Concurrency test for kimi-next-key.sh

STATE_FILE="$HOME/.config/kimi/state"
echo "0" > "$STATE_FILE"

echo "=== Test 1: 10 concurrent calls ==="
for i in {1..10}; do
    ./kimi-next-key.sh > /dev/null &
done
wait

# Verify state file not corrupted
if [[ $(cat "$STATE_FILE") =~ ^[0-9]+$ ]]; then
    echo "PASS: state file valid"
else
    echo "FAIL: state file corrupted"
fi

echo "=== Test 2: Lock contention fallback ==="
# Hold lock in background
(flock 9; sleep 5) 9>"$STATE_FILE.lock" &
HOLDER=$!

# Try to acquire (should fallback)
output=$(./kimi-next-key.sh 2>&1)
if echo "$output" | grep -q "unvalidated fallback"; then
    echo "PASS: fallback triggered"
else
    echo "FAIL: no fallback message"
fi

kill $HOLDER 2>/dev/null
```
