from collections import MutableMapping


class ImmutableKeyDict(MutableMapping):
    """
    Dictionary-like class that only allows the change of values
    of already existing keys. No new keys may be added after 
    the creation of an instance.
    """

    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]
    
    def __setitem__(self, key, value):
        if key not in self._data:
            raise KeyError(key)
        self._data[key] = value
    
    def __delitem__(self, key):
        raise NotImplementedError
    
    def __iter__(self):
        return iter(self._data)
    
    def __len__(self):
        return len(self._data)
