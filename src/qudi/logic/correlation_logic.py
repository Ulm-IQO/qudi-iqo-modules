import numpy as np
import scipy
from scipy import ndimage
import skimage
import math
import copy
from qudi.core.module import LogicBase
from qudi.util import paths
from qudi.util.datastorage import TextDataStorage


class CorrelationLogic(LogicBase):
    """
    Correlate confocal scan images in order to find the translation and rotation between two images. This is done by
    successively applying different process steps.
    """

    # declare connectors
    # _camera = Connector(name='camera', interface='CameraInterface')
    # declare config options
    # _minimum_exposure_time = ConfigOption(name='minimum_exposure_time',
    #                                      default=0.05,
    #                                      missing='warn')

    # signals

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._display_image_1 = None
        self._display_image_2 = None
        self._image_1 = None
        self._image_2 = None
        self.result_dict = None
        self._datastorage = TextDataStorage(root_dir=paths.get_default_data_dir())
        self._process_steps = None
        self.default_init_process_steps()

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

    def on_deactivate(self):
        """ Perform required deactivation. """

    @property
    def image_1(self):
        return copy.deepcopy(self._image_1)

    @property
    def image_2(self):
        return copy.deepcopy(self._image_2)

    @property
    def display_image_1(self):
        return copy.deepcopy(self._display_image_1)

    @property
    def display_image_2(self):
        return copy.deepcopy(self._display_image_2)

    @property
    def process_steps(self):
        return self._process_steps.copy()

    def sort_process_steps(self):
        if self._process_steps is None:
            return
        self._process_steps = sorted(self._process_steps, key=lambda x: x.index)

    def add_process_step(self, process_step, index=None):
        if index is None:
            index = process_step.parameters['index']
        else:
            process_step.update_parameters({'index': index})

        if self._process_steps is None:
            self._process_steps = []

        self.sort_process_steps()
        # raise index of a step by 1, if the step has the same index as the added process step
        # (and then check for the further steps accordingly)
        for step in self._process_steps:
            if step.parameters['index'] == index:
                index += 1
                step.update_parameters({'index': index})

        self._process_steps.append(process_step)
        self.sort_process_steps()

    def remove_process_step(self, name):
        """Removes all steps with the given name."""

        new_process_steps = []
        for step in self._process_steps:
            if step.name is not name:
                new_process_steps.append(step)
        self._process_steps = new_process_steps
        self.sort_process_steps()

    def edit_process_step(self, name, parameters):
        """ Edits a parameter of a process step.
        @param str name: Name of the step that should be edited
        @param dict parameters: Dict with parameter-value pairs that should be edited in the specified step
        """
        for step in self._process_steps:
            if step.name == name:
                step.update_parameters(parameters)
        self.sort_process_steps()

    def import_image(self, number, path):
        """Import an image from a .dat file into the correlation logic.
        @param int number: Defines whether the image is imported into the first image or second image of the correlation
         logic
        @param str path: Absolute path to the image file
        """
        image = Image(datastorage=self._datastorage, path=path)
        if number == 1:
            self._image_1 = image
        elif number == 2:
            self._image_2 = image
        else:
            raise ValueError(f"Importing image to image number {number} failed. Number should be 1 or 2.")

    def reset(self):
        """Resets the images and the result dict of the logic."""
        self._image_1.reset_image()
        self._image_2.reset_image()
        self.result_dict = {}

    def run(self, reset=True):
        """Run through the process steps with the current parameters.
        @param boolean reset: Decides whether the images and the result dict are reset before the run."""
        # make sure that the process steps are sorted according to their index
        self.sort_process_steps()

        if reset:
            self.reset()

        # run through the sorted process steps and apply them if they are enabled up to the last process step
        enabled_process_steps = [x for x in self.process_steps if x.parameters['enabled'] is True]
        for process_step in enabled_process_steps:
            self._image_1, self._image_2, result_dict = process_step.apply(self._image_1, self._image_2)

            if process_step.parameters.get('display_image') is True:
                self._display_image_1 = copy.deepcopy(self._image_1)
                self._display_image_2 = copy.deepcopy(self._image_2)

            if result_dict is not None:
                self.result_dict.update(result_dict)

    def default_init_process_steps(self):
        """Prepare all process steps with their parameters in the right order."""
        self._process_steps = []
        # preprocessing steps
        to_8bit_step = To8Bit({'index': 0, 'enabled': True, 'percent_up': 99.1112, 'percent_down': 53.3,
                               'equal_noise_filter': True, 'equal_upper_percentiles': True})
        rescale_step = Rescale({'index': 1, 'enabled': True, 'display_image': True})
        padding_step = Padding({'index': 2, 'enabled': True})
        blur_step = Blur(
            {'index': 3, 'enabled': True, 'method': 'box_blur', 'kernel_size': 4, 'sigma': 1, 'truncate': 4})
        # point detection steps
        feature_detection_step = FeatureDetection({'index': 4, 'enabled': True, 'min_sigma': 0.967, 'max_sigma': 4,
                                                   'sigma_ratio': 1.1, 'threshold': 0.07, 'overlap': 1,
                                                   'marker_size': 1.1})
        # correlation steps
        correlation_step = Correlation({'index': 5, 'enabled': True, 'save_correlation_image': True})

        process_steps_list = [to_8bit_step, rescale_step, padding_step, blur_step, feature_detection_step,
                              correlation_step]
        for step in process_steps_list:
            self.add_process_step(step)
        self.sort_process_steps()

    def sweep(self, name, parameter, values):
        """Sweeps a parameter through given values and runs through the process_steps for each value of the parameter.
        Starts with the raw images.
        Returns a list with the result dictionaries

        @param str name: Name of the process step that includes the sweeping parameter.
        @param str parameter: Name of the sweeping parameter.
        @param list values: Values through which the parameter runs.

        @return list results: List of the result dictionaries for the different sweeping steps.
        """
        # make sure that the process_step list is sorted and save the initial state of the list
        self.sort_process_steps()
        initial_process_steps = self.process_steps

        # disable the saving of the correlation image into the results dict for performance reasons for sweeping
        self.edit_process_step('Correlation', {'save_correlation_image': False})

        active_process_steps = [step for step in self.process_steps if step.parameters['enabled']]
        # create a new step list with all the steps up to the  step where the sweeping begins
        one_time_steps = []
        sweeping_steps = []
        found_step = False
        for step in active_process_steps:
            if step.name == name:
                found_step = True
            if found_step:
                sweeping_steps.append(step)
            else:
                one_time_steps.append(step)
        if not found_step:
            raise RuntimeError(f'Can\'t find the process step with name {name} in the list of active process steps,'
                               f'where the sweeping should begin. Either the step is not part of the process_steps list'
                               f'or it is not enabled.')

        # run the process steps up to the step where the sweeping begins one time
        self._process_steps = one_time_steps
        self.run()

        # save the interim state
        image_1_start_sweeping = self.image_1
        image_2_start_sweeping = self.image_2
        result_dict_start_sweeping = self.result_dict

        # create the new process step list for the sweeping

        results = []
        # sweep the parameter
        for value in values:
            # load the interim state
            self._image_1 = copy.deepcopy(image_1_start_sweeping)
            self._image_2 = copy.deepcopy(image_2_start_sweeping)
            self.result_dict = copy.deepcopy(result_dict_start_sweeping)

            # set the process_steps of the logic beginning with the sweeping step
            self._process_steps = copy.deepcopy(sweeping_steps)
            # update the sweeping parameter to its current value
            self.edit_process_step(name, {parameter: value})
            self.run(reset=False)
            # add current value to the result_dict
            self.result_dict['sweep_value'] = value
            results.append(self.result_dict)

        # load back the initial state of the logic
        self._image_1.reset_image()
        self._image_2.reset_image()
        self._process_steps = initial_process_steps
        self.result_dict = dict()

        return results

    def rotation_init_process_steps(self):
        """Prepare all process steps with their parameters in the right order for the rotation detection."""
        self.default_init_process_steps()

        rotation_step = Rotation({'index': 4, 'enabled': True, 'rotation_angle': 0})

        # put rotation step behind the feature detection step
        index_rotation = None
        for step in self._process_steps:
            if step.name == "FeatureDetection":
                index_rotation = step.parameters['index'] + 1

        if index_rotation is None:
            raise RuntimeError("Can't find a step with the name 'FeatureDetection' in process_steps")

        self.add_process_step(rotation_step, index_rotation)
        self.sort_process_steps()


class Image:
    """ Image class storing an image together with its properties.
    """

    def __init__(self, path=None, datastorage=None, root_dir=None):
        """
        @param str path: Absolute path to the image file
        @param TextDataStorage datastorage: Datastorage object used to import/save the images from files (optional).
        @param str root_dir: Root directory to save images into
        """
        self._array_raw = None
        # resolution of the raw image in pixel/meter
        self._resolution_xy_raw = None
        # range of the raw image in meter
        self._range_xy_raw = None

        self.array = None
        # resolution of the image in pixel/meter
        self.resolution_xy = None
        # range of the image in meter
        self.range_xy = None

        self.metadata = None
        self.general = None

        if datastorage is None:
            self._datastorage = TextDataStorage(root_dir=root_dir)
        else:
            self._datastorage = datastorage

        if path is not None:
            self.import_image(path)

    def import_image(self, path):
        """Import image from an absolute file path.
        @param str path: Absolute path to the image file
        """
        # import raw image from file
        self._array_raw, self.metadata, self.general = self._datastorage.load_data(path)
        # set NaN-values to zero
        self._array_raw[np.isnan(self._array_raw)] = 0
        self.array = self.array_raw
        # get range for image
        range_x = np.abs(self.metadata['x scan range'][1] - self.metadata['x scan range'][0])
        range_y = np.abs(self.metadata['y scan range'][1] - self.metadata['y scan range'][0])
        self._range_xy_raw = [range_x, range_y]
        self.range_xy = self.range_xy_raw
        resolution_x = self.array.shape[1] / self.range_xy[0]
        resolution_y = self.array.shape[0] / self.range_xy[1]
        self._resolution_xy_raw = [resolution_x, resolution_y]
        self.resolution_xy = self.resolution_xy_raw

    def import_from_array(self, array, resolution_xy=None, range_xy=None):
        """Import image from an array and a given range or a given resolution.
        @param numpy.ndarray array: Two dimensional array of the image
        @param list resolution_xy: Resolution of the image in pixel/meter
        @param range_xy: Image range in meters
        """
        self._array_raw = array.copy()
        self.array = array.copy()
        y_length, x_length = array.shape
        if resolution_xy is not None:
            self.resolution_xy = resolution_xy.copy()
            self._resolution_xy_raw = resolution_xy.copy()
            self._range_xy_raw = [x_length / resolution_xy[0], y_length / resolution_xy[1]]
            self.range_xy = self._range_xy_raw.copy()
        elif range_xy is not None:
            self.range_xy = range_xy.copy()
            self.resolution_xy = [x_length / range_xy[0], y_length / range_xy[1]]

    def reset_image(self):
        """Resets the image array to the raw image array."""
        self.array = self.array_raw
        self.resolution_xy = self.resolution_xy_raw
        self.range_xy = self.range_xy_raw

    @property
    def array_raw(self):
        return self._array_raw.copy()

    @property
    def resolution_xy_raw(self):
        """Returns the resolution of the raw image in pixel/meter."""
        return self._resolution_xy_raw.copy()

    @property
    def range_xy_raw(self):
        """Returns the range of the raw image in meter."""
        return self._range_xy_raw.copy()

    def show(self):
        """Show image (not implemented yet)"""

    def rotate(self, angle, resize=True):
        """Rotate the image by a certain angle around its center. Assumes that the x- and y-resolution of the image is
        the same.
        @param angle: Angle of rotation
        @param boolean resize: Decides whether the image is resized so that no part of the image is lost due to the
        rotation.
        """
        self.array = skimage.transform.rotate(self.array, angle, resize=resize, preserve_range=True)

        # calculate the new range of the image
        range_x = self.array.shape[1] / self.resolution_xy[0]
        range_y = self.array.shape[0] / self.resolution_xy[1]
        self.range_xy = [range_x, range_y]

    def mirror(self, mirror_axis):
        """Mirrors the image by inverting the x- or the y-axis of the image.
        @param str mirror_axis: The mirror axis is the axis, that stays the same. The other axis is inverted. Can be
        either x or y.
        """
        if mirror_axis == "x":
            self.array = self.array[::-1, :]
        elif mirror_axis == "y":
            self.array = self.array[:, ::-1]
        else:
            raise ValueError(f'Mirror axis should be "x" or "y", but was "{mirror_axis}"')


class ProcessStep:
    """Generic class for a process step."""

    def __init__(self, parameters, name):
        """
        @param dict parameters: Dictionary of the parameters for the process step
        @param str name: Name of the process step
        """
        self.name = name
        self._parameters = dict()
        # set default values for the required parameters
        self._parameters['index'] = None
        self._parameters['enabled'] = True
        self.update_parameters(parameters)

    def apply(self, image_1, image_2):
        """Applies a process step to the two images and returns two images together with a result dictionary.
        @param Image image_1: Image 1 for the process step
        @param Image image_2: Image 2 for the process step
        """
        image_1_result = copy.deepcopy(image_1)
        image_2_result = copy.deepcopy(image_2)
        result_dict = dict()
        return image_1_result, image_2_result, result_dict

    @property
    def parameters(self):
        return self._parameters.copy()

    def update_parameters(self, parameters):
        """ Update the parameters' dictionary.
        @param dict parameters: Dictionary of the parameters that should be changed together with their new values
        """
        self._parameters.update(parameters)

    @property
    def index(self):
        return self._parameters['index']


class To8Bit(ProcessStep):

    def __init__(self, parameters, name='To8Bit'):
        init_parameters = {'percent_down': None, 'percent_up': None, 'equal_upper_percentiles': None,
                           'equal_noise_filter': None}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Transform the two images into 8bit images by limiting the values with an upper and lower percentile."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)
        # set parameters
        percent_down = self._parameters['percent_down']
        percent_up = self._parameters['percent_up']
        equal_upper_percentile = self._parameters['equal_upper_percentiles']
        equal_noise_filter = self._parameters['equal_noise_filter']

        # make sure that percent_up is bigger than percent_down
        if percent_down > percent_up:
            percent_down, percent_up = percent_up, percent_down

        # determine maximum permitted value with the percentile for each of the two arrays
        max_value_1 = np.percentile(image_1.array, percent_up)
        max_value_2 = np.percentile(image_2.array, percent_up)

        # values greater than max_value are capped at max_value for both images or values greater than the individual
        # max value are capped at those values for the respective image
        if equal_upper_percentile:
            max_value = np.max((max_value_1, max_value_2))
            max_value_1 = max_value
            max_value_2 = max_value

        image_1.array[image_1.array > max_value_1] = max_value_1
        image_2.array[image_2.array > max_value_2] = max_value_2

        # determine minimum permitted value with the percentile for each of the two arrays
        min_value_1 = np.percentile(image_1.array, percent_down)
        min_value_2 = np.percentile(image_2.array, percent_down)
        if equal_noise_filter:
            min_value = np.min((min_value_1, min_value_2))
            min_value_1 = min_value
            min_value_2 = min_value
        # set values smaller than min_value to min_value
        image_1.array[image_1.array < min_value_1] = min_value_1
        image_2.array[image_2.array < min_value_2] = min_value_2

        # map all values to the range [0,2^8-1] (8bit value range)
        image_1.array = (image_1.array - min_value_1) / (max_value_1 - min_value_1) * (2 ** 8 - 1)
        image_1.array = np.uint8(image_1.array)
        image_2.array = (image_2.array - min_value_2) / (max_value_2 - min_value_2) * (2 ** 8 - 1)
        image_2.array = np.uint8(image_2.array)

        return image_1, image_2, {}


class Rescale(ProcessStep):
    def __init__(self, parameters, name='Rescale'):
        init_parameters = {}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Rescales the image array with the smaller pixel/m resolution to the resolution of the other array
        (with the bigger resolution).
        Sets both x- and y-resolution to the same value."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        y_length_1, x_length_1 = image_1.array.shape
        y_length_2, x_length_2 = image_2.array.shape

        x_resolution_1 = x_length_1 / image_1.range_xy[0]
        y_resolution_1 = y_length_1 / image_1.range_xy[1]
        x_resolution_2 = x_length_2 / image_2.range_xy[0]
        y_resolution_2 = y_length_2 / image_2.range_xy[1]

        # find max resolution
        resolution = max(x_resolution_1, x_resolution_2, y_resolution_1, y_resolution_2)

        # rescale x-axis of images with smaller x-pixel resolution to the biggest resolution
        if resolution > x_resolution_1:
            image_1.array = ndimage.interpolation.zoom(image_1.array, (1, resolution / x_resolution_1))
            x_resolution_1 = image_1.array.shape[1] / image_1.range_xy[0]
        if resolution > x_resolution_2:
            image_2.array = ndimage.interpolation.zoom(image_2.array, (1, resolution / x_resolution_2))
            x_resolution_2 = image_2.array.shape[1] / image_2.range_xy[0]
        # rescale y-axis of images with smaller y-pixel resolution to the biggest resolution
        if resolution > y_resolution_1:
            image_1.array = ndimage.interpolation.zoom(image_1.array, (resolution / y_resolution_1, 1))
            y_resolution_1 = image_1.array.shape[0] / image_1.range_xy[1]
        if resolution > y_resolution_2:
            image_2.array = ndimage.interpolation.zoom(image_2.array, (resolution / y_resolution_2, 1))
            y_resolution_2 = image_2.array.shape[0] / image_2.range_xy[1]

        image_1.resolution_xy = [x_resolution_1, y_resolution_1]
        image_2.resolution_xy = [x_resolution_2, y_resolution_2]
        return image_1, image_2, {}


class Padding(ProcessStep):

    def __init__(self, parameters, name='Padding'):
        init_parameters = {}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Pads the two image arrays in order to get them to the same size. Padding is done at the end of the
        array."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        # get the pixel lengths of the image arrays
        y_length_1, x_length_1 = image_1.array.shape
        y_length_2, x_length_2 = image_2.array.shape

        # pad x- and y-axis by appending zeros at the ends to achieve same x and y length
        if x_length_1 > x_length_2:
            x_pad_right = x_length_1 - x_length_2
            image_2.array = np.pad(image_2.array, ((0, 0), (0, x_pad_right)), mode='constant')
            # set new range_xy for image_2
            image_2.range_xy[0] = image_2.range_xy[0] + x_pad_right / image_2.resolution_xy[0]
        elif x_length_1 < x_length_2:
            x_pad_right = x_length_2 - x_length_1
            image_1.array = np.pad(image_1.array, ((0, 0), (0, x_pad_right)), mode='constant')
            # set new range_xy for image_1
            image_1.range_xy[0] = image_1.range_xy[0] + x_pad_right / image_1.resolution_xy[0]

        if y_length_1 > y_length_2:
            y_pad_right = y_length_1 - y_length_2
            image_2.array = np.pad(image_2.array, ((0, y_pad_right), (0, 0)), mode='constant')
            # set new range_xy for image_2
            image_2.range_xy[1] = image_2.range_xy[1] + y_pad_right / image_2.resolution_xy[1]
        elif y_length_1 < y_length_2:
            y_pad_right = y_length_2 - y_length_1
            image_1.array = np.pad(image_1.array, ((0, y_pad_right), (0, 0)), mode='constant')
            # set new range_xy for image_1
            image_1.range_xy[1] = image_1.range_xy[1] + y_pad_right / image_1.resolution_xy[1]

        return image_1, image_2, {}


class Blur(ProcessStep):

    def __init__(self, parameters, name='Blur'):
        init_parameters = {'method': None}
        # parameters for method box_blur:
        init_parameters.update({'kernel_size': None, })
        # parameters for method gaussian_filter:
        init_parameters.update({'sigma': None, 'truncate': None})

        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Apply a box blur or gaussian filter to the images in order to blur them."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        # either use box blur or gaussian blur:
        # box blur:
        kernel_size = self.parameters['kernel_size']
        if self.parameters['method'] == 'box_blur':
            image_1.array = scipy.signal.convolve(image_1.array,
                                                  np.ones((kernel_size, kernel_size)) * 1 / kernel_size ** 2,
                                                  mode='same')

            image_2.array = scipy.signal.convolve(image_2.array,
                                                  np.ones((kernel_size, kernel_size)) * 1 / kernel_size ** 2,
                                                  mode='same').astype(np.uint8)
        # gaussian blur:
        sigma = self.parameters['sigma']
        truncate = self.parameters['truncate']
        if self.parameters['method'] == 'gaussian_filter':
            image_1.array = ndimage.gaussian_filter(image_1.array, sigma=sigma, truncate=truncate, mode='mirror')
            image_2.array = ndimage.gaussian_filter(image_2.array, sigma=sigma, truncate=truncate, mode='mirror')

        # convert datatype of the image arrays back from float to 8bit (np.uint8)
        image_1.array = np.rint(image_1.array).astype(np.uint8)
        image_2.array = np.rint(image_2.array).astype(np.uint8)

        return image_1, image_2, {}


class FeatureDetection(ProcessStep):

    def __init__(self, parameters, name='FeatureDetection'):
        init_parameters = {'min_sigma': None, 'max_sigma': None, 'sigma_ratio': None, 'threshold': None,
                           'overlap': None, 'marker_size': None}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Find features (NV centers) in the image and generate a new black image with markers for the detected
        features."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        features_image_1 = skimage.feature.blob_dog(image_1.array, min_sigma=self.parameters['min_sigma'],
                                                    max_sigma=self.parameters['max_sigma'],
                                                    sigma_ratio=self.parameters['sigma_ratio'],
                                                    threshold=self.parameters['threshold'],
                                                    overlap=self.parameters['overlap'])
        image_1.array = self.draw_markers(image_1, features_image_1)

        features_image_2 = skimage.feature.blob_dog(image_2.array, min_sigma=self.parameters['min_sigma'],
                                                    max_sigma=self.parameters['max_sigma'],
                                                    sigma_ratio=self.parameters['sigma_ratio'],
                                                    threshold=self.parameters['threshold'],
                                                    overlap=self.parameters['overlap'])
        image_2.array = self.draw_markers(image_2, features_image_2)

        return image_1, image_2, {}

    def draw_markers(self, image, features):
        y_length, x_length = image.array.shape
        # calculate radius of features using the sigma values
        features[:, 2] = features[:, 2] * math.sqrt(2)
        if len(features) == 0:
            raise RuntimeError('No NV centers found! Adjust the parameters of the FeatureDetection.')
            # return np.zeros((y_length, x_length))
        else:
            # make array with circle locations
            marked_spots = np.zeros((y_length, x_length))
            if self.parameters['marker_size'] == 0:
                for y, x, r in features:
                    disk = skimage.draw.disk((y, x), r, shape=(y_length, x_length))
                    marked_spots[disk] = 1
            #            marked_spots[int(y), int(x)] = 1
            elif self.parameters['marker_size'] == 1:
                for y, x, r in features:
                    # x and y should be in array
                    if x >= x_length:
                        x = x_length - 1
                    if y >= y_length:
                        y = y_length - 1
                    if x < 0:
                        x = 0
                    if y < 0:
                        y = 0
                    marked_spots[int(y), int(x)] = 1
            else:
                for y, x, r in features:
                    disk = skimage.draw.disk((y, x), self.parameters['marker_size'], shape=(y_length, x_length))
                    marked_spots[disk] = 1
            #            marked_spots[int(y), int(x)] = 1
            return marked_spots


class Correlation(ProcessStep):

    def __init__(self, parameters, name='Correlation'):
        init_parameters = {'save_correlation_image': None}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Correlates two images and uses the correlation to find the translation vector pointing from image_1 to
        image_2.
        """
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)
        # dtype of input arrays for scipy.signal.correlate() defines output dtype -> prevent overflow if e.g. the dtype
        # is 8bit by using a bigger dtype
        dtype = 'uint64'
        # make sure that values between zero and one are not set to zero by the dtype conversion
        image_1.array[np.logical_and(image_1.array > 0, image_1.array < 1)] = 1
        image_2.array[np.logical_and(image_2.array > 0, image_2.array < 1)] = 1

        im1 = image_1.array.astype(dtype)
        im2 = image_2.array.astype(dtype)
        correlation_array = scipy.signal.correlate(im2, im1, mode='full')
        correlation_image = Image()
        correlation_image.import_from_array(correlation_array, resolution_xy=image_1.resolution_xy)
        result_dict = dict()
        if self.parameters.get('save_correlation_image'):
            result_dict['correlation_image'] = correlation_image

        # now find the translation by finding the position of the maximum value of the correlation
        # middle of matrix
        mid_y, mid_x = np.unravel_index(int(np.floor(correlation_array.size / 2)), correlation_array.shape)
        # find max value in cor
        max_y, max_x = np.unravel_index(np.argmax(correlation_array), correlation_array.shape)
        max_value = correlation_array[max_y, max_x]
        # calculate translation vector pointing from im1 to im2
        trans_x = max_x - mid_x
        trans_y = max_y - mid_y

        translation_xy_pixels = np.array([trans_x, trans_y])
        result_dict['translation_vector_xy_pixels'] = translation_xy_pixels
        result_dict['translation_vector_xy_meters'] = translation_xy_pixels / np.array(correlation_image.resolution_xy)
        result_dict['max_correlation_value'] = max_value

        return image_1, image_2, result_dict


class Rotation(ProcessStep):
    def __init__(self, parameters, name='Rotation'):
        init_parameters = {'rotation_angle': None}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Rotates image_2. Assumes that the x- and y-resolution of the image is the same."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        image_2.rotate(self.parameters['rotation_angle'])

        return image_1, image_2, {}


class Mirror(ProcessStep):
    def __init__(self, parameters, name='Mirror'):
        init_parameters = {'mirror_axis': None}
        init_parameters.update(parameters)
        super().__init__(init_parameters, name)

    def apply(self, image_1, image_2):
        """Mirrors image_2 by inverting the x- or the y-axis of the image."""
        image_1 = copy.deepcopy(image_1)
        image_2 = copy.deepcopy(image_2)

        image_2.mirror(self.parameters['mirror_axis'])

        return image_1, image_2, {}
