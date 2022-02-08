from unittest import TestCase
from camera_dummy import CameraDummy
from helper_functions\
import calc_image_dimensions, num_images_from_measurement_time, valid_binning
import yaml
import time
import os

# loading the config file
base_dir = r'/home/geegee/Documents/my_virt_envs/qudi/src/qudi/qudi/core'
file_name = 'default.cfg'
path = os.path.join(base_dir, file_name)
with open(path, 'r') as file:
    my_config = yaml.safe_load(file)


class CameraDummyTests(TestCase):
    """
    Class to test basic camera functionality
    """
    def get_init_test_cam(self):
        """
        Each test should start with the same state of the camera.
        """
        test_cam = CameraDummy(config=my_config, name='my_dummy',
                       qudi_main_weakref=None)
        return test_cam

    def test_name(self):
        test_cam = self.get_init_test_cam()
        print('Test if the name is correct')
        self.assertEqual('camera_dummy', test_cam.name)


    def test_size(self):
        test_cam = self.get_init_test_cam()
        self.assertEqual(tuple, type(test_cam.size))
        return


    def test_state(self):
        test_cam = self.get_init_test_cam()
        print('Testing the state of the camera')
        test_cam.stop_acquisition()
        state = test_cam.state
        self.assertEqual(True, state)
        test_cam.start_acquisition()
        state = test_cam.state
        self.assertEqual(False, state)

    def test_exposures(self):
        # TODO check if the individual exposures make sense 
        test_cam = self.get_init_test_cam()
        self.assertEqual(list, type(test_cam.exposures))
        test_cam.exposures = [0.1, 0.2, 0.3]
        self.assertEqual(list, type(test_cam.exposures))

    def test_sensitivity(self):
        test_cam = self.get_init_test_cam()
        self.assertEqual(True, test_cam.sensitivity > 0)
        test_cam.sensitivity = 85.0
        self.assertEqual(85.0, test_cam.sensitivity)

    def test_sensor_area_settings(self):
        test_cam = self.get_init_test_cam()
        binning = test_cam.sensor_area_settings['binning']
        crop = test_cam.sensor_area_settings['crop']
        self.assertEqual(tuple, type(binning))
        self.assertEqual(tuple, type(crop))
        self.assertEqual(True, valid_binning(binning, crop))

    # test the acquisition mode like (1,1), (1, 10), (-1,10)
    def test_acquisition_single(self):
        test_cam = self.get_init_test_cam()
        print('Testing the acquisition of a single image')
        test_cam.acquisition_mode = (1, 1)
        test_cam.start_acquisition()
        # wait the exposure time
        time.sleep(1.0)
        new_img = test_cam.get_images(1)[0]
        sensor_area = test_cam.sensor_area_settings
        binning, crop = sensor_area['binning'], sensor_area['crop']
        dimensions = calc_image_dimensions(binning, crop)
        self.assertEqual(dimensions, new_img.shape)

    def test_acquisition_sequence(self):
        test_cam = self.get_init_test_cam()
        print('Testing the acquisition of a sequence')
        mode = 1
        num_images = 10
        test_cam.acquisition_mode = (mode, num_images)

        print(f'measurement duration {test_cam._cm.measurement_duration}')
        test_cam.start_acquisition()
        sensor_area = test_cam.sensor_area_settings
        binning, crop = sensor_area['binning'], sensor_area['crop']
        dimensions = calc_image_dimensions(binning, crop)
        for ii in range(num_images):
            time.sleep(test_cam.exposures[0] + 0.01)

        rt = test_cam.readout_time
        runtime = test_cam._cm.run_time()
        exposures = test_cam.exposures
        print(f'sequence test (e, r, rt) {exposures} {runtime} {rt}')
        num_recorded_images = num_images_from_measurement_time(exposures, runtime, rt) 
        self.assertEqual(num_images, num_recorded_images)
        # now check that the dimensions are correct
        new_images = test_cam.get_images(num_images)
        for img in new_images:
            self.assertEqual(dimensions, img.shape)
 
    def test_acquisition_continuous(self):
        test_cam = self.get_init_test_cam()
        print('Testing the acquisition of a continuous sequence')
        # acquire for some time images and check that the
        # number and shape is correct.
        mode = -1
        num_images = 5
        wait_for_images = 7
        exposures = [0.01, 0.02, 0.03]
        test_cam.acquisition_mode = (mode, num_images)
        test_cam.exposures = exposures
        test_cam.start_acquisition()
        sensor_area = test_cam.sensor_area_settings
        binning, crop = sensor_area['binning'], sensor_area['crop']
        dimensions = calc_image_dimensions(binning, crop)
        rt = test_cam.readout_time
        exposures = test_cam.exposures
        num_exposures = len(exposures)
        for ii in range(wait_for_images):
            exp_ind = ii % num_exposures
            time.sleep(exposures[exp_ind] + rt)

        runtime = test_cam._cm.run_time()
        print('runtime and exposures')
        print(runtime, exposures)
        num_recorded_images = num_images_from_measurement_time(exposures, runtime, rt)
        print(f'recorded images: {num_recorded_images}')
        self.assertEqual(wait_for_images, num_recorded_images)
        print('passed assertion - getting new images')
        # now check that the dimensions are correct
        new_images = test_cam.get_images(wait_for_images)
        # lets see if we get the right amount of images
        self.assertEqual(new_images.shape[0], wait_for_images)
        print('got new images - off to checking the dimensions. New images dimensions')
        for img in new_images:
            self.assertEqual(dimensions, img.shape)
        return


