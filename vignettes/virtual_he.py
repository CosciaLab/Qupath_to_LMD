"""Turn the TD_01 mIF image into a virtual H&E, once, so vignette 1 has a tissue background.

Run:  uv run --with tifffile python virtual_he.py <tif> <out.npy>
"""

import sys

import numpy
import tifffile

DAPI, VIMENTIN, PANCK, COL1A1 = 9, 5, 7, 10
# Ruifrok & Johnston optical-density vectors: what haematoxylin and eosin absorb, per RGB channel
H_OD = numpy.array([0.650, 0.704, 0.286])
E_OD = numpy.array([0.072, 0.990, 0.105])


def norm(channel):
    """Stretch a channel to 0-1 between its 1st and 99.5th percentiles."""
    lo, hi = numpy.percentile(channel, [1, 99.5])
    return numpy.clip((channel.astype(float) - lo) / (hi - lo), 0, 1)


stack = tifffile.imread(sys.argv[1])
hema = norm(stack[DAPI])
eosin = numpy.clip(0.7 * norm(stack[PANCK]) + 0.6 * norm(stack[COL1A1]) + 0.4 * norm(stack[VIMENTIN]), 0, 1)
eosin = 0.12 + 0.88 * eosin  # stroma is never fully unstained on a real slide
od = 1.9 * hema[..., None] * H_OD + 0.75 * eosin[..., None] * E_OD
rgb = numpy.exp(-od)
numpy.save(sys.argv[2], rgb.astype(numpy.float32))
print(rgb.shape, rgb.min(), rgb.max())
