"""Create clearly marked, disposable images for authorized phone tests."""
from pathlib import Path
import argparse
from PIL import Image, ImageDraw, ImageFont

destination = Path(__file__).parents[1] / "artifacts" / "demo"
destination.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 40)
parser = argparse.ArgumentParser()
parser.add_argument("--start", type=int, default=5)
parser.add_argument("--count", type=int, default=6)
options = parser.parse_args()
for number in range(options.start, options.start + options.count):
    image = Image.new("RGB", (800, 450), (30 + number * 8 % 200, 65, 125))
    ImageDraw.Draw(image).text((35, 185), f"DEMO - Best gallery {number}", font=font, fill="white")
    image.save(destination / f"best_gallery_demo_{number}.jpg", quality=90)
