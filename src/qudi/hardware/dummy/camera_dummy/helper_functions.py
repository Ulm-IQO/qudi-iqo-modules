import copy
import numpy as np
from qudi.core.logger import get_logger

logger = get_logger(__name__)


def create_boundaries(val_min, val_max, step):
    return {'min': val_min, 'max': val_max, 'step': step}


def combine_boundaries(boundary0, boundary1):
    x0, x1, dx = boundary0['min'], boundary0['max'], boundary0['step']
    y0, y1, dy = boundary1['min'], boundary1['max'], boundary1['step']
    kmax = (x1 - x0) // dx
    rmax = (y1 - y0) // dy
    vals = []
    for k in range(kmax):
        for r in range(rmax):
            x_val = x0 + k * dx
            y_val = y0 + r * dy
            vals.append(x_val * y_val)
    return sorted(vals)


def valid_value(val, boundary):
    val_arr = array_from_boundary(boundary)
    if np.any(np.isclose(val, val_arr)):
        return True
    return False


def array_from_boundary(boundary):
    return np.arange(boundary['min'], boundary['max'], boundary['step'])


def valid_binning(binning, crop):
    b0, b1 = binning
    c0, c1 = crop
    # add one because we start from zero
    width = c0[1] - c0[0] + 1
    height = c1[1] - c1[0] + 1
    cond0 = width % b0 == 0
    cond1 = height % b1 == 0
    if cond0 & cond1:
        return True
    else:
        return False


def valid_crop(crop, binning, sensor_size):
    # first check if anything lies outside the boundaries
    cropx, cropy = crop
    bound_low = 0
    bound_highx, bound_highy = sensor_size
    if (cropx[0] | cropy[0]) < bound_low:
        return False
    elif (cropx[1] > bound_highx) | (cropy[1] > bound_highy):
        return False

    # the number of pixels must be a multiple of the
    # binning
    npx_x = cropx[1] - cropx[0]
    npx_y = cropy[1] - cropy[0]
    if npx_x % binning[0] != 0:
        return False
    if npx_y % binning[1] != 0:
        return False
    return True


def valid_readout_freq(freq, direction, amps, readout_freqs):
    """
    Logic specific for the hardware.
    """
    if 'EM' in amps:
        if not freq in readout_freqs['EM'][direction]:
            return False
    else:
        if not freq in readout_freqs['preamp'][direction]:
            return False
    return True


def valid_exposures(exposures, exposure_range, max_exposures):
    avail_exposures = array_from_boundary(exposure_range)
    if len(exposures) > max_exposures:
        return False

    # logger.error(exposures)
    # logger.error(avail_exposures)
    for exposure in exposures:
        # logger.error(type(exposure))
        # logger.error(type(avail_exposures))
        if not np.any(np.isclose(exposure, avail_exposures)):
            return False
    return True


def calc_new_number_images(full_size_number, size, binning, crop):
    """
    Calculate the number  
    :param full_size_number: 
    :param size: 
    :param binning: 
    :param crop: 
    :returns: 

    """
    # the memory size should be a constant
    b0, b1 = binning
    h0, h1 = crop[0]
    v0, v1 = crop[1]
    npx_crop = (h1 - h0) * (v1 - v0)
    npx_total = size[0] * size[1]
    return int(full_size_number * npx_total / (b0 * b1 * npx_crop))


def calc_image_dimensions(binning, crop):
    b0, b1 = binning
    h0, h1 = crop[0]
    v0, v1 = crop[1]
    return (h1 - h0) // b0, (v1 - v0) // b1

# Dealing with the readout time cosntraint
def calc_readout_time(crop, binning, hrfq, vrfq, frame_transfer):
        crop_hor = crop[0]
        crop_ver = crop[1]

        eff_region = ((crop_hor[0] // binning[0], crop_hor[1] // binning[1]),
                      (crop_ver[0] // binning[1], crop_ver[1] // binning[1]))

        hor_shifts_needed = eff_region[0][1]
        ver_shifts_needed = eff_region[1][1]

        if not frame_transfer:
            readout_time = ver_shifts_needed / vrfq + ver_shifts_needed * hor_shifts_needed / hrfq
        else:
            # only an approximation in the case that the time to shift an image to
            # the secondary sensor is lower than the exposure time.
            readout_time = ver_shifts_needed / vrfq
        # depending on where we are need to calculate how
        # many horizontal and vertical shifts are to perform
        return readout_time


def readout_times_qudi_transform(horizontal_freqs, vertical_freqs,
                                 crop, binning, frame_transfer):
    readout_times = list(calc_readout_time(crop, binning, hrfq, vrfq, frame_transfer)
                         for hrfq in horizontal_freqs
                         for vrfq in vertical_freqs)
    return readout_times


def readout_freq_hw_transform(available_readout_freqs, amp, dir):
    return available_readout_freqs[amp][dir]


# Dealing with the sensitivity constraint
def gains_hw_transform(available_amplifiers, amp, var_amp):
    gain = 1
    # logger.error(available_amplifiers)
    # logger.error(amp)
    # logger.error(var_amp)
    for amplifier in amp:
        if amplifier != var_amp:
            gain *= amp[amplifier]

    available_gains = array_from_boundary(available_amplifiers[var_amp])
    return [gain * gain_step for gain_step in available_gains]


def gain_from_amp_chain(amp):
    gain = 1
    for amplifier in amp:
        gain *= amp[amplifier]
    return gain


def get_readout_amp(amps):
    if 'EM' in amps:
        return 'EM'
    else:
        return 'preamp'

# dealing acquisition setting
def valid_acquisition_setting(setting):
    """
    Check if a given acquisition setting is valid.
    Seq_runs is element of the natural numbers + {-1}.
    Images_per_rep is a natural number.
    @param setting tuple (seq_runs, images_per_rep): seq_runs how often we run through sequence,
                                                 step reps how many images a sequence contains.
    @return:
    """
    seq_runs, images_per_rep = setting
    if isnatnumom1(seq_runs) and isnatnumom1(images_per_rep):
        return True
    else:
        return False


def num_images_from_measurement_time(exposures, runtime, rt):
    """ Calculate the number of images that the dummy camera has recorded

    :param exposures: List containing the exposure times 
    :param runtime: time the measurement is running 
    :param rt: time to readout an image given the current settings
    :returns: Number of new images
    """
    
    images_per_cycle = len(exposures)
    cycle_time = sum(exposures) + images_per_cycle * rt
    cycles = int(runtime / cycle_time)
    num_images = cycles * images_per_cycle
    remaining_time = runtime - cycles * cycle_time

    # run through the incomplete cycle
    extra_time = 0.0
    for exposure in exposures:
        extra_time += exposure
        if (extra_time + rt) <= remaining_time:
            num_images += 1
    return num_images


def val_between_boundaries(val, boundaries):
    low_bound, up_bound = boundaries
    if low_bound <= val <= up_bound:
        return True
    else:
        return False


def outside_constraints(caller):
    return f'requested {caller} lies outside constraints'

# From here on more general helper functions
def pos_in_array(val, arr):
    for ii, el in enumerate(arr):
        if np.isclose(val, el):
            return ii
    return False


def tulis_to_array(lot):
    """
    Given a list of tuples [(a0, ... , ak), (b0, ..., bk) ... ]
    return array of the form [a0, ..., ak; b0, ..., bk; ... ]
    """
    # TODO There got to be a more pythonic way to do this.
    tuple_len = len(lot[0])
    lis_len = len(lot)
    total_arr = np.zeros((lis_len, tuple_len))
    for ii, line in enumerate(lot):
        total_arr[ii, :] = np.array(line)

    return total_arr


def tulis_to_el_lis(lot):
    """
    Given a list of tuples [(a0, .. , ak), (b0, ..., bk) ... ]
    return [[a0, b0, ...], [a1, b1, ...], ... ]
    :param lot:
    :return:
    """
    tuple_len = len(lot[0])
    liofli = [[] for i in range(tuple_len)]

    for tu in lot:
        for ii, el in enumerate(tu):
            liofli[ii].append(el)

    return liofli


def element_pos(pivot, lis):
    """
    Find the position of an element in a list
    @param lis:
    @return:
    """
    for ii, el in enumerate(lis):
        if pivot == el:
            return ii
    return False


def get_fields(cls_obj, req_fields):
    attrs = list()
    for field in req_fields:
        try:
            attrs.append(getattr(cls_obj, field))
        except AttributeError:
            logger.error('object has no attribute {}'.format(field))

    return attrs

def update_dictionary(di, path, new_val):
    cur_di = di
    final_key = path.pop()
    print('help')
    print(di, path, new_val)
    for step in path:
        cur_di = cur_di[step]
    cur_di[final_key] = new_val
    return

def red_dictionary(input_dict, ch_key):
    input_dict_keys = set(input_dict.keys())
    input_dict_keys.remove(ch_key)
    remaining_keys = input_dict_keys
    if remaining_keys:
        red_dict = {key: input_dict[key] for key in remaining_keys}
    else:
        red_dict = dict()
    return red_dict

def isnatnum(num):
    if int(num) > 0:
        return True
    else:
        return False


def isnatnumom1(num):
    if isnatnum(num):
        return True
    elif num == -1:
        return True
    else:
        return False

def custom_smaller_than(a, b):
    """
    -1 is in fact the largest number and
    nothing compares to her
    """
    if a == -1:
        return False
    elif b == -1:
        return True
    else:
        return a < b

