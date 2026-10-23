"""ERA5 monthly means on pressure levels -> the simulator's wind / temperature climatology.

    python tools/era5_climatology.py ERA5_FILE.nc [more.nc ...] [-o atcsim/data/climate/era5_gmmm.npz] [--step 0.5]

Input: "ERA5 monthly averaged data on pressure levels" (Copernicus CDS), product "monthly averaged reanalysis",
variables u, v, t, any set of pressure levels, any years, over the region. Both the 2024+ CDS NetCDF layout
(valid_time / pressure_level / latitude / longitude) and the older one (time / level / ...) are read; several files
(e.g. one per variable or per decade) are merged.

Output (numpy .npz, float32, a few MB):
    lat, lon            grid (degrees, ascending), coarsened to --step (default 0.5 deg)
    p_hpa, fl           pressure levels and their ISA pressure altitudes (flight levels), ascending FL
    u, v, t             [12, nlev, nlat, nlon] monthly climatological means (kt, kt, deg C)
    u_sd, v_sd          [12, nlev, nlat, nlon] interannual standard deviation of the monthly means (kt)
    eof_u, eof_v        [12, nlev, nlat, nlon] leading mode of the year-to-year anomalies of each month (u and v
                        together, in kt for one standard deviation of its principal component): "+1" = a typical
                        strong-jet month, "-1" = a weak one
    eof_var             [12] fraction of the anomaly variance that mode explains
    years               the years used
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

MS_TO_KT = 1.0 / 0.514444


def pressure_to_fl(p_hpa):
    """ISA pressure altitude in flight levels (troposphere and the isothermal layer above 11 km)."""
    p = np.asarray(p_hpa, float)
    h_trop = 44330.77 * (1.0 - (p / 1013.25) ** 0.190263)              # metres
    p11 = 226.32
    h_strat = 11000.0 + 6341.62 * np.log(p11 / p)
    h = np.where(p >= p11, h_trop, h_strat)
    return h / 0.3048 / 100.0


def _open(paths):
    import xarray as xr
    dss = [xr.open_dataset(p) for p in paths]
    ds = xr.merge(dss, compat="override")
    ren = {}
    for a, b in (("valid_time", "time"), ("pressure_level", "level"), ("latitude", "lat"), ("longitude", "lon"),
                 ("isobaricInhPa", "level")):
        if a in ds.dims or a in ds.coords:
            ren[a] = b
    ds = ds.rename(ren)
    if "expver" in ds.dims:                               # ERA5 / ERA5T mix
        ds = ds.max(dim="expver", skipna=True)
    if "number" in ds.dims:
        ds = ds.isel(number=0)
    return ds


def build(paths, out, step=0.5):
    ds = _open(paths)
    for v in ("u", "v"):
        if v not in ds:
            raise SystemExit(f"variable {v} missing (have {list(ds.data_vars)})")
    lon = ds["lon"].values
    if lon.max() > 180:
        ds = ds.assign_coords(lon=((ds["lon"] + 180) % 360) - 180)
    ds = ds.sortby("lat").sortby("lon").sortby("level", ascending=False)   # high pressure (low FL) first
    if step and step > 0:
        lat_new = np.arange(np.ceil(ds.lat.min() / step) * step, ds.lat.max() + 1e-6, step)
        lon_new = np.arange(np.ceil(ds.lon.min() / step) * step, ds.lon.max() + 1e-6, step)
        ds = ds.interp(lat=lat_new, lon=lon_new)
    t = ds["time"].values
    months = (t.astype("datetime64[M]").astype(int) % 12) + 1
    years = t.astype("datetime64[Y]").astype(int) + 1970
    p = ds["level"].values.astype(float)
    fl = pressure_to_fl(p)
    U = ds["u"].values * MS_TO_KT                        # [time, lev, lat, lon]
    V = ds["v"].values * MS_TO_KT
    T = ds["t"].values - 273.15 if "t" in ds else None
    nlev, nlat, nlon = U.shape[1:]
    shape = (12, nlev, nlat, nlon)
    out_d = {k: np.zeros(shape, np.float32) for k in ("u", "v", "t", "u_sd", "v_sd", "eof_u", "eof_v")}
    eof_var = np.zeros(12, np.float32)
    for m in range(1, 13):
        sel = months == m
        if not sel.any():
            raise SystemExit(f"no data for month {m}")
        u, v = U[sel], V[sel]
        out_d["u"][m - 1], out_d["v"][m - 1] = u.mean(0), v.mean(0)
        out_d["u_sd"][m - 1], out_d["v_sd"][m - 1] = u.std(0), v.std(0)
        if T is not None:
            out_d["t"][m - 1] = T[sel].mean(0)
        n = u.shape[0]
        if n >= 3:                                        # leading mode of the year-to-year anomalies (EOF 1)
            X = np.concatenate([(u - u.mean(0)).reshape(n, -1), (v - v.mean(0)).reshape(n, -1)], axis=1)
            U_, S_, Vt = np.linalg.svd(X, full_matrices=False)
            pc_sd = S_[0] / np.sqrt(n)                   # standard deviation of the leading principal component
            mode = Vt[0] * pc_sd
            # sign: "+1" strengthens the mean westerly over the region at the jet levels
            half = mode.size // 2
            if (mode[:half].reshape(nlev, nlat, nlon) * np.sign(u.mean(0))).sum() < 0:
                mode = -mode
            out_d["eof_u"][m - 1] = mode[:half].reshape(nlev, nlat, nlon)
            out_d["eof_v"][m - 1] = mode[half:].reshape(nlev, nlat, nlon)
            eof_var[m - 1] = float(S_[0] ** 2 / (S_ ** 2).sum())
    order = np.argsort(fl)
    for k in out_d:
        out_d[k] = out_d[k][:, order]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    np.savez_compressed(out, lat=ds.lat.values.astype(np.float32), lon=ds.lon.values.astype(np.float32),
                        p_hpa=p[order].astype(np.float32), fl=fl[order].astype(np.float32), eof_var=eof_var,
                        years=np.unique(years).astype(np.int32), **out_d)
    print(f"{out}: {len(np.unique(years))} years {years.min()}-{years.max()}, levels {list(np.round(p[order]))} hPa, "
          f"grid {ds.lat.size}x{ds.lon.size} at {step} deg, {os.path.getsize(out) / 1e6:.1f} MB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", default=os.path.join(os.path.dirname(__file__), "..", "atcsim", "data", "climate",
                                                        "era5_gmmm.npz"))
    ap.add_argument("--step", type=float, default=0.5)
    a = ap.parse_args()
    build(a.files, a.out, a.step)
    sys.exit(0)
