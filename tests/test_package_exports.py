"""The package's exit-code re-export is complete and declared.

Two failure modes this pins, both of which had already happened:

1. Seven F401 warnings, because a deliberate re-export without __all__ is
   indistinguishable from seven forgotten imports. Lint noise that has to
   be explained away every run trains readers to skip lint output.

2. EXIT_UNRELIABLE existed in exit_codes but was missing from the import
   block while every sibling was present, so `from code_forge import
   EXIT_UNRELIABLE` raised ImportError with no way for a caller to have
   predicted which constants were re-exported and which were not. A partial
   re-export is worse than no re-export, because it looks complete.

The completeness test is the one that matters: it derives the expected set
from exit_codes rather than restating it, so a constant added there fails
here until it is exported, instead of being silently unreachable from the
package root.
"""

import code_forge
from code_forge import exit_codes


def _exit_constants(namespace) -> set[str]:
    return {n for n in dir(namespace) if n.startswith("EXIT_")}


class TestReExportCompleteness:
    def test_every_exit_code_is_reachable_from_the_package_root(self):
        missing = _exit_constants(exit_codes) - _exit_constants(code_forge)
        assert not missing, (
            f"defined in exit_codes but not importable from code_forge: "
            f"{sorted(missing)}"
        )

    def test_all_lists_every_re_exported_constant(self):
        exported = {n for n in code_forge.__all__ if n.startswith("EXIT_")}
        assert exported == _exit_constants(exit_codes)

    def test_all_names_actually_exist(self):
        # A typo in __all__ is invisible until someone runs `import *`.
        missing = [n for n in code_forge.__all__ if not hasattr(code_forge, n)]
        assert not missing, f"__all__ names nothing: {missing}"

    def test_values_are_the_same_object_not_a_copy(self):
        for name in _exit_constants(exit_codes):
            assert getattr(code_forge, name) is getattr(exit_codes, name)


class TestVersionIsExported:
    def test_version_is_in_all(self):
        assert "__version__" in code_forge.__all__

    def test_version_is_a_string(self):
        assert isinstance(code_forge.__version__, str)
        assert code_forge.__version__
