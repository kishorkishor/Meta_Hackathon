"""Spectral indices on Sentinel-2 L2A surface reflectance (0..1). All return NaN where the input is NaN.

Band names follow Earth Search asset keys:
  blue B02, green B03, red B04, nir08 B8A (20 m), swir16 B11, swir22 B12.
  "nir" is the NIR used by NDVI and NDBI; the composite sets it to B8A.
"""
import numpy as np


def _nd(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (a - b) / (a + b)
    out[~np.isfinite(out)] = np.nan
    return out


def ndvi(b):
    """Vegetation: (NIR - red) / (NIR + red)."""
    return _nd(b["nir"], b["red"])


def nbr(b):
    """Normalized Burn Ratio: (B8A - B12) / (B8A + B12). Burning drops NIR and raises SWIR."""
    return _nd(b["nir08"], b["swir22"])


def mndwi(b):
    """Modified water index (Xu 2006): (green - B11) / (green + B11). Above 0 is usually open water."""
    return _nd(b["green"], b["swir16"])


def ndbi(b):
    """Built-up index: (B11 - NIR) / (B11 + NIR). Rises on roofs, concrete and bare soil."""
    return _nd(b["swir16"], b["nir"])


def brightness(b):
    """Mean visible reflectance; very dark pixels are often terrain or cloud shadow."""
    return (b["blue"] + b["green"] + b["red"]) / 3.0


def all_indices(b):
    return {"ndvi": ndvi(b), "nbr": nbr(b), "mndwi": mndwi(b), "ndbi": ndbi(b), "bright": brightness(b)}
