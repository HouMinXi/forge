
































        `range(n_features)`.

    index : array-like, default=None
        Index for data.

    Returns
    -------














    if isinstance(data_to_wrap, pd.DataFrame):
        if columns is not None:
            data_to_wrap.columns = columns
        if index is not None:
            data_to_wrap.index = index
        return data_to_wrap

    return pd.DataFrame(data_to_wrap, index=index, columns=columns)
