


















































    def __mro_entries__(self, bases: Tuple) -> Tuple:
        return (self.__class__,)

    def __getitem__(self, key: str) -> "_MockObject":
        return _make_subclass(key, self.__display_name__, self.__class__)()

    def __getattr__(self, key: str) -> "_MockObject":
        return _make_subclass(key, self.__display_name__, self.__class__)()
