































































        fileobj.seek(pos)
        return sig == FITS_SIGNATURE
    elif filepath is not None:
        return filepath.lower().endswith(
            (".fits", ".fits.gz", ".fit", ".fit.gz", ".fts", ".fts.gz")
        )
    return isinstance(args[0], (HDUList, TableHDU, BinTableHDU, GroupsHDU))


