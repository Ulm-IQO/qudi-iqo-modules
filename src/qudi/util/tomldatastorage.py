from io import TextIOWrapper, BytesIO
import datetime

import tomli
import tomli_w

import numpy as np

from qudi.util.datastorage import TextDataStorage, _is_dtype_class, _is_dtype_str, _dtype_to_str, get_header_from_file

def sanitize_header(header):
    if isinstance(header, (int, float, bool, str, datetime.datetime, datetime.date, datetime.time)):
        return header
    elif isinstance(header, (list, np.ndarray)):
        return [sanitize_header(elem) for elem in header]
    elif isinstance(header, dict):
        return {k:sanitize_header(elem) for (k,elem) in header.items()}
    else:
        return repr(header)

class TOMLHeaderDataStorage(TextDataStorage):
    """Helper class to store (measurement)data on disk as CSV file.
    This is a specialized sub-class of TextDataStorage that uses hard-coded commas as delimiter and
    includes column headers uncommented in the first row of data. This is the standard format for
    importing a table into e.g. MS Excel.
    """

    def create_header(self, timestamp=None, metadata=None, notes=None, column_headers=None,
                      column_dtypes=None):
        """Include column_headers without line comment specifier.
        for more information see: qudi.util.datastorage.TextDataStorage.create_header()
        """
        metadata = self.get_unified_metadata(metadata)
        header = {}
        header['General'] = { }
        if timestamp:
            header['General']['timestamp'] = timestamp
        header['General']['delimiter'] = self.delimiter
        header['General']['comments'] = self.comments
        if column_dtypes:
            if _is_dtype_class(column_dtypes):
                header['General']['column_dtypes'] = _dtype_to_str(column_dtypes)
            elif _is_dtype_str(column_dtypes):
                header['General']['column_dtypes'] = column_dtypes
            else:
                try:
                    header['General']['column_dtypes'] = [_dtype_to_str(t) for t in column_dtypes]
                except TypeError:
                    raise TypeError(f'Unknown column_dtypes "{column_dtypes}".\nMust either be dtype '
                                    f'name str ("int", "float", "complex", "str"), dtype class (int, '
                                    f'float, complex, str, numpy.float32, etc.) or sequence of the '
                                    f'afore mentioned formats.')
        if column_headers:
            header['General']['column_headers'] = column_headers
        if notes:
            header['General']['notes'] = notes
        if metadata:
            header['Metadata'] = metadata
        header = sanitize_header(header)
        buffer = BytesIO()
        tomli_w.dump(header, buffer)
        buffer.seek(0)
        header_lines = TextIOWrapper(buffer).read().splitlines()
        buffer.close()
        header_lines.append('---- END HEADER ----')
        line_sep = f'\n{self.comments}'
        return f'{self.comments}{line_sep.join(header_lines)}\n'

    @staticmethod
    def load_data(file_path):
        """See: DataStorageBase.load_data()

        file_path : str, optional
            Path to file to load data from.
        """
        # Read back metadata

        header, header_lines = get_header_from_file(file_path)
        header = tomli.loads(header)
        general = header.get("General", {})
        metadata = header.get("Metadata", {})
        # Determine dtype specifier from general header section
        dtype = general['column_dtypes']
        if dtype is not None and not isinstance(dtype, type):
            # If dtypes differ, construct a structured array
            if all(dtype[0] == typ for typ in dtype):
                dtype = dtype[0]
            elif str in dtype:
                # handle str type separately since this is (arguably) a bug in numpy.genfromtxt
                dtype = None
            else:
                dtype = [(f'f{col:d}', typ) for col, typ in enumerate(dtype)]
        # Load data from file and skip header
        start_line = header_lines + 1
        if general['column_headers']:
            start_line += 1
        data = np.genfromtxt(file_path,
                             dtype=dtype,
                             comments=general['comments'],
                             delimiter=general['delimiter'],
                             skip_header=start_line)
        return data, metadata, general
