from qudi.core.logger import get_logger

import time
from copy import copy
from functools import reduce, partial
from fractions import Fraction

from qudi.hardware.dummy.camera_dummy.helper_functions import *

logger = get_logger(__name__)


class ModelCamera:
    """
    This class encapsulates actual hardware. The CameraDummy then communicates
    with this model. This class is mainly for illustration purposes to see how
    more complex hardware can be abstracted to fit into the existing camera interface.
    """
    _size = (512, 512)
    _binning_sizes = [2 ** i for i in range(10)]
    _readout_freqs = {'preamp': {'vertical': [0.5e6, 1e6, 1.5e6],
                                 'horizontal': [1e6, 3e6, 6e6]},
                      'EM': {'vertical': [0.5e6, 1e6, 1.5e6],
                             'horizontal': [10e6, 30e6, 60e6]}}
    _bit_depth = 16
    datatype = getattr(np, 'int' + str(_bit_depth))
    _qe = 0.8
    # need to adjust the internal memory when adjusting image size ...
    _num_full_size_images = 25
    _internal_memory = np.zeros((_num_full_size_images, _size[0], _size[1]), dtype=datatype)
    _num_exposures = 10
    _exposure_range = create_boundaries(0.01, 100.0, 0.01)
    _amps = {'preamp': create_boundaries(1, 100, 1),
             'EM': create_boundaries(1, 1000, 1)}
    _trigger_modes = ['Internal', 'External', 'Software']
    _shutter_states = [True, False]
    _shutter_speeds = create_boundaries(0.01, 1.0, 0.01)
    _pixel_units = ['Counts', 'Electrons', 'Photons']

    def __init__(self, amp_state={'preamp': 1.0, 'EM': 100.0}, binning_state=(1, 1),
                 crop_state=((0, 511), (0, 511)),
                 readout_freq_state={'vertical': 1e6,
                                     'horizontal': 60e6},
                 acquisition_mode_state='Single Image',
                 trigger_mode_state='Internal',
                 exposures=[0.2],
                 shutter_state=True,
                 shutter_speed_state=0.05,
                 pixel_unit_state='Counts',
                 acquisition_settings=(1, 1),
                 frame_transfer=True):

        self._amp_state = amp_state
        self._binning_state = binning_state
        self._crop_state = crop_state
        self._readout_freq_state = readout_freq_state
        self._acquisition_mode_state = acquisition_mode_state
        self._trigger_mode_state = trigger_mode_state
        self._exposures = exposures
        self._shutter_state = shutter_state
        self._shutter_speed_state = shutter_speed_state
        self._pixel_unit_state = pixel_unit_state
        self._acquisition_settings = acquisition_settings
        self._ft = frame_transfer
        return

    # The constraints imposed by the hardware
    @property
    def size(self):
        return self._size

    @property
    def available_binnings(self):
        return self._binning_sizes

    @property
    def available_readout_freqs(self):
        return self._readout_freqs

    @property
    def available_trigger_modes(self):
        return self._trigger_modes

    @property
    def available_internal_memory(self):
        shp = self._internal_memory.shape
        num_entries = shp[0] * shp[1] * shp[2]
        return self._bit_depth * num_entries

    @property
    def available_amplifiers(self):
        return self._amps

    @property
    def available_number_of_exposures(self):
        return self._num_exposures

    @property
    def available_exposure_times(self):
        return self._exposure_range

    @property
    def available_pixel_units(self):
        return self._pixel_units

    @property
    def available_shutter_states(self):
        return self._shutter_states

    @property
    def available_shutter_speeds(self):
        return self._shutter_speeds

    # the configurable part of the camera
    @property
    def binning(self):
        return self._binning_state

    @binning.setter
    def binning(self, binning):
        # check if this is a valid binning
        if valid_binning(binning, self._crop_state):
            self._binning_state = binning
            # now need to adjust the memory
            num_images = calc_new_number_images(self._num_full_size_images,
                                                self._size, self._binning_state, self._crop_state)
            # for now just erase images

            dim0, dim1 = calc_image_dimensions(self._binning_state, self._crop_state)
            self._internal_memory = np.zeros((num_images, dim0, dim1))
        else:
            logger.error('the requested binning is not available')
        return

    @property
    def crop(self):
        return self._crop_state

    @crop.setter
    def crop(self, crop):
        if valid_crop(crop, self._binning_state, self._size):
            self._crop_state = crop
            num_images = calc_new_number_images(self._num_full_size_images,
                                                self._size, self._binning_state, self._crop_state)
            # for now just erase images

            dim0, dim1 = calc_image_dimensions(self._binning_state, self._crop_state)
            self._internal_memory = np.zeros((num_images, dim0, dim1))
        else:
            logger.error('the requested crop is not available')
        return

    @property
    def amp(self):
        return self._amp_state

    @amp.setter
    def amp(self, amp_dict):
        for amp_name in amp_dict:
            gain = amp_dict[amp_name]
            if valid_value(gain, self._amps[amp_name]):
                self._amp_state[amp_name] = gain
            else:
                logger.error('Impossible gain {} for amplifier {}'.format(gain, amp_name))
        return

    @property
    def readout_freq(self):
        return self._readout_freq_state

    @readout_freq.setter
    def readout_freq(self, freq_dict):
        for direction in freq_dict:
            freq = freq_dict[direction]
            if valid_readout_freq(freq, direction, self._amp_state, self._readout_freqs):
                self._readout_freq_state[direction] = freq
            else:
                logger.error('{} Readout freq  is not available'.format(direction))
        return

    @property
    def trigger_mode(self):
        return self._trigger_mode_state

    @trigger_mode.setter
    def trigger_mode(self, mode):
        if mode in self._trigger_modes:
            self._trigger_mode_state = mode
        else:
            logger.error('Unknown trigger mode {}.'.format(mode))
        return

    @property
    def shutter(self):
        return self._shutter_state

    @shutter.setter
    def shutter(self, state):
        if state in self._shutter_states:
            self._shutter_state = state
        else:
            logger.error('shutter state {} not available.'.format(state))
        return

    @property
    def shutter_speed(self):
        return self._shutter_speed_state

    @shutter_speed.setter
    def shutter_speed(self, speed):
        if valid_value(speed, self._shutter_speeds):
            self._shutter_speed_state = speed
        else:
            logger.error('Required shutter speed is not available.')
        return

    @property
    def acquisition_settings(self):
        return self._acquisition_settings

    @acquisition_settings.setter
    def acquisition_settings(self, settings):
        self._acquisition_settings = settings
        return

    @property
    def exposures(self):
        return self._exposures

    @exposures.setter
    def exposures(self, exposures):
        if valid_exposures(exposures, self._exposure_range, self._num_exposures):
            self._exposures = exposures
        else:
            logger.error('Not valid list of exposures.')
        return

    @property
    def pixel_unit(self):
        return self._pixel_unit_state

    @pixel_unit.setter
    def pixel_unit(self, px_unit):
        if px_unit in self._pixel_units:
            self._pixel_unit_state = px_unit
        return

    @property
    def quantum_efficiency(self):
        return self._qe

    @property
    def bit_depth(self):
        return self._bit_depth

    @property
    def frame_transfer(self):
        return self._ft

    @frame_transfer.setter
    def frame_transfer(self, frame_transfer):
        self._ft = frame_transfer
        return


class ImageGenerator:
    """
    Contains the algorithms to generate dummy images for the dummy hardware.
    """
    def __init__(self, camera, measurement, method='random_images', count_rate=150e3):
        self._cam = camera
        self._cm = measurement
        self._method = method
        self._count_rate = count_rate
        return

    def image_generation(self, exposure):
        # get the current image dimensions
        image_dimensions = calc_image_dimensions(self._cam.binning, self._cam.crop)
        if self._method == 'random_images':
            image = np.random.poisson(self._count_rate * exposure, image_dimensions)
        # now this is the image in photons
        if self._cam.pixel_unit == 'Counts':
            # needs its own multiply
            # make sure we stay in the integer domain (problem is with this we
            # always return less than we should
            scale = Fraction(str(self.total_gain() * self._cam.quantum_efficiency))
            num, denom = scale.numerator, scale.denominator
            image *= num
            image //= denom
        elif self._cam.pixel_unit == 'Electrons':
            scale = Fraction(self._cam.quantum_efficiency)
            num, denom = scale.numerator, scale.denominator
            image *= num
            image //= denom
        return image

    def image_generator(self, num_images):
        num_exposures = len(self._cam.exposures)
        for ii in range(num_images):
            cur_exposure_ind = ii % num_exposures
            yield self.image_generation(self._cam.exposures[cur_exposure_ind])

    def total_gain(self):
        amp_dict = self._cam.amp
        return reduce(lambda x, y: x * y, [amp_dict[_] for _ in amp_dict], 1)


class CameraMeasurement:
    """
    Object that keeps the time during a measurement and tells
    if a measurement has already stopped.
    """
    _measurement_time = 0.0
    _measurement_start = time.time()
    _measurement_running = False
    _readout_images = 0
    # gives the end of a measurement a time tag.
    # The measurement can end due to the completion of the whole measurement sequence
    # or a stop required by the user.
    _measurement_stop = _measurement_start


    def __init__(self, camera):
        self._cam = camera
        self._measurement_duration = self.calc_measurement_duration()
        return

    @property
    def measurement_duration(self):
        return self.calc_measurement_duration()

    @property
    def measurement_state(self):
        return self._measurement_running

    @property
    def readout_images(self):
        """
        Read in the past as in how many images have been readout. 
        """
        return self._readout_images

    @readout_images.setter
    def readout_images(self, new_readouts):
        """
        Already readout images
        """
        self._readout_images += new_readouts
        return

    def run_time(self):
        time_passed = time.time() - self._measurement_start
        if custom_smaller_than(time_passed, self.measurement_duration):
            return time_passed
        else:
            return self.measurement_duration

    def start_measurement(self):
        """
        Reset the relevant parameters of the measurement
        """
        self._measurement_start = time.time()
        self._measurement_time = 0.0
        self._measurement_running = True
        self._readout_images = 0

    def measurement_finished(self, req_stop=False):
        """
        Return if a measurement is finished
        """

        if not self._measurement_running:
            return True

        if req_stop:
            self._measurement_running = False
            self._measurement_stop = time.time()
            return True

        # is a measurement running?
        # did the measurement already finish
        md, rt = self.measurement_duration, self.run_time()
        # as long as measurement duration is smaller than runtime the
        # measurement is running
        if custom_smaller_than(rt, md):
            self._measurement_running = True 
            return False
        else:
            self._measurement_running = False
            return True

    def calc_measurement_duration(self):
        """
        Return the total measurement time
        @return:
        """
        # the total measurement time depends on:
        #  - acquisition settings
        #  - exposure(s)
        #  - readout time
        acquisition_settings = self._cam.acquisition_settings
        seq_runs, images_per_rep = acquisition_settings

        # query the necessary values from the camera
        exposures = self._cam.exposures
        crop, binning = self._cam.crop, self._cam.binning
        readout_freq, ft = self._cam.readout_freq, self._cam.frame_transfer
        hrfq = readout_freq['horizontal']
        vrfq = readout_freq['vertical']
        readout_time = calc_readout_time(crop, binning, hrfq, vrfq, ft)
        if isnatnum(seq_runs) and isnatnum(images_per_rep):
            num_exposures = len(exposures)
            time_for_run = 0.0
            for i in range(images_per_rep):
                run_ind = i % num_exposures
                # for each image give the correct exposure time
                time_for_run += exposures[run_ind] + readout_time
            return seq_runs * time_for_run
        else:
            return -1


class QudiHardwareMapper:
    """
    Certain functions in hardware can work with input values out of a given set A.
    This set A corresponds to a set B according to the hardware interface given
    in Qudi.

    This class contains the functions to transform between the qudi abstraction
    and the raw hardware.
    """
    def __init__(self,  hardware, hw_transform, qudi_transform,
                 hw_req_fields=[],
                 qudi_req_fields=[], hw_kwargs={}, qudi_kwargs={}):
        self._hardware = hardware
        self._hardware_transform = hw_transform
        self._qudi_transform = qudi_transform
        self._hw_req_fields = hw_req_fields
        self._qudi_req_fields = qudi_req_fields

        self._hw_kwargs = hw_kwargs
        self._qudi_kwargs = qudi_kwargs
        self.update_fields()
        return

    @property
    def hardware_required_fields(self):
        return self._hw_req_fields

    @hardware_required_fields.setter
    def hardware_required_fields(self, new_hw_req_fields):
        self._hw_req_fields = new_hw_req_fields
        return

    @property
    def qudi_required_fields(self):
        return self._qudi_req_fields

    @qudi_required_fields.setter
    def qudi_required_fields(self, new_qudi_req_fields):
        self._qudi_req_fields = new_qudi_req_fields
        return

    @property
    def hardware_kwargs(self):
        return self._hw_kwargs

    @hardware_kwargs.setter
    def hardware_kwargs(self, new_hw_kwargs):
        self._hw_kwargs = new_hw_kwargs
        return

    @property
    def hardware_kwargs(self):
        return self._hw_kwargs

    @hardware_kwargs.setter
    def hardware_kwargs(self, new_hw_kwargs):
        self._hw_kwargs = new_hw_kwargs
        self.update_fields()
        return

    @property
    def qudi_kwargs(self):
        return self._qudi_kwargs

    @qudi_kwargs.setter
    def qudi_kwargs(self, new_qudi_kwargs):
        self._qudi_kwargs = new_qudi_kwargs
        self.update_fields()
        return

    @property
    def hardware_transform(self):
        self.update_fields()
        return self._hardware_transform_set

    @hardware_transform.setter
    def hardware_transform(self, hardware_transform):
        self._hardware_transform = hardware_transform
        self.update_fields()
        return

    @property
    def qudi_transform(self):
        self.update_fields()
        return self._qudi_transform_set

    @qudi_transform.setter
    def qudi_transform(self, qudi_transform):
        self._qudi_transform = qudi_transform
        self.update_fields()
        return

    def update_fields(self):
        qudi_field_vals = get_fields(self._hardware, self._qudi_req_fields)
        hw_field_vals = get_fields(self._hardware, self._hw_req_fields)
        self._qudi_transform_kwarg = {req_field: field_val
                                      for req_field, field_val in
                                      zip(self._qudi_req_fields, qudi_field_vals)}
        self._qudi_transform_kwarg.update(self._qudi_kwargs)
        self._qudi_transform_set = partial(self._qudi_transform, **self._qudi_transform_kwarg)
        self._hardware_transform_kwarg = {req_field: field_val
                                          for req_field, field_val in
                                          zip(self._hw_req_fields, hw_field_vals)}
        self._hardware_transform_kwarg.update(self._hw_kwargs)
        self._hardware_transform_set = partial(self._hardware_transform, **self._hardware_transform_kwarg)
        return

    def hardware_update(self, attr, new_val, path=[]):
        if path:
            old_state = copy.deepcopy(getattr(self._hardware, attr))
            update_dictionary(old_state, path, new_val)
            setattr(self._hardware, attr, old_state)
        else:
            setattr(self._hardware, attr, new_val)
        return


class QudiHardwareContainer:
    """
    Contains the set of values that are available for given constraints
    both in QuDi and hardware space.
    """
    def __init__(self, hardware_constraints, hardware_set, qudi_set):
        self._hardware_constraints = hardware_constraints
        self._hardware_set = hardware_set
        self._qudi_set = qudi_set
        return

    @property
    def hardware_constraints(self):
        return self._hardware_constraints

    @hardware_constraints.setter
    def hardware_constraints(self, hw_constraints):
        self._hardware_constraints = hw_constraints
        return

    @property
    def hardware_set(self):
        return self._hardware_set

    @hardware_set.setter
    def hardware_set(self, hw_set):
        self._hardware_set = hw_set
        return

    @property
    def qudi_set(self):
        return self._qudi_set

    @qudi_set.setter
    def qudi_set(self, qs):
        self._qudi_set = qs
        return









