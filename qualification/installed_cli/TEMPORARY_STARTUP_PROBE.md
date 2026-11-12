# Temporary independent startup observation

This branch replaces the capture workflow with one default-off manual probe. It
is not intended for merging into main. Dispatch requires the exact reviewed SHA.

The fixed system interpreter runs with `-I -S -B`. System startup candidates are
observed by directory-entry name/stat only, never imported or copied. The existing
validator receives system roots with a synthetic launcher/config fixture. Separate
synthetic empty-root and inert-hook controls establish its admission behavior.
This is not the real wheel's `verify_installation` path, a target CLI run, or proof
of the historical failure cause. A system rejection is an observation, not a
successful qualification. Failed controls produce UNAVAILABLE and nonzero status.

Only a separately validated closed JSON envelope is uploaded. No candidate
contents, paths, exception messages, environment, or private stderr are published.
There are no installations, namespace commands, hook executions, security-policy
changes, or target workloads. Only private temporary fixtures are created/deleted.
Local tests use synthetic roots on Python 3.12; they do not qualify Ubuntu 26.04.
