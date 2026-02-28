import sys
sys.path.insert(0, '/mnt/c/Users/tad/ax_maps/code/autocross-cone-thing')

from PIL import Image
import numpy as np

img = Image.open('/mnt/c/Users/tad/ax_maps/Seneca_Grand_Prix_2021.jpg')
arr = np.array(img)

print(f"Image size: {img.size} (WxH)")
print(f"Array shape: {arr.shape}")

r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
cone_mask = (r > 200) & (g > 100) & (g < 200) & (b < 100)
cone_pixels = np.where(cone_mask)
print(f"\nOrange/yellow cone pixels: {len(cone_pixels[0])}")

from scipy import ndimage
labeled, num_features = ndimage.label(cone_mask)
print(f"Distinct cone clusters: {num_features}")

cone_positions = []
for i in range(1, num_features + 1):
    region = np.where(labeled == i)
    if len(region[0]) >= 3:
        cy = float(np.mean(region[0]))
        cx = float(np.mean(region[1]))
        size = len(region[0])
        cone_positions.append((cx, cy, size))

cone_positions.sort(key=lambda x: -x[2])
print(f"Cone clusters with >=3 pixels: {len(cone_positions)}")
print(f"Top 10 largest:")
for pos in cone_positions[:10]:
    print(f"  ({pos[0]:.1f}, {pos[1]:.1f}) size={pos[2]}")
print(f"Smallest 5:")
for pos in cone_positions[-5:]:
    print(f"  ({pos[0]:.1f}, {pos[1]:.1f}) size={pos[2]}")

sizes = [pos[2] for pos in cone_positions]
if sizes:
    import statistics
    print(f"\nSize stats: min={min(sizes)}, max={max(sizes)}, median={statistics.median(sizes):.1f}")

green_mask = (r < 150) & (g > 150) & (b < 150)
green_pixels = np.where(green_mask)
print(f"\nGreen pixels: {len(green_pixels[0])}")

red_mask = (r > 150) & (g < 100) & (b < 100)
red_pixels = np.where(red_mask)
print(f"Red pixels: {len(red_pixels[0])}")
if len(red_pixels[0]) > 0:
    red_cx = float(np.mean(red_pixels[1]))
    red_cy = float(np.mean(red_pixels[0]))
    print(f"Red center: ({red_cx:.1f}, {red_cy:.1f})")

white_mask = (r > 240) & (g > 240) & (b > 240)
print(f"\nWhite pixels: {int(np.sum(white_mask))}")

dark_mask = (r < 50) & (g < 50) & (b < 50)
print(f"Dark/black pixels: {int(np.sum(dark_mask))}")

print("\nDone!")
