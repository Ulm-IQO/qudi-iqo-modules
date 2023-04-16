import PIL.Image
import PIL.ImageOps
import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1 import make_axes_locatable
import numpy as np
import scipy
from scipy import ndimage
import math
from qudi.core.module import LogicBase
from qudi.util.datastorage import TextDataStorage


def initialize_process_steps():
    """Prepare all process steps with their parameters and return a list containing all process steps."""
    # preprocessing steps
    to_8bit_step = To8Bit({'index': 0, 'enabled': True, 'percent_up': 99.1112, 'percent_down': 53.3,
                           'equal_noise_filter': True, 'equal_upper_percentiles': True})
    rescale_step = Rescale({'index': 1, 'enabled': True})
    padding_step = Padding({'index': 2, 'enabled': True})
    blur_step = Blur({'index': 3, 'enabled': True, 'method': 'box_blur', 'kernel_size': 4, 'sigma': 1, 'truncate': 4})
    # point detection steps
    feature_detection_step = FeatureDetection({'index': 4, 'enabled': False, 'min_sigma': 0.967, 'max_sigma': 4,
                                               'sigma_ratio': 1.1, 'threshold': 0.07, 'overlap': 1, 'marker_size': 1.1})
    # correlation steps
    correlation_step = Correlation({'index': 5, 'enabled': True})
    find_translation_step = FindTranslation({'index': 6, 'enabled': True})

    process_steps = [to_8bit_step, rescale_step, padding_step, blur_step, feature_detection_step, correlation_step,
                     find_translation_step]
    return sorted(process_steps, key=return_index)


class CorrelationLogic(LogicBase):
    """
    Correlate confocal scan images in order to find the translation and rotation between two images. This is done by
    applying different process steps one after another.
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
        self._image_1 = None
        self._image_2 = None
        self.correlation_image = Image()
        self.translation_xy_pixels = None
        self.translation_xy_meters = None
        # self._datastorage = TextDataStorage(root_dir=r'C:\Users\fabia\OneDrive\Uni_Laptop\Qudi notebooks')
        self._datastorage = TextDataStorage(root_dir=None)
        self._process_steps = initialize_process_steps()

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

    def on_deactivate(self):
        """ Perform required deactivation. """

    @property
    def image_1(self):
        return self._image_1

    @property
    def image_2(self):
        return self._image_2

    @property
    def process_steps(self):
        return self._process_steps

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
            self.log.error(f"Importing image to image number {number} failed. Number should be 1 or 2.")

    def run(self):
        """Run through the process steps with the current parameters"""
        # make sure that the process steps are sorted according to their index
        self._process_steps = sorted(self.process_steps, key=return_index)

        # run through the sorted process steps and apply them if they are enabled
        for process_step in self.process_steps:
            if process_step.parameters['enabled']:
                process_step.apply(self)



class Image:
    """ Image class storing an image together with its attributes.
    2d_array _array_raw: The raw imported image
    2d_array array: The current maybe edited image
    list resolution_xy: Image resolution (pixel per meter)
    list _resolution_xy_raw: Raw image resolution (pixel per meter)
    list range_xy: Image size in meters
    TextDataStorage datastorage: Datastorage object used to import/save the images from files
    """

    def __init__(self, path=None, datastorage=None, root_dir=None):
        """
        @param str path: Absolute path to the image file
        @param TextDataStorage datastorage: Datastorage object used to import/save the images from files (optional).
        @param str root_dir: Root directory to save images into
        """
        # raw imported image array
        self._array_raw = None
        # resolution of the raw image in pixel/meter
        self._resolution_xy_raw = None
        # image array
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
        self.array = self._array_raw.copy()
        # get range for image
        range_x = np.abs(self.metadata['x scan range'][1] - self.metadata['x scan range'][0])
        range_y = np.abs(self.metadata['y scan range'][1] - self.metadata['y scan range'][0])
        self.range_xy = [range_x, range_y]
        resolution_x = self.array.shape[1] / self.range_xy[0]
        resolution_y = self.array.shape[0] / self.range_xy[1]
        self.resolution_xy = [resolution_x, resolution_y]
        self._resolution_xy_raw = [resolution_x, resolution_y]

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
            self.range_xy = [x_length / resolution_xy[0], y_length / resolution_xy[1]]
        elif range_xy is not None:
            self.range_xy = range_xy.copy()
            self.resolution_xy = [x_length / range_xy[0], y_length / range_xy[1]]

    @property
    def array_raw(self):
        return self._array_raw

    @property
    def resolution_xy_raw(self):
        return self._resolution_xy_raw

    def show(self):
        """Show image (not implemented yet)"""


class ProcessStep:
    """Generic class for a process step."""

    def __init__(self, parameters, name):
        """
        @param dict parameters: Dictionary of the parameters for the process step
        @param str name: Name of the process step
        """
        self._name = name
        self._parameters = dict()
        self.update_params(parameters)

    def update_params(self, parameters):
        for parameter, value in parameters.items():
            self._parameters[parameter] = value

    def apply(self, correlation_logic):
        """Applies a process step to the correlation logic.
        @param CorrelationLogic correlation_logic: Apply the step to this correlation logic
        """

    @property
    def name(self):
        return self._name

    @property
    def parameters(self):
        return self._parameters


def return_index(process_step):
    """Return the index parameter value for a ProcessStep object"""
    return process_step.parameters['index']


class To8Bit(ProcessStep):

    def __init__(self, parameters, name='To8Bit'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Transform the two images into 8bit images by limiting the values with an upper and lower percentile."""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2
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


class Rescale(ProcessStep):
    def __init__(self, parameters, name='Rescale'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Rescales the image array with the smaller pixel/m resolution to the resolution of the other array
        (with the bigger resolution).
        Sets both x- and y-resolution to the same value."""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2

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


class Padding(ProcessStep):

    def __init__(self, parameters, name='Padding'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Pads the two image arrays in order to get them to the same size. Padding is done at the ends of the
        arrays."""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2

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


class Blur(ProcessStep):

    def __init__(self, parameters, name='Blur'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Apply a gaussian filter to the images in order to blur them."""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2
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


class FeatureDetection(ProcessStep):

    def __init__(self, parameters, name='FeatureDetection'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Find features (NV centers) in the image and generate a new black image with markers for the detected
        features."""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2
        features_image_1 = skimage.feature.blob_dog(image_1.array, min_sigma=self.parameters['min_sigma'],
                                                    max_sigma=self.parameters['max_sigma'],
                                                    sigma_ratio=self.parameters['sigma_ratio'],
                                                    threshold=self.parameters['threshold'],
                                                    overlap=self.parameters['overlap'])
        image_1.array = self.draw_markers(image_1, features_image_1)
        image_1.features = features_image_1

        features_image_2 = skimage.feature.blob_dog(image_2.array, min_sigma=self.parameters['min_sigma'],
                                                    max_sigma=self.parameters['max_sigma'],
                                                    sigma_ratio=self.parameters['sigma_ratio'],
                                                    threshold=self.parameters['threshold'],
                                                    overlap=self.parameters['overlap'])
        image_2.array = self.draw_markers(image_2, features_image_2)
        image_2.features = features_image_2

    def draw_markers(self, image, features):
        y_length, x_length = image.array.shape
        # calculate radius of features using the sigma values
        features[:, 2] = features[:, 2] * math.sqrt(2)
        if len(features) == 0:
            print('No circles found!')
            return np.zeros((y_length, x_length))
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
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Correlates two images"""
        image_1 = correlation_logic.image_1
        image_2 = correlation_logic.image_2
        # dtype of input arrays for scipy.signal.correlate() defines output dtype -> prevent overflow if e.g. the dtype
        # is 8bit by using a bigger dtype
        dtype = 'uint64'
        im1 = image_1.array.astype(dtype)
        im2 = image_2.array.astype(dtype)
        correlation_array = scipy.signal.correlate(im2, im1, mode='full')
        correlation_logic.correlation_image.import_from_array(correlation_array, resolution_xy=image_1.resolution_xy)


class FindTranslation(ProcessStep):
    def __init__(self, parameters, name='FindTranslation'):
        super().__init__(parameters, name)

    def apply(self, correlation_logic):
        """Tries to find the translation vector pointing from image_1 to image_2 by finding the
            maximum value of the correlation"""
        correlation_array = correlation_logic.correlation_image.array
        # middle of matrix
        mid_y, mid_x = np.unravel_index(int(np.floor(correlation_array.size / 2)), correlation_array.shape)
        # find max value in cor
        max_y, max_x = np.unravel_index(np.argmax(correlation_array), correlation_array.shape)
        # calculate translation vector pointing from im1 to im2
        trans_x = max_x - mid_x
        trans_y = max_y - mid_y
        correlation_logic.translation_xy_pixels = np.array([trans_x, trans_y])
        correlation_logic.translation_xy_meters = correlation_logic.translation_xy_pixels / np.array(
            correlation_logic.correlation_image.resolution_xy)
