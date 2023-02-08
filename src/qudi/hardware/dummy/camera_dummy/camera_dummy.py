# -*- coding: utf-8 -*-

"""
Dummy implementation for camera_interface.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

from enum import Enum

from qudi.hardware.dummy.camera_dummy.helper_functions import *
from qudi.hardware.dummy.camera_dummy.helper_classes \
    import ModelCamera, ImageGenerator, CameraMeasurement, QudiHardwareMapper

from qudi.core.configoption import ConfigOption
from qudi.interface.scientific_camera_interface import ScientificCameraInterface, CameraConstraints

from qudi.util.network import netobtain


class OperatingMode(Enum):
    default = 0
    high_responsitivity = 1
    fast_readout = 2


class CameraDummy(ScientificCameraInterface):
    """ Dummy hardware for camera interface
    """
    # define defaults for the dummy module
    _shutter = ConfigOption(name='shutter', default=False)
    _shutter_speed = ConfigOption(name='shutter_speed', default=0.1)
    _acquisition_settings = ConfigOption(name='acquisition_settings', default=(1, 1))
    _pixel_unit = ConfigOption(name='pixel_unit', default='Counts')
    _binning = ConfigOption(name='binning', default=(2, 2))
    _crop = ConfigOption(name='crop', default=((0, 511), (0, 511)))

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        # capabilities of the camera, in actual hardware many of these attributes should
        # be obtained by querying the camera on startup
        # state of the 'Camera'
        self._measurement_start = 0.0
        self._num_sequences = 2
        self._num_images = 5

        # internal dummy variables
        self._sensor_temperature = 20.0
        self._sensor_setpoint_temperature = 20.0
        self._ambient_temperature = 20.0
        self._temperature_control = False
        self._cooling_speed = 0.04
        self._thermal_contact = 0.01
        self._time = 0.0
        self._start_time = 0.0
        self._start_temperature = self._ambient_temperature
        # variable to only allow reading of images within the exposure time limit
        self._temperature_control_start = 0.0

        self._cam = ModelCamera(amp_state={'preamp': 4.0}, binning_state=self._binning,
                                crop_state=self._crop,
                                readout_freq_state={'horizontal': 1e6, 'vertical': 0.5e6},
                                acquisition_mode_state=self._acquisition_settings,
                                trigger_mode_state='Internal',
                                ring_of_exposures=[0.2],
                                shutter_state=self._shutter,
                                shutter_speed_state=self._shutter_speed,
                                pixel_unit_state='Counts',
                                acquisition_settings=(1, 1))

        self._cm = CameraMeasurement(self._cam)
        self._ig = ImageGenerator(self._cam, self._cm, method='random_images', count_rate=150e3)
        # from the model camera initialize the constraints
        # initialize the constraints
        self._constraints = CameraConstraints()
        img_dims = calc_image_dimensions(self._cam.binning, self._cam.crop)
        n_pxls = img_dims[0] * img_dims[1]
        size_of_image = n_pxls * self._cam.bit_depth
        self._constraints.max_images = self._cam.available_internal_memory // size_of_image
        self._constraints.pixel_units = self._cam.available_pixel_units
        self._constraints.ring_of_exposures = self._cam.available_exposure_times
        self._constraints.ring_of_exposures['max_num_of_exposure_times'] = self._cam.available_number_of_exposures
        shutter_states = self._cam.available_shutter_states
        shutter_speeds = self._cam.available_shutter_speeds
        self._constraints.shutter = {'states': shutter_states,
                                     'speed': shutter_speeds}
        self._constraints.acquisition_modes = {'image': True, 'video': True, 'image_burst': True, 'image_burst_sequence': True}
        # now to the tricky constraints. They are derived quantities of the
        # hardware.

        # those will stay constant during runtime
        hw_transform, qudi_transform = readout_freq_hw_transform,\
                                       readout_times_qudi_transform

        # if any of these change, also the readout_time_mapper needs to be updated.
        hardware_fields, qudi_fields = [], \
                                       ['crop', 'binning', 'frame_transfer']

        # could be interesting to set these through config options
        hw_kwargs = {'amp': 'EM', 'dir': 'horizontal'}
        qudi_kwargs = {'vertical_freqs': [self._cam.readout_freq['vertical']]}


        self._readout_time_mapper = QudiHardwareMapper(self._cam, hw_transform,
                                                       qudi_transform, hardware_fields,
                                                       qudi_fields, hw_kwargs=hw_kwargs,
                                                       qudi_kwargs=qudi_kwargs)

        # TODO Add mapper for the responsitivity
        hw_transform, qudi_transform = gains_hw_transform, gains_hw_transform

        # if any of these change, also the readout_time_mapper needs to be updated.
        hardware_fields, qudi_fields = ['amp'], \
                                       ['amp']

        # could be interesting to set these through config options
        hw_kwargs = {'var_amp': 'preamp'}
        qudi_kwargs = {'var_amp': 'preamp'}


        self._responsitivity_mapper = QudiHardwareMapper(self._cam, hw_transform,
                                                       qudi_transform, hardware_fields,
                                                       qudi_fields, hw_kwargs=hw_kwargs,
                                                       qudi_kwargs=qudi_kwargs)


        self._operating_mode = OperatingMode.default
        # self._update_constraints()



    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # set the operating mode
        self.operating_mode = self._operating_mode.name

        return

    def on_deactivate(self):
        """ Initialisation performed during deactivation of the module.
        """
        self.stop_acquisition()

    @property
    def constraints(self):
        return self._constraints

    @constraints.setter
    def constraints(self, update_dict):
        self._constraints.update(update_dict)
        return

    @property
    def name(self):
        """ Retrieve an identifier of the camera that the GUI can print

        @return string: name for the camera
        """
        return 'camera_dummy'

    @property
    def size(self):
        """ Retrieve size of the image in pixel

        @return tuple: Size (width, height)
        """
        return self._cam.size

    @property
    def state(self):
        """ Is the camera ready for an acquisition ?
        @return bool: ready ?
        """
        return self._cm.measurement_finished()

    @property
    def measurement_running(self):
        """ Is the camera ready for an acquisition ?
        @return bool: ready ?
        """
        return self._cm.measurement_finished()

    @measurement_running.setter
    def measurement_running(self, status):
        self._cm._measurement_running = status

    @property
    def ring_of_exposures(self):
        """ Get the exposure time in seconds

            @return float exposure time
        """
        return self._cam.ring_of_exposures

    @ring_of_exposures.setter
    def ring_of_exposures(self, exposures):
        """ Set the list of exposures

        @param float exposure: desired new exposure time
        """
        # TODO Think of something better here (ValueError could really be anything)
        try:
            self._cam.ring_of_exposures = exposures
        except ValueError:
            self._cam.ring_of_exposures = netobtain(exposures)
        return

    @property
    def responsitivity(self):
        responsitivity = 1.0
        for amp in self._cam.amp:
            responsitivity *= self._cam.amp[amp]

        return responsitivity

    @responsitivity.setter
    def responsitivity(self, responsitivity):
        el_pos = element_pos(responsitivity, self.constraints.responsitivity)
        if el_pos:
            var_amp = self._responsitivity_mapper.qudi_kwargs['var_amp']
            red_amp = red_dictionary(self._cam.amp, var_amp)
            gain_besides_var_amp = gain_from_amp_chain(red_amp)
            new_gain = responsitivity / gain_besides_var_amp
            self._cam.amp = {var_amp: new_gain}
        else:
            self.log.error(outside_constraints('responsitivity'))
        return

    @property
    def readout_time(self):
        """
        Return how long the readout of a single image will take
        @return float time: Time it takes to read out an image from the sensor
        """
        hrfq = self._cam.readout_freq['horizontal']
        vrfq = self._cam.readout_freq['vertical']
        hrfqs = self._readout_time_mapper.hardware_transform(self._cam.available_readout_freqs)
        pos = pos_in_array(hrfq, hrfqs)
        qrts = self._readout_time_mapper.qudi_transform(hrfqs)
        return calc_readout_time(self._cam.crop, self._cam.binning, hrfq, vrfq, self._cam.frame_transfer)
        #return qrts[pos]

    @readout_time.setter
    def readout_time(self, readout_time):
        """
        Set up the camera in a way to comply with the requested readout time.
        @param readout_time: Requested readout time
        @return:
        """
        if readout_time in self.constraints.readout_times:
            direction = self._readout_time_mapper.hardware_kwargs['dir']
            ind = pos_in_array(readout_time, self.constraints.readout_times)
            av_freqs = self._cam.available_readout_freqs
            hw_freqs = self._readout_time_mapper.hardware_transform(av_freqs)
            # -ind because the directions are reversed
            n_freqs = len(hw_freqs)
            self.log.error(f'ind {ind}, n_freqs - in {n_freqs - ind}')
            self.log.error(f'hw freqs {hw_freqs[ind]} dir {direction}')
            self._cam.readout_freq = {direction: hw_freqs[ind]}
        else:
            self.log.error(f'requested readout time {readout_time} is not available')
        return

    @property
    def sensor_area_settings(self):
        """
        Return the current binning and crop settings of the sensor e.g. {'binning': (2,2), 'crop' (128, 256)}
        @return: dict of the sensor area settings
        """
        return {'binning': self._cam.binning, 'crop': self._cam.crop}

    @sensor_area_settings.setter
    def sensor_area_settings(self, settings):
        """
        Binning and extracting a certain part of the sensor e.g. {'binning': (2,2), 'crop' (128, 256)} takes 4 pixels
        together to 1 and takes from all the pixels and area of 128 by 256
        """
        # TODO Add constraints and check them
        if 'binning' in settings:
            self._cam.binning = settings['binning']
        if 'crop' in settings:
            self._cam.crop = settings['crop']
        # in case the binning or crop changed, we should update the constraints
        self._update_constraints()
        return

    @property
    def bit_depth(self):
        """
        Return the bit depth of the camera
        @return:
        """
        return self._cam.bit_depth

    @property
    def pixel_unit(self):
        """
        Get the currently set count convert mode.
        The GUI will make use of this to display what is recorded.
        @return string mode: i.e. 'Counts', 'Electrons' or 'Photons'
        """
        return self._cam.pixel_unit

    @pixel_unit.setter
    def pixel_unit(self, px_unit):
        """
        Return signal in 'Counts', 'Electrons' or 'Photons'
        @param string mode: i.e. 'Counts', 'Electrons' or 'Photons'
        """
        # TODO check constraints
        if px_unit in self._constraints.pixel_units:
            self._cam.pixel_unit = px_unit
        else:
            self.log.error('requested pixel unit not available.')
        return

    @property
    def operating_mode(self):
        return self._operating_mode

    @operating_mode.setter
    def operating_mode(self, mode):
        self._operating_mode = getattr(OperatingMode, mode)
        # adjust the camera settings to the new operating mode
        self._configure_operating_mode(mode)
        return

    def start_acquisition(self):
        """
        Start an acquisition. The acquisition settings
        will determine if you record a single image, or image sequence.
        """
        self._cm.start_measurement()

    def stop_acquisition(self):
        """
        Stop/abort acquisition
        """
        self._cm.measurement_finished(req_stop=True)
        return

    @property
    def acquisition_mode(self):
        """
        Get the acquisition mode of the camera
        @return: tuple, denoting the acquisition mode of the camera
        """
        return self._cam.acquisition_settings

    @acquisition_mode.setter
    def acquisition_mode(self, acquisition_setting):
        """
        Set the acquisition mode of the camera.

        Tuple (seq_len, repetitions) containing the sequence length
        and the repetitions of the sequence.
        Cases: (1, 1) -> take a single image
               (1, -1) -> Continuously record single images (Video)
               (1 < seq_len, rep >= 1) -> Image stack
               (-1, -1) -> Continuously run through sequence
        """
        if valid_acquisition_setting(acquisition_setting):
            self._cam.acquisition_settings = acquisition_setting
        else:
            self.log.error('Invalid acquisition mode.')
        return

    def get_images(self, image_num):
        """
        Read a number of images from the buffer

        @param int image_num: Number of most recent images
        @return: numpy nd array of dimension (image_num, npx_x, npx_y)
        """
        # check how many images have been acquired
        run_time = self._cm.run_time()
        acquired_images = num_images_from_measurement_time(self.ring_of_exposures,
                                                           run_time,
                                                           self.readout_time)
        new_images = list()
        max_images = acquired_images - self._cm.readout_images
        if image_num <= max_images:
            # self.log.info('getting the generator')
            generator = self._ig.image_generator(image_num)
            # self.log.info('creating a list')
            new_images = list(generator)
            self._cm.readout_images += image_num
        else:
            self.log.error('Not enough images have been recorded or already been read out.')
        return np.array(new_images)

    @property
    def shutter_state(self):
        """
        Query the camera if a shutter exists.
        @return boolean: True if yes, False if not
        """
        return self._cam.shutter

    @shutter_state.setter
    def shutter_state(self, state):
        """
        Set the state of the shutter
        """
        if state in self._constraints.shutter['states']:
            self._cam.shutter = state
        else:
            self.log.error('requested shutter state not available')

    @property
    def shutter_speed(self):
        """
        Query the shutter speed of the camera
        @return:
        """
        return self._shutter_speed

    @shutter_speed.setter
    def shutter_speed(self, speed):
        """
        Set the speed of the shutter
        """
        if valid_value(speed, self._constraints.shutter['speed']):
            self._cam.shutter_speed = speed
        else:
            self.log.error('requested shutter speed not available')

        return

    def _configure_operating_mode(self, mode):
        if mode == OperatingMode.default.name:
            self.log.info('operationg mode set to >> default <<')
            self._cam.trigger_mode = 'Software'
            self._cam.amp = {'preamp': 4.0}
            self._cam.frame_transfer = False
            # when changing responsitivity, which amplifier gain should be changed?
            self._readout_time_mapper.hardware_kwargs['amp'] = 'preamp'
            self._responsitivity_mapper.hardware_kwargs['var_amp'] = 'preamp'
            self._responsitivity_mapper.qudi_kwargs['var_amp'] = 'preamp'

        elif mode == OperatingMode.high_responsitivity.name:
            self.log.info('operationg mode set to >> high_responsitivity <<')
            self._cam.trigger_mode = 'Internal'
            self._cam.amp = {'preamp': 4.0, 'EM': 100.0}
            self._cam.frame_transfer = False
            # readout time
            self._readout_time_mapper.hardware_kwargs['amp'] = 'EM'
            # responsitivity
            self._responsitivity_mapper.hardware_kwargs['var_amp'] = 'EM'
            self._responsitivity_mapper.qudi_kwargs['var_amp'] = 'EM'

        elif mode == OperatingMode.fast_readout.name:
            self.log.info('operationg mode set to >> fast_readout <<')
            self._cam.trigger_mode = 'Internal'
            self._cam.amp = {'preamp': 4.0, 'EM': 100.0}
            self._cam.frame_transfer = False
            # readout time
            self._readout_time_mapper.hardware_kwargs['amp'] = 'EM'
            # responsitivity
            self._responsitivity_mapper.hardware_kwargs['var_amp'] = 'EM'
            self._responsitivity_mapper.qudi_kwargs['var_amp'] = 'EM'
            self._cam.trigger_mode = 'Internal'
            self._cam.frame_transfer = True
        else:
            self.log.error('operating mode {}'.format(mode))

        # after changing the operating mode the constraints have to be updated
        self._update_constraints()

    def _update_constraints(self):
        # list of constraints that should be updated
        # update each constraint by using the hardware object
        # and the transform object.

        # update the readout time constraint
        # readout time
        hw_settings = self._readout_time_mapper.hardware_transform(self._cam.available_readout_freqs)
        qudi_set = set(self._readout_time_mapper.qudi_transform(hw_settings))
        self._constraints.readout_times = qudi_set

        # responsitvity
        # hw_settings = self._responsitivity_mapper.hardware_transform(self._cam.available_amplifiers)
        qudi_set = set(self._responsitivity_mapper.hardware_transform(self._cam.available_amplifiers))
        self._constraints.responsitivity = qudi_set

        return

    def _current_values_in_constraints(self):
        """
        Changing the operating mode will lead to a change in the constraints and with this
        old settings might become invalid. Therefore check >all< settings of the camera if they
        still fall within the constraints. If not give a warning and set the smallest default value.
        :return:
        """
        # what is the best way to run through all the constraints and their corresponding settings?
        # Get a list of all the interface methods that carry a property.
        return
