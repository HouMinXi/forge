# Boundary Cases by Data Type

Edge cases that commonly cause bugs in production.

## Strings

| Case | Value | Common Bug |
|------|-------|------------|
| Empty | `""` | Unhandled empty check, division by zero on length |
| Whitespace only | `"   "` | Treated as non-empty, fails validation |
| Single char | `"a"` | Off-by-one in substring operations |
| Very long | 10MB+ string | Memory exhaustion, buffer overflow |
| Unicode | `"你好🎉"` | Byte vs character length mismatch |
| Newlines | `"line1\nline2"` | Breaks line-based parsing |
| Null bytes | `"foo\x00bar"` | String truncation in C-style APIs |
| Control chars | `"\r\n\t\b"` | Terminal escape injection |

## Numbers

| Case | Value | Common Bug |
|------|-------|------------|
| Zero | `0` | Division by zero, empty array index |
| Negative | `-1`, `-999` | Unsigned overflow, invalid array index |
| Max int | `2147483647` (32-bit) | Overflow on +1 |
| Min int | `-2147483648` (32-bit) | Overflow on negation |
| Float precision | `0.1 + 0.2` | Not equal to `0.3` |
| Infinity | `float('inf')` | Breaks comparisons |
| NaN | `float('nan')` | Breaks equality checks |
| Scientific notation | `1e10` | Parsing as string fails |

## Collections

| Case | Value | Common Bug |
|------|-------|------------|
| Empty array | `[]` | Index out of bounds on `arr[0]` |
| Single element | `[1]` | Loop assumes multiple elements |
| Duplicates | `[1, 1, 1]` | Set conversion loses data |
| Very large | 1M+ elements | Memory exhaustion, O(n^2) algorithms |
| Nested empty | `[[]]` | Depth check fails |
| Mixed types | `[1, "two", None]` | Type assumptions break |

## Files

| Case | Value | Common Bug |
|------|-------|------------|
| Non-existent | `/no/such/file` | Unhandled FileNotFoundError |
| Empty file | 0 bytes | Read returns empty, breaks parsing |
| Permission denied | `chmod 000 file` | Unhandled PermissionError |
| Directory not file | `/tmp/` | IsADirectoryError |
| Symlink | `ln -s target link` | Follows link unexpectedly |
| Very large | 10GB+ | Memory exhaustion on read() |
| Binary data | Non-UTF8 bytes | UnicodeDecodeError |

## Network

| Case | Scenario | Common Bug |
|------|----------|------------|
| Timeout | Server doesn't respond | Hangs forever without timeout |
| Connection refused | Port closed | Unhandled ConnectionRefusedError |
| DNS failure | Invalid hostname | Unhandled socket.gaierror |
| Partial response | Server closes mid-stream | Incomplete data treated as valid |
| Malformed JSON | `{"key": "value"` | json.JSONDecodeError |
| HTTP 429 | Rate limit | Retry loop without backoff |
| HTTP 500 | Server error | Treated as success |

## Dates/Times

| Case | Value | Common Bug |
|------|-------|------------|
| Epoch | `1970-01-01 00:00:00` | Treated as null/unset |
| Leap year | `2024-02-29` | Invalid date in non-leap year |
| DST transition | `2024-03-10 02:30` | Non-existent time |
| Timezone edge | `UTC+14` vs `UTC-12` | Date changes across zones |
| Far future | `9999-12-31` | Overflow in timestamp conversion |

## Concurrency

| Case | Scenario | Common Bug |
|------|----------|------------|
| Race condition | Two threads write same file | Corrupted data |
| Deadlock | A waits for B, B waits for A | Infinite hang |
| Lock timeout | Lock held too long | Fallback not implemented |
| Signal interruption | SIGINT during I/O | Partial write |

## Testing Strategy

1. **Identify data types**: What inputs does the code accept?
2. **Select relevant cases**: Match boundary cases to data types
3. **Execute with boundary input**: Replace normal input with edge case
4. **Verify graceful handling**: No crashes, clear error messages
5. **Check side effects**: Files/logs/state not corrupted

## Example Test Script

```bash
#!/bin/bash
# Boundary test for kimi-balance.sh

echo "=== Empty key ==="
KIMI_API_KEY="" ./kimi-balance.sh
echo "Exit code: $?"

echo "=== Whitespace key ==="
KIMI_API_KEY="   " ./kimi-balance.sh
echo "Exit code: $?"

echo "=== Invalid JSON response ==="
response='{"invalid json' ./kimi-balance.sh
echo "Exit code: $?"

echo "=== Very long key (10KB) ==="
KIMI_API_KEY=$(python3 -c "print('x' * 10000)") ./kimi-balance.sh
echo "Exit code: $?"
```
