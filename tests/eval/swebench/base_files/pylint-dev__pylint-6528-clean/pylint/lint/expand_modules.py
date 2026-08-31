












































    return any(file_pattern.match(element) for file_pattern in ignore_list_re)


def expand_modules(
    files_or_modules: Sequence[str],
    ignore_list: list[str],










    for something in files_or_modules:
        basename = os.path.basename(something)
        if (
            basename in ignore_list
            or _is_in_ignore_list_re(os.path.basename(something), ignore_list_re)
            or _is_in_ignore_list_re(something, ignore_list_paths_re)
        ):
            continue
        module_path = get_python_path(something)
