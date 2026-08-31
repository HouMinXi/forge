
























    """
    xarray version of pandas.core.nanops._maybe_null_out
    """

    if axis is not None and getattr(result, "ndim", False):
        null_mask = (np.take(mask.shape, axis).prod() - mask.sum(axis) - min_count) < 0
        if null_mask.any():
            dtype, fill_value = dtypes.maybe_promote(result.dtype)
            result = result.astype(dtype)
