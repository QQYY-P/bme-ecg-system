"""Rebuild ECG app icons with Pillow (authoring only; not a server dependency)."""
from pathlib import Path
from PIL import Image, ImageDraw


def build():
    folder=Path(__file__).resolve().parents[1]/'web/icons';folder.mkdir(exist_ok=True)
    image=Image.new('RGB',(1024,1024),'#087f71');draw=ImageDraw.Draw(image)
    # All foreground artwork stays inside the maskable icon's safe centre.
    points=[(280,524),(366,524),(407,470),(456,620),(526,340),(585,554),(654,554),(714,500),(746,500)]
    draw.line(points,fill='#ffffff',width=28,joint='curve')
    for x,y in points:draw.ellipse((x-14,y-14,x+14,y+14),fill='#ffffff')
    for name,size in [('icon-192',192),('icon-512',512),('icon-maskable',512),('apple-touch-icon',180)]:
        image.resize((size,size),Image.Resampling.LANCZOS).save(folder/(name+'.png'),optimize=True)


if __name__=='__main__':build()
