"""Core logic on small synthetic scenes: indices, detection rules, doubt flags, regions, report."""
import math

import numpy as np
import pytest

from app import imagery
from app.detect import (BUILT, BURN, FLAG_CLOUD, FLAG_DARK, FLAG_SHIFT, LIKELY, NONE, SURE, UNSURE,
                        VEG_LOSS, WATER_GONE, WATER_NEW, detect)
from app.indices import mndwi, nbr, ndbi, ndvi
from app.regions import regions, totals

N = 40

# Typical L2A surface reflectance for a few covers.
FOREST = dict(blue=0.03, green=0.06, red=0.04, nir=0.35, nir08=0.36, swir16=0.16, swir22=0.07)
BURNED = dict(blue=0.04, green=0.05, red=0.06, nir=0.12, nir08=0.12, swir16=0.20, swir22=0.18)
WATER = dict(blue=0.06, green=0.07, red=0.05, nir=0.03, nir08=0.03, swir16=0.01, swir22=0.01)
CROP = dict(blue=0.04, green=0.08, red=0.05, nir=0.30, nir08=0.31, swir16=0.18, swir22=0.09)
SOIL = dict(blue=0.10, green=0.13, red=0.17, nir=0.22, nir08=0.23, swir16=0.30, swir22=0.24)
ROOFS = dict(blue=0.12, green=0.13, red=0.14, nir=0.18, nir08=0.18, swir16=0.25, swir22=0.22)
SHADOW = dict(blue=0.005, green=0.008, red=0.006, nir=0.05, nir08=0.05, swir16=0.03, swir22=0.02)


def scene(cover, n=N):
    return {b: np.full((n, n), v, np.float32) for b, v in cover.items()}


def paint(s, cover, ys, xs):
    for b, v in cover.items():
        s[b][ys, xs] = v
    return s


def test_index_formulas():
    b = scene(FOREST, 2)
    assert np.allclose(ndvi(b), (0.35 - 0.04) / (0.35 + 0.04))
    assert np.allclose(nbr(b), (0.36 - 0.07) / (0.36 + 0.07))
    assert np.allclose(mndwi(b), (0.06 - 0.16) / (0.06 + 0.16))
    assert np.allclose(ndbi(b), (0.16 - 0.35) / (0.16 + 0.35))


def test_indices_are_nan_where_input_is_nan_or_zero():
    b = scene(FOREST, 2)
    b["nir"][0, 0] = np.nan
    b["nir"][0, 1] = 0.0
    b["red"][0, 1] = 0.0
    out = ndvi(b)
    assert np.isnan(out[0, 0]) and np.isnan(out[0, 1]) and np.isfinite(out[1, 1])


def test_unchanged_forest_has_no_change():
    d = detect(scene(FOREST), scene(FOREST))
    assert (d.classes == NONE).all()
    assert d.seen.all()


def test_burn_is_found_and_is_sure_in_the_middle():
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    d = detect(scene(FOREST), after)
    assert (d.classes[10:30, 10:30] == BURN).all()
    assert (d.classes[:10] == NONE).all()
    assert d.confidence[20, 20] == SURE


def test_burn_needs_vegetation_before():
    before = paint(scene(FOREST), SOIL, slice(10, 30), slice(10, 30))
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    d = detect(before, after)
    assert not (d.classes[10:30, 10:30] == BURN).any()


def test_new_water_and_water_gone():
    after = paint(scene(CROP), WATER, slice(5, 20), slice(5, 35))
    d = detect(scene(CROP), after)
    assert (d.classes[8:18, 8:32] == WATER_NEW).all()
    d2 = detect(after, scene(CROP))
    assert (d2.classes[8:18, 8:32] == WATER_GONE).all()


def test_vegetation_loss_without_burn_signal():
    # forest thinned to grass and shrubs: NDVI falls well below 0.5, NBR drop stays under the burn rule
    thinned = dict(blue=0.05, green=0.08, red=0.10, nir=0.28, nir08=0.29, swir16=0.22, swir22=0.12)
    after = paint(scene(FOREST), thinned, slice(10, 30), slice(10, 30))
    d = detect(scene(FOREST), after)
    assert (d.classes[15:25, 15:25] == VEG_LOSS).all()


def test_cleared_to_bright_soil_is_bare_ground_not_burn():
    after = paint(scene(FOREST), SOIL, slice(10, 30), slice(10, 30))
    d = detect(scene(FOREST), after)
    assert (d.classes[15:25, 15:25] == BUILT).all()


def test_seasonal_drying_everywhere_is_not_vegetation_loss():
    # the whole scene dries a little (NDVI 0.80 -> 0.68), as between January and May in the hills
    dry = dict(FOREST, red=0.065, nir=0.34, nir08=0.35)
    d = detect(scene(FOREST), scene(dry))
    assert (d.classes == NONE).all()
    assert d.offsets["ndvi_drop"] > 0.05


def test_no_offset_when_the_whole_area_changed():
    d = detect(scene(FOREST), scene(BURNED))
    assert d.offsets["nbr_drop"] == 0.0
    assert (d.classes == BURN).all()


def test_a_burn_over_most_of_the_area_is_not_explained_away():
    # 60 percent burned, the rest dried a little: the season is measured on the unburned 40 percent
    dry = dict(FOREST, red=0.05)
    after = paint(scene(dry), BURNED, slice(0, 24), slice(0, N))
    d = detect(scene(FOREST), after)
    assert 0 < d.offsets["ndvi_drop"] < 0.1
    assert (d.classes[:24] == BURN).all()
    assert (d.classes[24:] == NONE).all()


def test_widespread_shallow_flood_and_its_retreat_are_found():
    # a shallow flood everywhere: MNDWI goes from -0.11 to +0.11 over the whole area
    land = dict(CROP, green=0.08, swir16=0.10)
    wet = dict(CROP, green=0.10, swir16=0.08)
    d = detect(scene(land), scene(wet))
    assert d.offsets["mndwi_rise"] == 0.0
    assert (d.classes == WATER_NEW).all()
    assert (detect(scene(wet), scene(land)).classes == WATER_GONE).all()


def test_moderate_widespread_burn_survives():
    # a uniform NBR drop of about 0.35 everywhere must still be a burn
    moderate = dict(blue=0.04, green=0.05, red=0.06, nir=0.24, nir08=0.25, swir16=0.20, swir22=0.12)
    d = detect(scene(FOREST), scene(moderate))
    drop = float(d.before["nbr"][0, 0] - d.after["nbr"][0, 0])
    assert 0.3 < drop < 0.4
    assert (d.classes == BURN).all()


def test_drying_that_stays_green_is_not_vegetation_loss():
    browner = dict(FOREST, red=0.09)   # NDVI 0.80 -> 0.59: drop 0.21 but still well vegetated
    after = paint(scene(FOREST), browner, slice(10, 30), slice(10, 30))
    d = detect(scene(FOREST), after)
    assert not (d.classes == VEG_LOSS).any()


def test_condition_close_to_its_limit_is_not_sure():
    # burn that is only just dark enough: every other condition is strong
    dim = dict(BURNED, blue=0.11, green=0.11, red=0.11)   # brightness 0.11, limit 0.12, margin 0.02
    after = paint(scene(FOREST), dim, slice(10, 30), slice(10, 30))
    d = detect(scene(FOREST), after)
    assert d.classes[20, 20] == BURN
    assert d.confidence[20, 20] != SURE


def test_new_building_on_cropland():
    after = paint(scene(CROP), ROOFS, slice(10, 30), slice(10, 30))
    d = detect(scene(CROP), after)
    got = d.classes[15:25, 15:25]
    assert (got == BUILT).all(), np.unique(got)


def test_missing_pixels_are_unseen_and_buffer_flags_neighbours():
    after = paint(scene(FOREST), BURNED, slice(0, N), slice(0, N))
    for b in after:
        after[b][:, :5] = np.nan
    d = detect(scene(FOREST), after)
    assert not d.seen[:, :5].any()
    assert (d.classes[:, :5] == NONE).all()
    assert (d.flags[:, 5:7] & FLAG_CLOUD).all()
    assert not (d.flags[:, 10:] & FLAG_CLOUD).any()


def test_one_pixel_shift_of_an_edge_is_flagged():
    before = paint(scene(FOREST), SOIL, slice(0, N), slice(0, 20))
    after = paint(scene(FOREST), SOIL, slice(0, N), slice(0, 21))   # boundary moved by one pixel
    d = detect(before, after)
    edge = d.classes[:, 20] > 0
    assert edge.any()
    assert (d.flags[:, 20][edge] & FLAG_SHIFT).all()
    assert (d.confidence[:, 20][edge] == UNSURE).all()


def test_dark_pixels_are_unsure():
    before = paint(scene(FOREST), SHADOW, slice(10, 30), slice(10, 30))
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    d = detect(before, after)
    changed = d.classes[12:28, 12:28] > 0
    assert changed.any()
    assert (d.flags[12:28, 12:28][changed] & FLAG_DARK).all()


def test_one_look_lowers_confidence():
    after = paint(scene(FOREST), BURNED, slice(10, 30), slice(10, 30))
    ones = np.ones((N, N), np.int16)
    d = detect(scene(FOREST), after, ones * 3, ones)
    assert d.confidence[20, 20] == LIKELY


def test_region_ids_follow_the_patch_not_the_box():
    # a thin ring around a bigger separate patch: the ring's box contains the inner patch
    after = scene(FOREST)
    paint(after, BURNED, slice(5, 35), slice(5, 6))
    paint(after, BURNED, slice(5, 35), slice(34, 35))
    paint(after, BURNED, slice(5, 6), slice(5, 35))
    paint(after, BURNED, slice(34, 35), slice(5, 35))
    paint(after, BURNED, slice(12, 28), slice(12, 28))
    d = detect(scene(FOREST), after)
    g = imagery.Grid(90.0, 23.0, 90.0078, 23.0072, N, N)
    feats, ids = regions(d, g)
    inner = next(f for f in feats if f["properties"]["pixel_box"] == [12, 12, 28, 28])["properties"]["id"]
    ring = next(f for f in feats if f["properties"]["pixel_box"] == [5, 5, 35, 35])["properties"]["id"]
    assert (ids == inner).sum() == 256
    assert (ids == ring).sum() == 4 * 30 - 4


def test_grid_is_about_20_m_and_covers_the_bbox():
    g = imagery.Grid.for_bbox((90.0, 23.0, 90.1, 23.1), 20.0)
    assert g.lon1 >= 90.1 and g.lat1 >= 23.1
    dx = (g.lon1 - g.lon0) / g.width * 111_320 * math.cos(math.radians(23.05))
    dy = (g.lat1 - g.lat0) / g.height * 111_320
    assert abs(dx - 20) < 0.5 and abs(dy - 20) < 0.5
    assert abs(g.pixel_area_ha() - 0.04) < 0.002
    with pytest.raises(ValueError):
        imagery.Grid.for_bbox((90.1, 23.0, 90.0, 23.1))


def test_overview_levels_match_the_grid():
    assert imagery.overview_for("red", 10) is None
    assert imagery.overview_for("red", 20) == 0
    assert imagery.overview_for("red", 40) == 1
    assert imagery.overview_for("swir22", 20) is None
    assert imagery.overview_for("swir22", 40) == 0


def test_resolution_switches_with_area():
    assert imagery.res_for_area((90.0, 23.0, 90.05, 23.05)) == 20.0
    assert imagery.res_for_area((90.0, 23.0, 90.2, 23.2)) == 40.0


def test_regions_have_area_ids_and_polygons():
    after = paint(scene(FOREST), BURNED, slice(2, 12), slice(2, 12))
    paint(after, BURNED, slice(25, 30), slice(25, 30))
    paint(after, BURNED, slice(35, 36), slice(35, 37))  # 2 px: below the minimum size
    d = detect(scene(FOREST), after)
    g = imagery.Grid.for_bbox((90.0, 23.0, 90.0078, 23.0072), 20.0)
    g = imagery.Grid(g.lon0, g.lat0, g.lon1, g.lat1, N, N)
    feats, ids = regions(d, g)
    assert [f["properties"]["id"] for f in feats] == [1, 2]
    assert (ids[2:12, 2:12] == 1).all() and (ids[25:30, 25:30] == 2).all() and ids[35, 35] == 0
    big = feats[0]["properties"]
    assert big["kind"] == "burned land"
    assert big["area_ha"] == pytest.approx(100 * g.pixel_area_ha(), rel=1e-6)
    assert feats[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    t = totals(d, g, ids)
    # the 2 pixel speck is below the minimum patch size and is not counted
    assert t["by_kind"]["burned land"]["area_ha"] == pytest.approx(round(125 * g.pixel_area_ha(), 1), abs=0.11)
