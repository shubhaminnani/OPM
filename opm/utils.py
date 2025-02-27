import numpy as np
from skimage.filters.rank import maximum
from skimage.filters import gaussian
from skimage.morphology.footprints import disk
from skimage.morphology import remove_small_objects, remove_small_holes
from skimage.color.colorconv import rgb2hsv
import matplotlib.pyplot as plt
import yaml
import tiffslide

# RGB Masking (pen) constants
RGB_RED_CHANNEL = 0
RGB_GREEN_CHANNEL = 1
RGB_BLUE_CHANNEL = 2
MIN_COLOR_DIFFERENCE = 40

# HSV Masking
HSV_HUE_CHANNEL = 0
HSV_SAT_CHANNEL = 1
HSV_VAL_CHANNEL = 2
MIN_SAT = 20 / 255
MIN_VAL = 30 / 255

# LAB Masking
LAB_L_CHANNEL = 0
LAB_A_CHANNEL = 1
LAB_B_CHANNEL = 2
LAB_L_THRESHOLD = 0.80


def print_sorted_dict(dictionary):
    sorted_keys = sorted(list(dictionary.keys()))
    output_str = "{"
    for index, key in enumerate(sorted_keys):
        output_str += str(key) + ": " + str(dictionary[key])
        if index < len(sorted_keys) - 1:
            output_str += "; "
    output_str += "}"

    return output_str


def pass_method(*args):
    """
    Method which takes any number of arguments and returns and empty string. Like 'pass' reserved word, but as a func.
    @param args: Any number of arguments.
    @return: An empty string.
    """
    return ""


def get_nonzero_percent(image):
    """
    Return what percentage of image is non-zero. Useful for finding percentage of labels for binary classification.
    @param image: label map patch.
    @return: fraction of image that is not zero.
    """
    np_img = np.asarray(image)
    non_zero = np.count_nonzero(np_img)
    return non_zero / (np_img.shape[0] * np_img.shape[1])


def get_patch_class_proportions(image):
    """
    Return what percentage of image is non-zero. Useful for finding percentage of labels for binary classification.
    @param image: label map patch.
    @return: fraction of image that is not zero.
    """
    np_img = np.asarray(image)
    unique, counts = np.unique(image, return_counts=True)
    denom = np_img.shape[0] * np_img.shape[1]
    prop_dict = {val: count / denom for val, count in list(zip(unique, counts))}
    return print_sorted_dict(prop_dict)


def map_values(image, dictionary):
    """
    Modify image by swapping dictionary keys to dictionary values.
    @param image: Numpy ndarray of an image (usually label map patch).
    @param dictionary: dict(int => int). Keys in image are swapped to corresponding values.
    @return:
    """
    template = image.copy()  # Copy image so all values not in dict are unmodified
    for key, value in dictionary.items():
        template[image == key] = value

    return template


def display_overlay(image, mask):
    overlay = image.copy()
    overlay[~mask] = (overlay[~mask] // 1.5).astype(np.uint8)
    plt.imshow(overlay)
    plt.show()


def hue_range_mask(image, min_hue, max_hue, sat_min=0.05):
    hsv_image = rgb2hsv(image)
    h_channel = gaussian(hsv_image[:, :, HSV_HUE_CHANNEL])
    above_min = h_channel > min_hue
    below_max = h_channel < max_hue

    s_channel = gaussian(hsv_image[:, :, HSV_SAT_CHANNEL])
    above_sat = s_channel > sat_min
    return np.logical_and(np.logical_and(above_min, below_max), above_sat)


def tissue_mask(image):
    """
    Quick and dirty hue range mask for OPM. Works well on H&E.
    TODO: Improve this
    """
    hue_mask = hue_range_mask(image, 0.8, 0.99)
    final_mask = remove_small_holes(hue_mask)
    return final_mask


def basic_pen_mask(image, pen_size_threshold, pen_mask_expansion):
    green_mask = np.bitwise_and(
        image[:, :, RGB_GREEN_CHANNEL] > image[:, :, RGB_BLUE_CHANNEL],
        image[:, :, RGB_GREEN_CHANNEL] - image[:, :, RGB_BLUE_CHANNEL]
        > MIN_COLOR_DIFFERENCE,
    )

    blue_mask = np.bitwise_and(
        image[:, :, RGB_BLUE_CHANNEL] > image[:, :, RGB_GREEN_CHANNEL],
        image[:, :, RGB_BLUE_CHANNEL] - image[:, :, RGB_GREEN_CHANNEL]
        > MIN_COLOR_DIFFERENCE,
    )

    masked_pen = np.bitwise_or(green_mask, blue_mask)
    new_mask_image = remove_small_objects(masked_pen, pen_size_threshold)

    return maximum(np.where(new_mask_image, 1, 0), disk(pen_mask_expansion)).astype(
        bool
    )


def basic_hsv_mask(image):
    """
    Mask based on low saturation and value (gray-black colors)
    :param image: RGB numpy image
    :return: image mask, True pixels are gray-black.
    """
    hsv_image = rgb2hsv(image)
    return np.bitwise_or(
        hsv_image[:, :, HSV_SAT_CHANNEL] <= MIN_SAT,
        hsv_image[:, :, HSV_VAL_CHANNEL] <= MIN_VAL,
    )


def hybrid_mask(image):
    return ~np.bitwise_or(basic_hsv_mask(image), basic_pen_mask(image))


def trim_mask(image, mask, background_value=0, mask_func=hybrid_mask):
    """
    Set the values of single-channel image to 0 if outside of whitespace.
    :param image: RGB numpy image
    :param mask: Mask to be trimmed
    :param background_value: Value to set in mask.
    :param mask_func: Func which takes `image` as a parameter. Returns a binary mask, `True` will be background.
    :return: `mask` with excess trimmed off
    """
    mask_copy = mask.copy()
    masked_image = mask_func(image)
    mask_copy[masked_image] = background_value
    return mask_copy


def patch_size_check(img, patch_height, patch_width):
    img = np.asarray(img)

    if img.shape[0] != patch_height or img.shape[1] != patch_width:
        return False
    else:
        return True


def alpha_channel_check(img):
    img = np.asarray(img)
    # If the image has three dimensions AND there is no alpha_channel...
    if len(img.shape) == 3 and img.shape[-1] == 3:
        return True
    # If the image has three dimensions AND ther IS an alpha channel...
    elif len(img.shape) == 3 and img.shape[-1] == 4:
        alpha_channel = img[:, :, 3]

        if np.any(alpha_channel != 255):
            return False
        else:
            return True
    # If the image is two dims, return True
    elif len(img.shape) == 2:
        return True
    # Other images (4D, RGBA+____, etc.), return False.
    else:
        return False


def parse_config(config_file):
    """
    Parse config file and return a dictionary of config values.
    :param config_file: path to config file
    :return: dictionary of config values
    """
    config = yaml.load(open(config_file), Loader=yaml.FullLoader)

    # initialize defaults
    if not ("scale" in config):
        config["scale"] = 16
    if not ("num_patches" in config):
        config["num_patches"] = -1
    if not ("num_workers" in config):
        config["num_workers"] = 1
    if not ("save_patches" in config):
        config["save_patches"] = True
    if not ("value_map" in config):
        config["value_map"] = None
    if not ("read_type" in config):
        config["read_type"] = "random"
    if not ("overlap_factor" in config):
        config["overlap_factor"] = 0.0

    return config


def generate_initial_mask(slide_path, scale):
    """
    Helper method to generate random coordinates within a slide
    :param slide_path: Path to slide (str)
    :param num_patches: Number of patches you want to generate
    :return: list of n (x,y) coordinates
    """
    # Open slide and get properties
    slide = tiffslide.open_slide(slide_path)
    slide_dims = slide.dimensions

    # Call thumbnail for effiency, calculate scale relative to whole slide
    slide_thumbnail = np.asarray(
        slide.get_thumbnail((slide_dims[0] // scale, slide_dims[1] // scale))
    )
    real_scale = (
        slide_dims[0] / slide_thumbnail.shape[1],
        slide_dims[1] / slide_thumbnail.shape[0],
    )

    return tissue_mask(slide_thumbnail), real_scale


def get_patch_size_in_microns(input_slide_path, patch_size_from_config, verbose=False):
    """
    This function takes a slide path and a patch size in microns and returns the patch size in pixels.

    Args:
        input_slide_path (str): The input WSI path.
        patch_size_from_config (str): The patch size in microns.
        verbose (bool): Whether to provide verbose prints.

    Raises:
        ValueError: If the patch size is not a valid number in microns.

    Returns:
        list: The patch size in pixels.
    """

    return_patch_size = [0, 0]
    patch_size = None

    if isinstance(patch_size_from_config, str):
        # first remove all spaces and square brackets
        patch_size_from_config = patch_size_from_config.replace(" ", "")
        patch_size_from_config = patch_size_from_config.replace("[", "")
        patch_size_from_config = patch_size_from_config.replace("]", "")
        # try different split strategies
        patch_size = patch_size_from_config.split(",")
        if len(patch_size) == 1:
            patch_size = patch_size_from_config.split("x")
        if len(patch_size) == 1:
            patch_size = patch_size_from_config.split("X")
        if len(patch_size) == 1:
            patch_size = patch_size_from_config.split("*")
        if len(patch_size) == 1:
            raise ValueError(
                "Could not parse patch size from config.yml, use either ',', 'x', 'X', or '*' as separator between x and y dimensions."
            )
    elif isinstance(patch_size_from_config, list) or isinstance(patch_size_from_config, tuple):
        patch_size = patch_size_from_config
    else:
        raise ValueError("Patch size must be a list or string.")

    magnification_prev = -1
    for i in range(len(patch_size)):
        magnification = -1
        if str(patch_size[i]).isnumeric():
            return_patch_size[i] = int(patch_size[i])
        elif isinstance(patch_size[i], str):
            if ("m" in patch_size[i]) or ("mu" in patch_size[i]):
                if verbose:
                    print(
                        "Using mpp to calculate patch size for dimension {}".format(i)
                    )
                # only enter if "m" is present in patch size
                input_slide = tiffslide.open_slide(input_slide_path)
                metadata = input_slide.properties
                if i == 0:
                    for property in [tiffslide.PROPERTY_NAME_MPP_X, "tiff.XResolution", "XResolution"]:
                        if property in metadata:
                            magnification = metadata[property]
                            magnification_prev = magnification
                            break                        
                elif i == 1:
                    for property in [tiffslide.PROPERTY_NAME_MPP_Y, "tiff.YResolution", "YResolution"]:
                        if property in metadata:
                            magnification = metadata[property]
                            break 
                    if magnification == -1:
                        # if y-axis data is missing, use x-axis data
                        magnification = magnification_prev
                # get patch size in pixels
                # check for 'mu' first
                size_in_microns = patch_size[i].replace("mu", "")
                size_in_microns = float(size_in_microns.replace("m", ""))
                if verbose:
                    print(
                        "Original patch size in microns for dimension {}",
                        format(size_in_microns),
                    )
                if magnification > 0:
                    return_patch_size[i] = round(size_in_microns / magnification)
                    magnification_prev = magnification
            else:
                return_patch_size[i] = float(patch_size[i])

    if verbose:
        print(
            "Estimated patch size in pixels: [{},{}]".format(
                return_patch_size[0], return_patch_size[1]
            )
        )

    return return_patch_size
