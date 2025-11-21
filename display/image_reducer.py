from PIL import Image
import numpy as np
import os, sys

TARGET_WIDTH = 800
TARGET_HEIGHT = 480
TARGET_RATIO = TARGET_WIDTH / TARGET_HEIGHT

# -------------------------------
# Helper functions
# -------------------------------
def crop_center_to_ratio(img, target_ratio):
    w, h = img.size
    ratio = w / h
    if ratio > target_ratio:
        new_w = int(h * target_ratio)
        new_h = h
    else:
        new_w = w
        new_h = int(w / target_ratio)
    l = (w - new_w) // 2
    t = (h - new_h) // 2
    return img.crop((l, t, l + new_w, t + new_h))

# 8x8 Bayer matrix for clustered halftoning
BAYER8 = (1/64) * np.array([
    [0,48,12,60,3,51,15,63],
    [32,16,44,28,35,19,47,31],
    [8,56,4,52,11,59,7,55],
    [40,24,36,20,43,27,39,23],
    [2,50,14,62,1,49,13,61],
    [34,18,46,30,33,17,45,29],
    [10,58,6,54,9,57,5,53],
    [42,26,38,22,41,25,37,21],
], dtype=np.float32)

# Approximate visible palette of the Spectra 6
EINK_COLORS = {
    (0,0,0):  [0,0,0],        # Black
    (0,0,1):  [0,0,255],      # Blue
    (0,1,0):  [255,255,0],    # Yellow
    (1,0,0):  [255,0,0],      # Red
    (0,1,1):  [0,128,0],      # Green (Blue+Yellow)
    (0,0,0):  [0,0,0],        # Black (all pigments)
}

def rgb_to_pigments(rgb):
    """
    Approximate how much red, yellow, blue pigment to use (0-1 each)
    given an RGB color. Think subtractive color mixing.
    """
    rgb = rgb / 255.0
    # Convert to CMY-like space
    c = 1 - rgb[0]
    m = 1 - rgb[1]
    y = 1 - rgb[2]
    # Map CMY to Spectra pigments (red, yellow, blue)
    red   = m * (1 - y)           # Red is magenta minus yellow
    yellow= y
    blue  = c
    return np.clip([red, yellow, blue], 0, 1)

def halftone_channel(channel, matrix):
    """Apply clustered Bayer halftoning to a single pigment channel."""
    h, w = channel.shape
    dither = np.tile(matrix, (h // 8 + 1, w // 8 + 1))[:h, :w]
    return (channel > dither).astype(np.uint8)

def spectra6_halftone(img):
    """Perform pigment-based halftoning for the Spectra 6 E-Ink."""
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    arr = arr / 255.0
    h, w, _ = arr.shape

    # Separate pigment channels
    reds = np.zeros((h, w))
    yellows = np.zeros((h, w))
    blues = np.zeros((h, w))

    # Compute pigment strengths
    for y in range(h):
        for x in range(w):
            r, g, b = arr[y, x]
            pr, py, pb = rgb_to_pigments(np.array([r*255, g*255, b*255]))
            reds[y, x] = pr
            yellows[y, x] = py
            blues[y, x] = pb

    # Apply Bayer clustered halftone to each channel
    reds_d = halftone_channel(reds, BAYER8)
    yellows_d = halftone_channel(yellows, BAYER8)
    blues_d = halftone_channel(blues, BAYER8)

    # Combine pigments to visible colors
    result = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            r, yel, b = reds_d[y, x], yellows_d[y, x], blues_d[y, x]
            if (r, yel, b) == (0,0,0):
                color = [255,255,255]  # white
            elif (r, yel, b) == (1,0,0):
                color = [255,0,0]      # red
            elif (r, yel, b) == (0,1,0):
                color = [255,255,0]    # yellow
            elif (r, yel, b) == (0,0,1):
                color = [0,0,255]      # blue
            elif (r, yel, b) == (0,1,1):
                color = [0,128,0]      # green (yellow+blue)
            else:
                color = [0,0,0]        # black or mixed pigments
            result[y, x] = color

    return Image.fromarray(result, "RGB")

def process_image(input_path):
    with Image.open(f"assets/images_raw/{input_path}") as img:
        w, h = img.size
        ratio = w / h
        rotated_ratio = h / w
        if abs(rotated_ratio - TARGET_RATIO) < abs(ratio - TARGET_RATIO):
            img = img.rotate(90, expand=True)
            print("🔄 Rotated 90° for better fit")

        img = crop_center_to_ratio(img, TARGET_RATIO)
        img = img.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)

        print("🎨 Applying Spectra-6 halftone simulation...")
        halftoned = spectra6_halftone(img)


        halftoned.save(f"assets/images_reduced/{input_path[-3]}.bmp", "BMP")
        print(f"✅ Saved dithered BMP.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python image_reducer.py <image_name>")
    else:
        process_image(sys.argv[1] if len(sys.argv) > 1 else None)