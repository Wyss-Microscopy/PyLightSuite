"""Generate control-point slice list (generate_cp_list_alt.m)."""

from __future__ import annotations

import numpy as np


def default_ap_cut_axis(volume_shape: tuple[int, int, int]) -> int:
    """Heuristic AP axis: longest volume extent (1-based axis index)."""
    return int(np.argmax(volume_shape) + 1)


def generate_ap_alignment_list(
    volume_shape: tuple[int, int, int],
    *,
    cut_axis: int | None = None,
    n_slices: int = 20,
) -> np.ndarray:
    """Return chooselist rows for AP-only slice alignment: [index, cut_axis, 1, 1]."""
    axis = cut_axis if cut_axis is not None else default_ap_cut_axis(volume_shape)
    if axis not in {1, 2, 3}:
        msg = f"cut_axis must be 1, 2, or 3, got {axis}"
        raise ValueError(msg)
    axis_size = volume_shape[axis - 1]
    nmin = min(volume_shape)
    minstart = max(1, int(np.ceil(nmin / 20)))
    if axis_size <= 2 * minstart:
        minstart = 1
    sids = np.round(np.linspace(minstart, axis_size - minstart, n_slices)).astype(int)
    sids = np.clip(sids, 1, axis_size)
    return np.column_stack(
        [
            sids,
            np.full(n_slices, axis, dtype=int),
            np.ones(n_slices, dtype=int),
            np.ones(n_slices, dtype=int),
        ]
    ).astype(int)


_CP_N_PER_SIDE = 20
_CP_INDMAT = np.array([[1, 1], [1, 2], [2, 1], [2, 2]], dtype=int)

# MATLAB ``rng(1); randperm(20)`` from generate_cp_list_alt.m, 1-based.
#
# MATLAB seeds the Mersenne Twister; numpy's default_rng uses PCG64, so the two
# streams differ and the row order cannot be reproduced numerically. Set this to
# the MATLAB values to make Python chooselist rows line up with MATLAB-authored
# ``atlas2histology_tform.mat`` cells. While ``None``, Python falls back to its
# own deterministic permutation, which is self-consistent but not MATLAB-compatible.
MATLAB_IPERM_20: tuple[int, ...] = (
    3, 15, 6, 19, 5, 7, 20, 13, 4, 8, 9, 1, 17, 11, 10, 18, 16, 12, 2, 14
)


def _cp_iperm() -> np.ndarray:
    """Zero-based permutation of the 20 per-side entries (MATLAB ``iperm``)."""
    if MATLAB_IPERM_20 is not None:
        perm = np.asarray(MATLAB_IPERM_20, dtype=int) - 1
        if sorted(perm.tolist()) != list(range(_CP_N_PER_SIDE)):
            msg = f"MATLAB_IPERM_20 must be a permutation of 1..{_CP_N_PER_SIDE}"
            raise ValueError(msg)
        return perm
    return np.random.default_rng(1).permutation(_CP_N_PER_SIDE)


def matlab_chooselist_is_available() -> bool:
    """True when ``generate_control_point_list`` reproduces MATLAB row order."""
    return MATLAB_IPERM_20 is not None


def generate_control_point_list(volume_shape: tuple[int, int, int]) -> np.ndarray:
    """Return chooselist array with columns [slice_index, axis, flag_a, flag_b].

    Faithful port of ``generate_cp_list_alt.m``. MATLAB reshapes column-major
    throughout, so the block partition and row order both differ from a naive
    row-major translation; getting this wrong makes cell *i* of a MATLAB
    ``atlas2histology_tform.mat`` refer to a different anatomical slice.
    """
    n_per_side = _CP_N_PER_SIDE
    n_types = int(_CP_INDMAT.shape[0])
    nmin = min(volume_shape)
    minstart = int(np.ceil(nmin / 20))

    # MATLAB ``cat(1, dataall{:})`` linearises the 3 x Ntypes cell column-major,
    # so ``idim`` varies fastest inside each ``ii`` group.
    blocks: list[np.ndarray] = []
    for ii in range(n_types):
        for idim in range(3):
            flat = np.round(
                np.linspace(minstart, volume_shape[idim] - minstart, n_per_side * n_types)
            ).astype(int)
            sids = flat.reshape(n_types, n_per_side, order="F")
            blocks.append(
                np.column_stack(
                    [
                        sids[ii],
                        np.full(n_per_side, idim + 1, dtype=int),
                        np.full(n_per_side, _CP_INDMAT[ii, 0], dtype=int),
                        np.full(n_per_side, _CP_INDMAT[ii, 1], dtype=int),
                    ]
                )
            )
    stacked = np.vstack(blocks)

    # reshape([Nperside 3 Ntypes Ntypes]) -> permute([4 2 3 1]) -> reshape(Ntypes, [])'
    # resolves to: row j reads stacked row iperm[j // 12] + 20 * (j % 3) + 60 * ((j // 3) % 4).
    iperm = _cp_iperm()
    n_rows = n_per_side * 3 * n_types
    rows = np.arange(n_rows)
    source = iperm[rows // 12] + n_per_side * (rows % 3) + 60 * ((rows // 3) % n_types)
    return stacked[source].astype(int)


def generate_cord_longitudinal_list(
    length_axis_size: int,
    n_slices: int = 20,
    *,
    cut_axis: int = 3,
) -> np.ndarray:
    """Return ordered chooselist rows for longitudinal align (rostrocaudal anchors)."""
    if cut_axis not in {1, 2, 3}:
        msg = f"cut_axis must be 1, 2, or 3, got {cut_axis}"
        raise ValueError(msg)
    if length_axis_size < 1:
        msg = f"length_axis_size must be >= 1, got {length_axis_size}"
        raise ValueError(msg)
    minstart = max(1, int(np.ceil(length_axis_size / 20)))
    if length_axis_size <= 2 * minstart:
        minstart = 1
    sids = np.round(np.linspace(minstart, length_axis_size - minstart, n_slices)).astype(int)
    sids = np.clip(sids, 1, length_axis_size)
    return np.column_stack(
        [
            sids,
            np.full(n_slices, cut_axis, dtype=int),
            np.ones(n_slices, dtype=int),
            np.ones(n_slices, dtype=int),
        ]
    ).astype(int)


# MATLAB ``rng(1); randperm(100)`` (Mersenne Twister, R2024a), 1-based. The cord GUI
# shows its 100 slices in this order, so slice numbers match matchControlPoints_unified.m.
MATLAB_RANDPERM_100_SEED1: tuple[int, ...] = (
    3, 99, 39, 51, 15, 28, 65, 56, 27, 6, 31, 61, 45, 91, 50, 72, 73, 19, 58, 5,
    76, 29, 7, 20, 13, 53, 96, 54, 85, 43, 49, 48, 4, 23, 36, 8, 78, 84, 9, 75,
    95, 62, 64, 1, 17, 11, 32, 87, 46, 93, 55, 68, 34, 66, 10, 18, 98, 57, 94, 70,
    59, 100, 90, 82, 89, 67, 16, 52, 12, 37, 35, 24, 63, 60, 2, 80, 42, 40, 79, 83,
    44, 21, 74, 38, 25, 14, 30, 81, 26, 86, 97, 71, 47, 77, 69, 92, 33, 88, 22, 41,
)


def generate_cord_control_point_list(
    length_axis_size: int,
    n_slices: int = 100,
    *,
    cut_axis: int = 3,
) -> np.ndarray:
    """Return chooselist rows for spinal cord match-points (matchControlPoints_unified.m).

    ``round(linspace(1, N, n_slices))`` sample slices, rows ``[slice_index, cut_axis, 1, 1]``,
    shuffled exactly like MATLAB's ``rng(1); randperm(n_slices)`` for the default 100
    slices (other counts fall back to a seeded numpy permutation).
    """
    if cut_axis not in {1, 2, 3}:
        msg = f"cut_axis must be 1, 2, or 3, got {cut_axis}"
        raise ValueError(msg)
    if length_axis_size < 1:
        msg = f"length_axis_size must be >= 1, got {length_axis_size}"
        raise ValueError(msg)
    # MATLAB round() rounds halves away from zero
    sids = np.floor(np.linspace(1, length_axis_size, n_slices) + 0.5).astype(int)
    sids = np.clip(sids, 1, length_axis_size)
    chooselist = np.column_stack(
        [
            sids,
            np.full(n_slices, cut_axis, dtype=int),
            np.ones(n_slices, dtype=int),
            np.ones(n_slices, dtype=int),
        ]
    )
    if n_slices == len(MATLAB_RANDPERM_100_SEED1):
        order = np.asarray(MATLAB_RANDPERM_100_SEED1, dtype=int) - 1
    else:
        order = np.random.default_rng(1).permutation(n_slices)
    return chooselist[order].astype(int)


