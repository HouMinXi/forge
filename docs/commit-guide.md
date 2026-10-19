# Forge commit messages

Use `.gitmessage` to explain why a change is needed. Choose a subsystem and
case that identify the affected behavior, then write a short subject:

```text
<subsystem>/<case>: <summary>

<problem and rationale>

Signed-off-by: Minxi Hou <houminxi@gmail.com>
```

The body should explain the problem, its effect, and why the chosen change
addresses it. Use English ASCII and enough context for someone who has not
seen the discussion. Mention verification only when you actually ran it.

Keep plan/task/decision IDs, severity labels, review/tool/model vocabulary,
inventory headings such as `Changes:`, `Added:`, or `Fixed:`, and AI co-author
trailers out of the message. A sign-off must be truthful: the template names
the owner, Minxi Hou. Keep it only when authorized to commit as the owner.
Otherwise replace it with your own actual name and email; do not impersonate
the owner or change Git identity to match the sample.

## Illustrative examples

These hypothetical messages illustrate wording, not delivered changes or
verification results. Their owner sign-off is subject to the rule above.

```text
docs/commits: keep contribution rationale close to the editor

Contributors can lose the reason for a change when the message starts from
a blank page. A shared prompt keeps the problem and rationale visible while
the message is being written.

Signed-off-by: Minxi Hou <houminxi@gmail.com>
```

```text
runner/cleanup: preserve results when temporary files are already gone

A temporary file can disappear before cleanup runs. Treating that absence
as an error hides the completed result, so cleanup tolerates a missing file
while preserving other failures.

Signed-off-by: Minxi Hou <houminxi@gmail.com>
```

## Use the template once

Run from the repository root after staging and checking the intended files.
The command opens your Git editor; replace every placeholder before saving.
These examples assume Git's default `#` comment prefix. If `core.commentChar`
or `core.commentString` selects another prefix, resolve the cleanup behavior
before using this comment-bearing template. With `#`, `--cleanup=strip`
removes the instructional comment lines.

```sh
git commit --template .gitmessage --cleanup=strip # docs
```

For a completed message file, copy `.gitmessage` to a temporary file outside
the repository, then edit its subject, body, and sign-off. Replace the path
below with that file. Explicit cleanup also strips comments with `--file`:

```sh
git commit --file /tmp/forge-commit-message.txt --cleanup=strip # docs
```

Choose one of these commit methods. Neither saves Git configuration or
installs a hook. After the commit succeeds, verify it in a separate shell
invocation:

```sh
git log -1 --format=full
```

## Match the change classification

The commands above are for documentation-only changes. The unquoted
`# docs` marker is the final shell token; it is not part of the message.
Do not append verification with `&&`: a shell comment would swallow it.
Use the matching `# config`, `# chore`, or `# wip` marker only for changes
that qualify under the existing rules.

Logic-bearing changes retain their existing tests, real defect-injection
checks, and three consecutive clean review cycles before commit, plus the
required smoke checks. A documentation marker or a well-written message
does not satisfy those gates. These examples create no new exemptions.
