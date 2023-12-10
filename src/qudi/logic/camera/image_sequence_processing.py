import numpy as np

def get_measurement_data(measurement) -> np.ndarray:
    return measurement.data

def get_measurement_slice(measurement_data, text_slice:str):
    def namestr(obj, namespace):
        return [name for name in namespace if namespace[name] is obj]
    names = namestr(measurement_data, locals())
    return eval(names[0] + text_slice)

def bin_measurement(sliced_data:np.ndarray, binnings:tuple) -> np.ndarray:
        # First we need to calculate the new shape
        """
        See https://scipython.com/blog/binning-a-2d-array-in-numpy/
        for how it works (in this case generalized for 3 D arrays
        """
        print(f"sliced_data_shape {sliced_data.shape}, binnings shape {binnings}")
        cur_shape = sliced_data.shape
        # first need to make sure that the shape is correct (3D)
        if len(cur_shape) == 1:
            sliced_data = sliced_data[np.newaxis, np.newaxis, :]
        elif len(cur_shape):
            sliced_data = sliced_data[np.newaxis, :, :]

        cur_shape = sliced_data.shape
        print(f"cur_shape {cur_shape}")
        bx, by, bz = binnings
        # The new shape needs to be a multiple of binnings in each direction but snaller than
        # the cur shape
        print(f"input to calculate new size along axis {bx}, {cur_shape[0]}")
        nl0 = calculate_new_size_along_axis(bx, cur_shape[0])
        nl1 = calculate_new_size_along_axis(by, cur_shape[1])
        nl2 = calculate_new_size_along_axis(bz, cur_shape[2])
        new_shape = [nl0, nl1, nl2]
        shape = (new_shape[0], sliced_data.shape[0] // new_shape[0],
                 new_shape[1], sliced_data.shape[1] // new_shape[1],
                 new_shape[2], sliced_data.shape[2] // new_shape[2])
        # the additional slicing is to ensure that the size of an axis is truly a multiple
        # of the binning.
        return sliced_data[0:nl0 * bx, 0:nl1 * by, 0:nl2 * bz].reshape(shape).mean(-1).mean(3).mean(1)

def calculate_new_size_along_axis(bin:int, cur_length:int) -> int:
    mult = cur_length // bin
    # in the case the binning is larger than the cur length
    # we just skip the binning.
    if mult == 0:
        return cur_length
    else:
        return mult

def process_image_sequence(measurement_data, text_slice:str, binnings:tuple) -> np.ndarray:
        sliced_data = get_measurement_slice(measurement_data, text_slice)
        print(f"shape of sliced data {sliced_data.shape}")
        measurement_data_slice = bin_measurement(sliced_data, binnings)
        # as a last step we remove unecessary axes.
        # measurement_data_slice = sliced_data
        return np.squeeze(measurement_data_slice).transpose()

