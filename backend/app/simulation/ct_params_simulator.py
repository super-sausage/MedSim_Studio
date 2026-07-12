"""
CT parameter simulation core for MVP preview generation.

This module applies image-domain approximations of CT acquisition and
reconstruction parameters to a HU volume using only NumPy/SciPy.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
from scipy.ndimage import (
    affine_transform,
    binary_dilation,
    binary_fill_holes,
    gaussian_filter,
    gaussian_filter1d,
    label,
    laplace,
    zoom,
)


Spacing3D = Tuple[float, float, float]
AIR_HU = -1000.0

DEFAULT_MAS_BY_DOSE_LEVEL: Dict[str, int] = {
    "low": 50,
    "standard": 150,
    "high": 300,
}

KVP_REFERENCE_FACTORS: Dict[int, Dict[str, float]] = {
    80: {"mu_water": 0.0215, "contrast_gain": 1.18, "bone_hardening": 1.14},
    100: {"mu_water": 0.0205, "contrast_gain": 1.09, "bone_hardening": 1.08},
    120: {"mu_water": 0.0195, "contrast_gain": 1.00, "bone_hardening": 1.00},
    140: {"mu_water": 0.0188, "contrast_gain": 0.93, "bone_hardening": 0.94},
}


def _center_slice_stats(volume: np.ndarray) -> Dict[str, float]:
    center_idx = int(volume.shape[0] // 2)
    center_slice = volume[center_idx]
    return {
        "slice_index": center_idx,
        "min": float(np.min(center_slice)),
        "max": float(np.max(center_slice)),
        "mean": float(np.mean(center_slice)),
        "std": float(np.std(center_slice)),
    }


def _clipped_float32(volume: np.ndarray) -> np.ndarray:
    return np.clip(volume, -1024.0, 3071.0).astype(np.float32, copy=False)


def _material_masks(volume: np.ndarray) -> Dict[str, np.ndarray]:
    """Coarse HU-based tissue groups for image-domain parameter remapping."""
    return {
        "air": volume < -900.0,
        "lung": (volume >= -900.0) & (volume < -500.0),
        "fat": (volume >= -180.0) & (volume < -30.0),
        "soft": (volume >= -30.0) & (volume < 120.0),
        "vascular": (volume >= 120.0) & (volume < 300.0),
        "cancellous_bone": (volume >= 300.0) & (volume < 1000.0),
        "cortical_bone": volume >= 1000.0,
    }


def _hu_to_relative_mu(volume: np.ndarray, kVp: int) -> np.ndarray:
    params = KVP_REFERENCE_FACTORS[kVp]
    mu_water = params["mu_water"]
    rel_mu = mu_water * (1.0 + volume.astype(np.float32, copy=False) / 1000.0)
    return np.clip(rel_mu, mu_water * 0.02, mu_water * 4.5).astype(np.float32, copy=False)


def _projection_thickness_surrogate(mu_map: np.ndarray) -> np.ndarray:
    centered_mu = np.clip(mu_map - float(np.percentile(mu_map, 4)), 0.0, None)
    proj_y = gaussian_filter(np.sum(centered_mu, axis=1), sigma=(0.45, 0.9), mode="nearest")
    proj_x = gaussian_filter(np.sum(centered_mu, axis=2), sigma=(0.45, 0.9), mode="nearest")

    proj_y_norm = proj_y / max(float(np.percentile(proj_y, 99.0)), 1e-6)
    proj_x_norm = proj_x / max(float(np.percentile(proj_x, 99.0)), 1e-6)

    backproj_y = np.repeat(proj_y_norm[:, None, :], mu_map.shape[1], axis=1)
    backproj_x = np.repeat(proj_x_norm[:, :, None], mu_map.shape[2], axis=2)
    return (0.55 * backproj_y + 0.45 * backproj_x).astype(np.float32, copy=False)


def _extract_body_support_mask(
    volume: np.ndarray,
    label_volume: Optional[np.ndarray] = None,
    threshold_hu: float = 25.0,
) -> np.ndarray:
    """
    Approximate the physically meaningful body support region.

    The right-side 3D preview is expected to behave like stacked 2D CT slices,
    not a glowing rectangular voxel box. After angle/FOV/noise processing,
    exterior padded air can drift above pure AIR_HU and become volume-visible.
    We therefore keep only the largest connected non-air component, fill its
    internal cavities, and preserve explicit label voxels.
    """
    support_seed = volume > threshold_hu
    support_seed = np.stack(
        [binary_fill_holes(support_seed[z]) for z in range(support_seed.shape[0])],
        axis=0,
    )
    if label_volume is not None:
        support_seed |= label_volume > 0

    labeled, num_features = label(support_seed)
    if num_features <= 0:
        return support_seed

    component_sizes = np.bincount(labeled.ravel())
    component_sizes[0] = 0
    largest_component = labeled == int(np.argmax(component_sizes))
    filled_component = binary_fill_holes(largest_component)
    if label_volume is not None:
        filled_component |= label_volume > 0
    return binary_dilation(filled_component, iterations=1)


def _normalized_radial_map(shape: tuple[int, int, int]) -> np.ndarray:
    _, ny, nx = shape
    yy = np.linspace(-1.0, 1.0, ny, dtype=np.float32)[None, :, None]
    xx = np.linspace(-1.0, 1.0, nx, dtype=np.float32)[None, None, :]
    radial = np.sqrt(xx * xx + yy * yy)
    return np.clip(radial, 0.0, 1.6).astype(np.float32, copy=False)


def _resolve_params(params: Dict[str, Any]) -> Dict[str, Any]:
    dose_level = params["dose_level"]
    resolved_mas = int(params.get("mAs") or DEFAULT_MAS_BY_DOSE_LEVEL[dose_level])
    return {
        "gantry_pitch_deg": float(params.get("gantry_pitch_deg", params.get("gantry_tilt_deg", 0.0))),
        "gantry_yaw_deg": float(params.get("gantry_yaw_deg", 0.0)),
        "gantry_roll_deg": float(params.get("gantry_roll_deg", 0.0)),
        "slice_thickness_mm": float(params["slice_thickness_mm"]),
        "dose_level": dose_level,
        "mAs": resolved_mas,
        "dose_level_reference_mAs": int(DEFAULT_MAS_BY_DOSE_LEVEL[dose_level]),
        "kVp": int(params["kVp"]),
        "pitch": float(params["pitch"]),
        "fov_mm": float(params["fov_mm"]),
        "matrix_size": int(params["matrix_size"]),
        "kernel": params["kernel"],
        "contrast_phase": params["contrast_phase"],
    }


def _rotation_matrix_pitch(pitch_deg: float) -> np.ndarray:
    angle_rad = float(np.deg2rad(pitch_deg))
    cos_a = float(np.cos(angle_rad))
    sin_a = float(np.sin(angle_rad))
    return np.array(
        [
            [cos_a, -sin_a, 0.0],
            [sin_a, cos_a, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _rotation_matrix_yaw(yaw_deg: float) -> np.ndarray:
    angle_rad = float(np.deg2rad(yaw_deg))
    cos_a = float(np.cos(angle_rad))
    sin_a = float(np.sin(angle_rad))
    return np.array(
        [
            [cos_a, 0.0, sin_a],
            [0.0, 1.0, 0.0],
            [-sin_a, 0.0, cos_a],
        ],
        dtype=np.float64,
    )


def _rotation_matrix_roll(roll_deg: float) -> np.ndarray:
    angle_rad = float(np.deg2rad(roll_deg))
    cos_a = float(np.cos(angle_rad))
    sin_a = float(np.sin(angle_rad))
    return np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, cos_a, -sin_a],
            [0.0, sin_a, cos_a],
        ],
        dtype=np.float64,
    )


def _build_gantry_pose_rotation_matrix(
    pitch_deg: float,
    yaw_deg: float,
    roll_deg: float,
) -> np.ndarray:
    return _rotation_matrix_roll(roll_deg) @ _rotation_matrix_yaw(yaw_deg) @ _rotation_matrix_pitch(pitch_deg)


def _estimate_pose_output_shape(
    shape: tuple[int, int, int],
    spacing: Spacing3D,
    rotation: np.ndarray,
) -> tuple[int, int, int]:
    if np.allclose(rotation, np.eye(3), atol=1e-8):
        return shape

    nz, ny, nx = shape
    sz, sy, sx = spacing
    cz = (nz - 1) * 0.5
    cy = (ny - 1) * 0.5
    cx = (nx - 1) * 0.5

    corners_idx = np.array(
        [
            [z, y, x]
            for z in (0.0, float(nz - 1))
            for y in (0.0, float(ny - 1))
            for x in (0.0, float(nx - 1))
        ],
        dtype=np.float64,
    )
    centered_phys = (corners_idx - np.array([cz, cy, cx], dtype=np.float64)) * np.array(
        [sz, sy, sx],
        dtype=np.float64,
    )

    rotated = centered_phys @ rotation.T
    extent_mm = rotated.max(axis=0) - rotated.min(axis=0)

    out_nz = max(int(np.ceil(extent_mm[0] / sz)) + 1, nz)
    out_ny = max(int(np.ceil(extent_mm[1] / sy)) + 1, ny)
    out_nx = max(int(np.ceil(extent_mm[2] / sx)) + 1, nx)
    return out_nz, out_ny, out_nx


def _apply_gantry_pose(
    volume: np.ndarray,
    spacing: Spacing3D,
    gantry_pitch_deg: float,
    gantry_yaw_deg: float,
    gantry_roll_deg: float,
    *,
    label_volume: Optional[np.ndarray] = None,
    support_mask: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray], Dict[str, Any]]:
    if (
        abs(gantry_pitch_deg) < 1e-6
        and abs(gantry_yaw_deg) < 1e-6
        and abs(gantry_roll_deg) < 1e-6
    ):
        return volume, label_volume, support_mask, {
            "gantry_pitch_deg": 0.0,
            "gantry_yaw_deg": 0.0,
            "gantry_roll_deg": 0.0,
            "gantry_tilt_deg": 0.0,
            "rotation_axes": {
                "pitch": "x_left_to_right",
                "yaw": "y_anterior_to_posterior",
                "roll": "z_head_to_feet",
            },
            "rotation_order": ["pitch", "yaw", "roll"],
            "interpolation_order_ct": 1,
            "interpolation_order_label": 0,
            "input_shape": [int(v) for v in volume.shape],
            "output_shape": [int(v) for v in volume.shape],
            "output_shape_changed": False,
        }

    input_shape = tuple(int(v) for v in volume.shape)
    rotation = _build_gantry_pose_rotation_matrix(
        gantry_pitch_deg,
        gantry_yaw_deg,
        gantry_roll_deg,
    )
    output_shape = _estimate_pose_output_shape(input_shape, spacing, rotation)

    input_center_idx = (np.array(input_shape, dtype=np.float64) - 1.0) * 0.5
    output_center_idx = (np.array(output_shape, dtype=np.float64) - 1.0) * 0.5
    spacing_arr = np.array(spacing, dtype=np.float64)
    spacing_matrix = np.diag(spacing_arr)
    inv_spacing_matrix = np.diag(1.0 / spacing_arr)

    # Map output voxel indices back to input voxel indices in spacing-aware coordinates.
    transform_matrix = inv_spacing_matrix @ rotation.T @ spacing_matrix
    offset = input_center_idx - transform_matrix @ output_center_idx

    rotated_volume = affine_transform(
        volume.astype(np.float32, copy=False),
        matrix=transform_matrix,
        offset=offset,
        output_shape=output_shape,
        order=1,
        mode="constant",
        cval=AIR_HU,
        prefilter=True,
    ).astype(np.float32, copy=False)

    rotated_labels: Optional[np.ndarray] = None
    if label_volume is not None:
        rotated_labels = affine_transform(
            label_volume.astype(np.float32, copy=False),
            matrix=transform_matrix,
            offset=offset,
            output_shape=output_shape,
            order=0,
            mode="constant",
            cval=0.0,
            prefilter=False,
        ).astype(np.uint8, copy=False)

    rotated_support_mask: Optional[np.ndarray] = None
    if support_mask is not None:
        rotated_support_mask = affine_transform(
            support_mask.astype(np.float32, copy=False),
            matrix=transform_matrix,
            offset=offset,
            output_shape=output_shape,
            order=0,
            mode="constant",
            cval=0.0,
            prefilter=False,
        ) > 0.5

    return rotated_volume, rotated_labels, rotated_support_mask, {
        "gantry_pitch_deg": float(gantry_pitch_deg),
        "gantry_yaw_deg": float(gantry_yaw_deg),
        "gantry_roll_deg": float(gantry_roll_deg),
        "gantry_tilt_deg": float(gantry_pitch_deg),
        "rotation_axes": {
            "pitch": "x_left_to_right",
            "yaw": "y_anterior_to_posterior",
            "roll": "z_head_to_feet",
        },
        "rotation_order": ["pitch", "yaw", "roll"],
        "interpolation_order_ct": 1,
        "interpolation_order_label": 0,
        "input_shape": [int(v) for v in input_shape],
        "output_shape": [int(v) for v in output_shape],
        "output_shape_changed": list(input_shape) != list(output_shape),
    }


def _apply_slice_thickness(
    volume: np.ndarray,
    spacing: Spacing3D,
    slice_thickness_mm: float,
) -> tuple[np.ndarray, Dict[str, Any]]:
    z_spacing = max(float(spacing[0]), 1e-6)
    if slice_thickness_mm <= z_spacing + 1e-6:
        return volume, {
            "effective_slice_thickness_mm": float(max(slice_thickness_mm, z_spacing)),
            "z_sigma_voxels": 0.0,
            "xy_sigma_voxels": 0.0,
            "slab_span_slices": 1,
            "slab_blend_alpha": 0.0,
            "z_reconstruction_scale": 1.0,
            "xy_reconstruction_scale": 1.0,
            "detail_suppression_alpha": 0.0,
            "thickness_model": "identity",
        }

    thickness_ratio = max(slice_thickness_mm / z_spacing, 1.0)
    sigma_voxels = max((thickness_ratio - 1.0) * 0.95, 0.0)
    blurred = gaussian_filter1d(volume, sigma=sigma_voxels, axis=0, mode="nearest")

    slab_span = max(int(round(thickness_ratio)), 1)
    slab_blend_alpha = 0.0
    if slab_span > 1 and volume.shape[0] > 2:
        slab_blend_alpha = min(0.32 + (thickness_ratio - 1.0) * 0.085, 0.84)
        pad_before = slab_span // 2
        pad_after = slab_span - 1 - pad_before
        padded = np.pad(
            blurred,
            ((pad_before, pad_after), (0, 0), (0, 0)),
            mode="edge",
        )
        cumulative = np.cumsum(padded, axis=0, dtype=np.float32)
        cumulative = np.concatenate(
            [np.zeros((1, cumulative.shape[1], cumulative.shape[2]), dtype=np.float32), cumulative],
            axis=0,
        )
        slab_avg = (cumulative[slab_span:] - cumulative[:-slab_span]) / float(slab_span)
        blurred = blurred * (1.0 - slab_blend_alpha) + slab_avg * slab_blend_alpha

    z_reconstruction_scale = 1.0

    xy_sigma = max((thickness_ratio - 1.0) * 0.24, 0.0)
    if xy_sigma > 1e-6:
        blurred = gaussian_filter(blurred, sigma=(0.0, xy_sigma, xy_sigma), mode="nearest")

    xy_reconstruction_scale = 1.0

    detail_suppression_alpha = min((thickness_ratio - 1.0) * 0.055, 0.42)
    if detail_suppression_alpha > 0.0:
        edge_component = blurred - gaussian_filter(
            blurred,
            sigma=(0.0, 0.65 + xy_sigma * 0.25, 0.65 + xy_sigma * 0.25),
            mode="nearest",
        )
        blurred = blurred - edge_component * detail_suppression_alpha

    return blurred.astype(np.float32, copy=False), {
        "effective_slice_thickness_mm": float(slice_thickness_mm),
        "z_sigma_voxels": float(sigma_voxels),
        "xy_sigma_voxels": float(xy_sigma),
        "slab_span_slices": int(slab_span),
        "slab_blend_alpha": float(slab_blend_alpha),
        "z_reconstruction_scale": float(z_reconstruction_scale),
        "xy_reconstruction_scale": float(xy_reconstruction_scale),
        "detail_suppression_alpha": float(detail_suppression_alpha),
        "thickness_model": "coverage_preserving_z_blur_plus_slab_averaging",
    }


def _apply_dose_noise(
    volume: np.ndarray,
    resolved_params: Dict[str, Any],
    rng: np.random.Generator,
) -> tuple[np.ndarray, Dict[str, Any]]:
    reference_mas = 150.0
    actual_mas = max(float(resolved_params["mAs"]), 1.0)
    dose_level = resolved_params["dose_level"]
    level_scale = {"low": 0.72, "standard": 1.0, "high": 1.35}[dose_level]
    pitch_scale = float(1.0 / np.sqrt(max(float(resolved_params["pitch"]), 0.1)))
    slice_thickness_scale = float(
        np.sqrt(max(float(resolved_params["slice_thickness_mm"]), 0.625) / 5.0)
    )
    kvp = int(resolved_params["kVp"])

    effective_mas = actual_mas * level_scale * pitch_scale * slice_thickness_scale
    photon_flux = max(22000.0 * (effective_mas / reference_mas) * (kvp / 120.0) ** 1.35, 2500.0)
    low_dose_severity = float(
        np.clip(
            0.58 * (reference_mas / max(effective_mas, 1.0)) ** 0.72
            + 0.62 * max((120.0 - kvp) / 40.0, 0.0),
            0.0,
            2.4,
        )
    )

    mu_map = _hu_to_relative_mu(volume, kvp)
    attenuation_proxy = _projection_thickness_surrogate(mu_map)
    radial = _normalized_radial_map(volume.shape)
    bowtie_fluence = np.clip(1.08 - 0.26 * radial * radial, 0.68, 1.12).astype(np.float32)

    transmission = np.exp(-1.55 * attenuation_proxy).astype(np.float32)
    expected_counts = np.clip(photon_flux * bowtie_fluence * transmission, 3.0, None).astype(np.float32)
    noisy_counts = rng.poisson(expected_counts).astype(np.float32)
    log_noise = np.log(expected_counts + 1.0) - np.log(noisy_counts + 1.0)

    base_sigma = 13.5 * np.sqrt(reference_mas / max(effective_mas, 1.0))
    sigma = base_sigma * (1.0 + 0.16 * low_dose_severity)

    materials = _material_masks(volume)
    body_mask = ~materials["air"]
    sigma_map = np.full(volume.shape, sigma * 0.22, dtype=np.float32)
    sigma_map[body_mask] = sigma * 0.72
    sigma_map[materials["soft"]] = sigma * 0.92
    sigma_map[materials["vascular"]] = sigma * 1.05
    sigma_map[materials["cancellous_bone"]] = sigma * 1.18
    sigma_map[materials["cortical_bone"]] = sigma * 1.28

    # Convert projection-domain log noise back into HU-like fluctuations.
    projection_noise = (
        log_noise
        * sigma_map
        * (0.85 + 0.25 * attenuation_proxy + 0.12 * low_dose_severity)
    ).astype(np.float32, copy=False)

    # Blend fine-grain and correlated detector/electronic noise so low-dose images look less synthetic.
    white_noise = rng.normal(0.0, 1.0, size=volume.shape).astype(np.float32)
    correlated_noise = gaussian_filter(
        rng.normal(0.0, 1.0, size=volume.shape).astype(np.float32),
        sigma=(0.25, 0.55, 0.55),
        mode="nearest",
    )
    low_freq_noise = gaussian_filter(
        rng.normal(0.0, 1.0, size=volume.shape).astype(np.float32),
        sigma=(1.2, 3.4, 3.4),
        mode="nearest",
    )
    dense_structure_mask = (materials["cancellous_bone"] | materials["cortical_bone"]).astype(np.float32)
    dense_edges = np.abs(
        gaussian_filter(dense_structure_mask, sigma=(0.0, 0.8, 0.8), mode="nearest")
        - gaussian_filter(dense_structure_mask, sigma=(0.0, 2.2, 2.2), mode="nearest")
    ).astype(np.float32)
    nz = volume.shape[0]
    ny = volume.shape[1]
    nx = volume.shape[2]
    streak_seed = gaussian_filter(
        rng.normal(0.0, 1.0, size=(nz, nx)).astype(np.float32),
        sigma=(0.75, 1.4),
        mode="nearest",
    )[:, None, :]
    streaks = streak_seed * gaussian_filter(dense_structure_mask, sigma=(0.0, 1.2, 0.0), mode="nearest")

    view_band_seed = gaussian_filter(
        rng.normal(0.0, 1.0, size=(nz, ny)).astype(np.float32),
        sigma=(0.9, 2.8),
        mode="nearest",
    )[:, :, None]
    view_banding = view_band_seed * np.clip(0.55 + 0.45 * radial, 0.0, 1.2)

    starburst_phase = np.linspace(0.0, 2.0 * np.pi * (1.0 + low_dose_severity * 0.18), nx, dtype=np.float32)[None, None, :]
    starburst = np.sin(starburst_phase + np.linspace(0.0, 2.0 * np.pi, nz, dtype=np.float32)[:, None, None] * 0.75)
    bone_streaks = starburst * dense_edges * (0.7 + 0.3 * attenuation_proxy)

    detector_noise = sigma_map * (
        0.48 * white_noise
        + (0.16 + 0.06 * low_dose_severity) * correlated_noise
        + (0.06 + 0.12 * low_dose_severity) * low_freq_noise
        + (0.08 + 0.16 * low_dose_severity) * streaks
        + (0.0 + 0.14 * low_dose_severity) * bone_streaks
        + (0.0 + 0.10 * low_dose_severity) * view_banding
    )

    photon_starvation = np.clip((attenuation_proxy - 0.58) / 0.42, 0.0, 1.0).astype(np.float32)
    starvation_streak_amp = 4.0 + 7.5 * low_dose_severity
    starvation_streaks = (
        gaussian_filter(streak_seed[:, 0, :], sigma=(0.55, 1.1), mode="nearest")[:, None, :]
        * photon_starvation
        * dense_edges
        * starvation_streak_amp
    )

    noisy = volume + projection_noise + detector_noise + starvation_streaks

    return noisy.astype(np.float32, copy=False), {
        "noise_sigma_hu": float(sigma),
        "noise_sigma_soft_tissue_hu": float(sigma * 0.92),
        "noise_sigma_dense_bone_hu": float(sigma * 1.28),
        "noise_reference_mAs": reference_mas,
        "dose_level_scale": float(level_scale),
        "pitch_noise_scale": float(pitch_scale),
        "slice_thickness_noise_scale": float(slice_thickness_scale),
        "effective_mAs": float(effective_mas),
        "photon_flux_reference": float(photon_flux),
        "bowtie_fluence_range": [
            float(np.min(bowtie_fluence)),
            float(np.max(bowtie_fluence)),
        ],
        "low_dose_severity": float(low_dose_severity),
        "photon_starvation_streak_hu": float(starvation_streak_amp),
        "noise_model": "projection_domain_poisson_plus_detector_noise_plus_photon_starvation_artifact",
    }


def _apply_kvp_transform(
    volume: np.ndarray,
    kVp: int,
) -> tuple[np.ndarray, Dict[str, Any]]:
    materials = _material_masks(volume)
    transformed = volume.copy()
    params = KVP_REFERENCE_FACTORS[kVp]

    material_scales = {
        80: {
            "lung": 1.02,
            "fat": 1.01,
            "soft": 1.08,
            "vascular": 1.24,
            "cancellous_bone": 1.10,
            "cortical_bone": 1.05,
        },
        100: {
            "lung": 1.01,
            "fat": 1.00,
            "soft": 1.04,
            "vascular": 1.12,
            "cancellous_bone": 1.05,
            "cortical_bone": 1.02,
        },
        120: {
            "lung": 1.00,
            "fat": 1.00,
            "soft": 1.00,
            "vascular": 1.00,
            "cancellous_bone": 1.00,
            "cortical_bone": 1.00,
        },
        140: {
            "lung": 0.995,
            "fat": 0.995,
            "soft": 0.97,
            "vascular": 0.90,
            "cancellous_bone": 0.96,
            "cortical_bone": 0.985,
        },
    }[kVp]

    water_reference = 0.0
    for material_name, scale in material_scales.items():
        mask = materials[material_name]
        if not np.any(mask):
            continue
        transformed[mask] = water_reference + (transformed[mask] - water_reference) * scale

    # Approximate beam-hardening / spectral response with smooth nonlinear terms.
    positive_hu = np.clip(transformed, 0.0, None)
    dense_bone_mask = materials["cancellous_bone"] | materials["cortical_bone"]
    transformed[dense_bone_mask] += (
        np.power(np.clip(positive_hu[dense_bone_mask] / 1000.0, 0.0, 2.5), 1.15)
        * 55.0
        * (params["bone_hardening"] - 1.0)
    )

    vascular_mask = materials["vascular"]
    if np.any(vascular_mask):
        vascular_base = np.clip((positive_hu[vascular_mask] - 80.0) / 180.0, 0.0, 2.5)
        transformed[vascular_mask] += (
            np.power(vascular_base, 1.12)
            * 42.0
            * (params["contrast_gain"] - 1.0)
        )

    soft_mask = materials["soft"]
    if np.any(soft_mask):
        soft_base = np.clip((transformed[soft_mask] + 30.0) / 170.0, 0.0, 2.0)
        transformed[soft_mask] += (
            (soft_base - 0.5)
            * 14.0
            * (params["contrast_gain"] - 1.0)
        )

    edge_component = transformed - gaussian_filter(transformed, sigma=(0.0, 0.55, 0.55), mode="nearest")
    transformed = transformed + edge_component * (params["contrast_gain"] - 1.0) * 0.12

    return transformed.astype(np.float32, copy=False), {
        "material_scales": {key: float(value) for key, value in material_scales.items()},
        "mu_water_cm_inv": float(params["mu_water"]),
        "kvp_model": "piecewise_material_hu_remap_plus_beam_hardening",
        "iodine_like_boost_applied": bool(kVp < 120),
        "vascular_contrast_scale": float(material_scales["vascular"]),
    }


def _apply_pitch_effect(
    volume: np.ndarray,
    pitch: float,
) -> tuple[np.ndarray, Dict[str, Any]]:
    if pitch <= 1.0:
        return volume, {
            "z_sigma_voxels": 0.0,
            "artifact_amplitude_hu": 0.0,
            "helical_blend_alpha": 0.0,
            "pitch_model": "identity",
        }

    sigma_voxels = (pitch - 1.0) * 0.8
    degraded = gaussian_filter1d(volume, sigma=sigma_voxels, axis=0, mode="nearest")
    helical_blend_alpha = min((pitch - 1.0) * 0.34, 0.22)
    if helical_blend_alpha > 0.0 and degraded.shape[0] > 3:
        shifted_up = np.roll(degraded, -1, axis=0)
        shifted_down = np.roll(degraded, 1, axis=0)
        z_phase = np.linspace(0.0, 2.0 * np.pi, degraded.shape[0], dtype=np.float32)[:, None, None]
        x_phase = np.linspace(0.0, 2.0 * np.pi * pitch, degraded.shape[2], dtype=np.float32)[None, None, :]
        interpolation_mix = 0.5 + 0.5 * np.sin(z_phase + x_phase)
        helical_interp = interpolation_mix * shifted_up + (1.0 - interpolation_mix) * shifted_down
        degraded = degraded * (1.0 - helical_blend_alpha) + helical_interp * helical_blend_alpha

    artifact_amplitude = min((pitch - 1.0) * 7.5, 6.5)
    if artifact_amplitude > 0.0 and degraded.shape[0] > 3:
        z = np.linspace(0.0, 2.0 * np.pi, degraded.shape[0], dtype=np.float32)[:, None, None]
        x = np.linspace(0.0, 2.0 * np.pi * (0.55 + pitch * 0.25), degraded.shape[2], dtype=np.float32)[None, None, :]
        ripple = np.sin(z * (1.0 + pitch * 0.35) + x)
        z_gradient = np.gradient(degraded, axis=0).astype(np.float32, copy=False)
        body_mask = (degraded > -850.0).astype(np.float32)
        artifact = ripple * body_mask * np.tanh(z_gradient / 110.0) * artifact_amplitude
        degraded = degraded + artifact

    return degraded.astype(np.float32, copy=False), {
        "z_sigma_voxels": float(sigma_voxels),
        "artifact_amplitude_hu": float(artifact_amplitude),
        "helical_blend_alpha": float(helical_blend_alpha),
        "pitch_model": "helical_interpolation_blur_plus_windmill_artifact",
    }


def _resize_xy(volume: np.ndarray, scale: float, order: int = 1) -> np.ndarray:
    if abs(scale - 1.0) < 1e-6:
        return volume.astype(np.float32, copy=False)
    return zoom(volume, (1.0, scale, scale), order=order, mode="nearest").astype(np.float32, copy=False)


def _crop_or_pad_to_shape(volume: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    _, ny, nx = volume.shape
    target_y, target_x = target_shape

    if ny > target_y:
        start_y = (ny - target_y) // 2
        volume = volume[:, start_y:start_y + target_y, :]
    elif ny < target_y:
        pad_before = (target_y - ny) // 2
        pad_after = target_y - ny - pad_before
        volume = np.pad(volume, ((0, 0), (pad_before, pad_after), (0, 0)), mode="constant", constant_values=-1000.0)

    if nx > target_x:
        start_x = (nx - target_x) // 2
        volume = volume[:, :, start_x:start_x + target_x]
    elif nx < target_x:
        pad_before = (target_x - nx) // 2
        pad_after = target_x - nx - pad_before
        volume = np.pad(volume, ((0, 0), (0, 0), (pad_before, pad_after)), mode="constant", constant_values=-1000.0)

    return volume.astype(np.float32, copy=False)


def _apply_fov_effect(
    volume: np.ndarray,
    spacing: Spacing3D,
    fov_mm: float,
    warnings: list[str],
    label_volume: Optional[np.ndarray] = None,
    support_mask: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, Optional[np.ndarray], Optional[np.ndarray], Spacing3D, Dict[str, Any]]:
    ny, nx = volume.shape[1], volume.shape[2]
    current_fov_y = float(spacing[1] * ny)
    current_fov_x = float(spacing[2] * nx)
    reference_fov = max(current_fov_y, current_fov_x)

    if abs(fov_mm - reference_fov) < 1.0:
        return volume, label_volume, support_mask, spacing, {
            "mode": "identity",
            "reference_fov_mm": float(reference_fov),
        }

    zoom_factor = reference_fov / max(fov_mm, 1.0)
    radial = _normalized_radial_map(volume.shape)
    if zoom_factor > 1.0:
        scaled = _resize_xy(volume, zoom_factor, order=1)
        result = _crop_or_pad_to_shape(scaled, (ny, nx))
        min_body_fraction = min(1.0 / zoom_factor, 1.0)
        if min_body_fraction < 0.45:
            warnings.append(
                f"Requested fov_mm={fov_mm} is small relative to current FOV ~{reference_fov:.1f} mm; anatomy may be cropped."
            )
        mode = "crop_and_resize"
    else:
        scaled = _resize_xy(volume, zoom_factor, order=1)
        result = _crop_or_pad_to_shape(scaled, (ny, nx))
        mode = "pad_and_resize"

    fov_ratio = float(fov_mm / max(reference_fov, 1.0))
    if fov_ratio < 1.0:
        cupping_strength = min((1.0 - fov_ratio) * 28.0, 18.0)
        peripheral_boost = np.power(np.clip(radial, 0.0, 1.0), 1.8).astype(np.float32)
        body_mask = (result > -850.0).astype(np.float32)
        result = result - body_mask * (1.0 - peripheral_boost) * cupping_strength

        truncation_strength = min((1.0 - fov_ratio) * 14.0, 10.0)
        edge_band = np.clip((radial - 0.72) / 0.28, 0.0, 1.0)
        result = result + body_mask * edge_band * truncation_strength
    else:
        cupping_strength = 0.0
        truncation_strength = 0.0

    label_result = label_volume
    if label_volume is not None:
        scaled_labels = _resize_xy(label_volume.astype(np.float32, copy=False), zoom_factor, order=0)
        label_result = _crop_or_pad_to_shape(scaled_labels, (ny, nx)).astype(np.uint8, copy=False)

    support_result = support_mask
    if support_mask is not None:
        scaled_support = _resize_xy(support_mask.astype(np.float32, copy=False), zoom_factor, order=0)
        support_result = _crop_or_pad_to_shape(scaled_support, (ny, nx)) > 0.5

    new_spacing = (
        float(spacing[0]),
        float(fov_mm / ny),
        float(fov_mm / nx),
    )
    return result.astype(np.float32, copy=False), label_result, support_result, new_spacing, {
        "mode": mode,
        "reference_fov_mm": float(reference_fov),
        "applied_zoom_factor": float(zoom_factor),
        "cupping_strength_hu": float(cupping_strength),
        "truncation_edge_boost_hu": float(truncation_strength),
    }


def _apply_matrix_effect(
    volume: np.ndarray,
    matrix_size: int,
) -> tuple[np.ndarray, Dict[str, Any]]:
    if matrix_size > 512:
        # Higher display matrix at fixed FOV should look slightly crisper and less pixel-limited,
        # even though this preview cannot invent true detector-domain information.
        upscale_factor = matrix_size / 512.0
        base_sigma = max((upscale_factor - 1.0) * 0.12, 0.0)
        denoised = gaussian_filter(volume, sigma=(0.0, base_sigma, base_sigma), mode="nearest")
        detail = volume - gaussian_filter(volume, sigma=(0.0, 0.4, 0.4), mode="nearest")
        enhanced = denoised + min((upscale_factor - 1.0) * 0.18, 0.16) * detail
        enhanced = _clipped_float32(enhanced)
        return enhanced, {
            "downsample_factor": 1.0,
            "effective_matrix_size": int(matrix_size),
            "matrix_model": "high_matrix_edge_enhancement",
            "upscale_factor_reference": float(upscale_factor),
            "edge_boost_amount": float(min((upscale_factor - 1.0) * 0.18, 0.16)),
        }

    if matrix_size == 512:
        return volume, {
            "downsample_factor": 1.0,
            "effective_matrix_size": int(matrix_size),
            "matrix_model": "reference_512_identity",
        }

    scale = matrix_size / 512.0
    prefiltered = gaussian_filter(
        volume,
        sigma=(0.0, max((1.0 / max(scale, 1e-6) - 1.0) * 0.22, 0.0), max((1.0 / max(scale, 1e-6) - 1.0) * 0.22, 0.0)),
        mode="nearest",
    )
    down = _resize_xy(prefiltered, scale, order=1)
    restored = zoom(
        down,
        (1.0, volume.shape[1] / max(down.shape[1], 1), volume.shape[2] / max(down.shape[2], 1)),
        order=1,
        mode="nearest",
    )
    restored = _crop_or_pad_to_shape(restored.astype(np.float32, copy=False), (volume.shape[1], volume.shape[2]))
    edge_loss_sigma = max((1.0 / max(scale, 1e-6) - 1.0) * 0.12, 0.0)
    if edge_loss_sigma > 1e-6:
        restored = gaussian_filter(restored, sigma=(0.0, edge_loss_sigma, edge_loss_sigma), mode="nearest")
    return restored, {
        "downsample_factor": float(scale),
        "effective_matrix_size": int(matrix_size),
        "matrix_model": "downsample_restore_with_antialias",
    }


def _apply_kernel_effect(
    volume: np.ndarray,
    kernel: str,
) -> tuple[np.ndarray, Dict[str, Any]]:
    radial = _normalized_radial_map(volume.shape)
    body_mask = (volume > -850.0).astype(np.float32)
    ring_phase = np.sin(radial * 34.0 + np.linspace(0.0, 2.0 * np.pi, volume.shape[0], dtype=np.float32)[:, None, None])

    if kernel in ("smooth", "soft"):
        sigma = 1.1 if kernel == "smooth" else 0.8
        filtered = gaussian_filter(volume, sigma=(0.0, sigma, sigma), mode="nearest")
        ring_artifact = body_mask * ring_phase * (1.4 if kernel == "smooth" else 0.8)
        filtered = filtered + ring_artifact
        return filtered.astype(np.float32, copy=False), {
            "mode": "gaussian_blur_plus_ring_artifact",
            "sigma_xy": float(sigma),
            "ring_artifact_hu": float(1.4 if kernel == "smooth" else 0.8),
            "kernel_family": "soft_tissue",
        }

    if kernel == "standard":
        filtered = gaussian_filter(volume, sigma=(0.0, 0.45, 0.45), mode="nearest")
        ring_artifact = body_mask * ring_phase * 0.4
        filtered = filtered + ring_artifact
        return filtered.astype(np.float32, copy=False), {
            "mode": "mild_blur_plus_faint_ring_artifact",
            "sigma_xy": 0.45,
            "ring_artifact_hu": 0.4,
            "kernel_family": "balanced",
        }

    if kernel == "lung":
        blurred = gaussian_filter(volume, sigma=(0.0, 0.5, 0.5), mode="nearest")
        sharpened = volume + 0.55 * (volume - blurred)
        high_freq = volume - gaussian_filter(volume, sigma=(0.0, 0.25, 0.25), mode="nearest")
        sharpened = sharpened + 0.08 * high_freq
        granular_noise = gaussian_filter(
            np.random.default_rng(17).normal(0.0, 1.0, size=volume.shape).astype(np.float32),
            sigma=(0.0, 0.35, 0.35),
            mode="nearest",
        )
        sharpened = sharpened + body_mask * granular_noise * 2.4
        return _clipped_float32(sharpened), {
            "mode": "unsharp_mask_plus_high_frequency_texture",
            "amount": 0.55,
            "pre_blur_sigma_xy": 0.5,
            "texture_noise_hu": 2.4,
            "kernel_family": "lung",
        }

    if kernel in ("bone", "sharp"):
        blurred = gaussian_filter(volume, sigma=(0.0, 0.7, 0.7), mode="nearest")
        sharpened = volume + 0.9 * (volume - blurred)
        if kernel == "sharp":
            sharpened = sharpened - 0.08 * laplace(blurred, mode="nearest")
        edge_mask = np.clip((radial - 0.64) / 0.36, 0.0, 1.0) * body_mask
        ring_boost = ring_phase * edge_mask * (1.8 if kernel == "sharp" else 1.2)
        sharpened = sharpened + ring_boost
        return _clipped_float32(sharpened), {
            "mode": "strong_unsharp_mask_plus_edge_ringing",
            "amount": 0.9,
            "pre_blur_sigma_xy": 0.7,
            "laplacian_boost": bool(kernel == "sharp"),
            "edge_ringing_hu": float(1.8 if kernel == "sharp" else 1.2),
            "kernel_family": "high_resolution",
        }

    return volume, {"mode": "identity", "kernel_family": "unknown"}


def _enhance_mask_region(
    volume: np.ndarray,
    mask: np.ndarray,
    delta_hu: float,
    *,
    smooth_sigma: float = 0.8,
    blur_sigma: float = 0.6,
) -> np.ndarray:
    if not np.any(mask):
        return volume
    result = volume.copy()
    result[mask] += delta_hu
    softened = gaussian_filter(
        mask.astype(np.float32),
        sigma=(smooth_sigma, smooth_sigma, smooth_sigma),
        mode="nearest",
    ) > 0.05
    result[softened] = 0.7 * result[softened] + 0.3 * gaussian_filter(
        result,
        sigma=blur_sigma,
        mode="nearest",
    )[softened]
    return result.astype(np.float32, copy=False)


def _apply_contrast_phase(
    volume: np.ndarray,
    contrast_phase: str,
    label_volume: Optional[np.ndarray],
) -> tuple[np.ndarray, Dict[str, Any]]:
    if contrast_phase == "noncontrast":
        return volume, {"mode": "identity", "used_label_volume": bool(label_volume is not None)}

    result = volume.copy()
    baseline = volume.copy()
    phase_deltas = {
        "arterial": {
            9: 14.0,   # liver
            7: 32.0,   # left kidney
            8: 32.0,   # right kidney
            17: 18.0,  # spleen
            15: 10.0,  # pancreas
            19: 42.0,  # trachea/major airway region proxy kept low relevance
            "generic_soft": 7.0,
            "generic_vascular": 30.0,
        },
        "venous": {
            9: 26.0,
            7: 20.0,
            8: 20.0,
            17: 22.0,
            15: 14.0,
            19: 18.0,
            "generic_soft": 12.0,
            "generic_vascular": 18.0,
        },
        "delayed": {
            9: 12.0,
            7: 10.0,
            8: 10.0,
            17: 8.0,
            15: 6.0,
            19: 8.0,
            "generic_soft": 5.0,
            "generic_vascular": 8.0,
        },
    }[contrast_phase]

    label_based = False
    if label_volume is not None and label_volume.shape == volume.shape:
        for label_id in (9, 7, 8, 17, 15, 19):
            label_mask = label_volume == label_id
            if np.any(label_mask):
                sigma = 0.55 if label_id in (7, 8, 19) else 0.9
                blur_sigma = 0.45 if label_id in (7, 8, 19) else 0.7
                result = _enhance_mask_region(
                    result,
                    label_mask,
                    phase_deltas[label_id],
                    smooth_sigma=sigma,
                    blur_sigma=blur_sigma,
                )
                label_based = True

    if not label_based:
        soft_tissue_mask = (volume > 20.0) & (volume < 120.0)
        vascular_mask = (volume >= 120.0) & (volume < 260.0)
        organ_rich_mask = (volume >= 45.0) & (volume < 95.0)
        result[soft_tissue_mask] += phase_deltas["generic_soft"]
        result[organ_rich_mask] += phase_deltas["generic_soft"] * 0.35
        result[vascular_mask] += phase_deltas["generic_vascular"]

    vascular_emphasis = {
        "arterial": 1.0,
        "venous": 0.7,
        "delayed": 0.35,
    }[contrast_phase]
    washout_strength = {
        "arterial": 0.12,
        "venous": 0.18,
        "delayed": 0.28,
    }[contrast_phase]

    vascular_component = np.clip(baseline - 110.0, 0.0, 220.0) / 220.0
    organ_component = np.clip(baseline - 35.0, 0.0, 95.0) / 95.0
    delayed_component = gaussian_filter(organ_component.astype(np.float32), sigma=(0.7, 1.0, 1.0), mode="nearest")

    result += vascular_component.astype(np.float32) * 18.0 * vascular_emphasis
    if contrast_phase == "delayed":
        result += delayed_component.astype(np.float32) * 9.0
        result -= vascular_component.astype(np.float32) * 8.0
    elif contrast_phase == "venous":
        result += delayed_component.astype(np.float32) * 3.0

    # Approximate venous/delayed washout in strongly enhanced voxels.
    enhancement = np.clip(result - baseline, 0.0, None)
    result -= enhancement * washout_strength * np.clip(vascular_component + 0.2, 0.0, 1.2)

    # Mild phase-specific smoothing prevents enhancement from looking like simple voxel-wise brightening.
    phase_blend_sigma = {
        "arterial": 0.35,
        "venous": 0.45,
        "delayed": 0.55,
    }[contrast_phase]
    smoothed = gaussian_filter(result, sigma=(0.0, phase_blend_sigma, phase_blend_sigma), mode="nearest")
    result = 0.82 * result + 0.18 * smoothed

    return result.astype(np.float32, copy=False), {
        "mode": "empirical_hu_boost",
        "used_label_volume": bool(label_based),
        "phase": contrast_phase,
        "vascular_emphasis": float(vascular_emphasis),
        "washout_strength": float(washout_strength),
        "phase_model": "organ_weighted_empirical_enhancement_with_washin_washout",
    }


def simulate_ct_scan_params(
    volume: np.ndarray,
    spacing: Spacing3D,
    params: Dict[str, Any],
    label_volume: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Simulate CT scan parameter effects on a HU volume.

    Returns a dictionary with simulated volume, metadata, params_json,
    and lightweight preview statistics.
    """
    if volume.ndim != 3:
        raise ValueError(f"volume must be 3-D (z, y, x), got shape={volume.shape}")
    if label_volume is not None and label_volume.shape != volume.shape:
        raise ValueError(
            f"label_volume shape {label_volume.shape} does not match volume shape {volume.shape}"
        )

    working = volume.astype(np.float32, copy=True)
    working_labels = label_volume.astype(np.uint8, copy=True) if label_volume is not None else None
    working_support_mask = _extract_body_support_mask(working, working_labels)
    input_spacing = (float(spacing[0]), float(spacing[1]), float(spacing[2]))
    warnings: list[str] = []
    resolved_params = _resolve_params(params)
    algorithm_steps: list[Dict[str, Any]] = []

    rng_seed = int(
        resolved_params["mAs"] * 17
        + resolved_params["kVp"] * 13
        + int(resolved_params["slice_thickness_mm"] * 100)
        + int(resolved_params["pitch"] * 100)
    )
    rng = np.random.default_rng(rng_seed)

    hu_before = [float(np.min(working)), float(np.max(working))]
    preview_before = _center_slice_stats(working)

    working, working_labels, working_support_mask, step_meta = _apply_gantry_pose(
        working,
        input_spacing,
        resolved_params["gantry_pitch_deg"],
        resolved_params["gantry_yaw_deg"],
        resolved_params["gantry_roll_deg"],
        label_volume=working_labels,
        support_mask=working_support_mask,
    )
    algorithm_steps.append({"name": "gantry_pose_resampling", **step_meta})

    working, step_meta = _apply_slice_thickness(working, input_spacing, resolved_params["slice_thickness_mm"])
    algorithm_steps.append({"name": "slice_thickness", **step_meta})

    working, step_meta = _apply_dose_noise(working, resolved_params, rng)
    algorithm_steps.append({"name": "dose_noise", **step_meta})

    working, step_meta = _apply_kvp_transform(working, resolved_params["kVp"])
    algorithm_steps.append({"name": "kvp_transform", **step_meta})

    working, step_meta = _apply_pitch_effect(working, resolved_params["pitch"])
    algorithm_steps.append({"name": "pitch_degradation", **step_meta})

    working, working_labels, working_support_mask, output_spacing, step_meta = _apply_fov_effect(
        working,
        input_spacing,
        resolved_params["fov_mm"],
        warnings,
        label_volume=working_labels,
        support_mask=working_support_mask,
    )
    algorithm_steps.append({"name": "fov_adjustment", **step_meta})

    working, step_meta = _apply_matrix_effect(working, resolved_params["matrix_size"])
    algorithm_steps.append({"name": "matrix_resolution", **step_meta})

    working, step_meta = _apply_kernel_effect(working, resolved_params["kernel"])
    algorithm_steps.append({"name": "reconstruction_kernel", **step_meta})

    working, step_meta = _apply_contrast_phase(working, resolved_params["contrast_phase"], working_labels)
    algorithm_steps.append({"name": "contrast_phase", **step_meta})

    if working_support_mask is not None:
        # Keep voxels outside the rotated/padded patient support region as true air.
        # Without this, later noise/filter stages can make the expanded rotation box visible.
        working = np.where(working_support_mask, working, AIR_HU)

    working = _clipped_float32(working)

    hu_after = [float(np.min(working)), float(np.max(working))]
    preview_after = _center_slice_stats(working)

    approximation_notes = [
        "Image-domain approximation only; not a physics-based CT forward model.",
        "Gantry pitch/yaw/roll use spacing-aware 3D affine resampling in image space.",
        "Dose and mAs use projection-inspired Poisson/log-count noise plus detector noise, not full reconstruction.",
        "kVp effect uses material remapping plus empirical beam-hardening response, not spectrum-resolved attenuation.",
        "Pitch, slice thickness, FOV, and matrix effects are approximated with helical interpolation, cupping/truncation effects, resampling, and smoothing.",
        "1024 matrix uses a mild high-resolution edge model, not true detector-domain super-resolution.",
        "Kernel and contrast-phase behavior remain empirical, with added texture/ringing and wash-in/washout trends.",
    ]

    standardized_output_notes = [
        "axis_order = zyx",
        "dtype = float32",
        "spacing order = z,y,x",
        "volume data is stored in top-level simulated_volume_base64",
        "standardized_case is intended for downstream artifact/lesion modules",
    ]

    metadata = {
        "shape": [int(v) for v in working.shape],
        "spacing": [float(v) for v in output_spacing],
        "hu_range": hu_after,
        "gantry_pitch_deg": float(resolved_params["gantry_pitch_deg"]),
        "gantry_yaw_deg": float(resolved_params["gantry_yaw_deg"]),
        "gantry_roll_deg": float(resolved_params["gantry_roll_deg"]),
        "gantry_tilt_deg": float(resolved_params["gantry_pitch_deg"]),
        "effective_slice_thickness_mm": float(resolved_params["slice_thickness_mm"]),
        "algorithm_notes": approximation_notes,
        "warnings": warnings,
        "notes": standardized_output_notes,
    }

    params_json = {
        "requested_params": params,
        "resolved_params": resolved_params,
        "algorithm_steps": algorithm_steps,
        "approximation_notes": approximation_notes,
        "warnings": warnings,
        "notes": standardized_output_notes,
        "input_shape": [int(v) for v in volume.shape],
        "output_shape": [int(v) for v in working.shape],
        "input_spacing": [float(v) for v in input_spacing],
        "output_spacing": [float(v) for v in output_spacing],
        "hu_range_before": hu_before,
        "hu_range_after": hu_after,
    }

    return {
        "simulated_volume": working,
        "simulated_label_volume": working_labels,
        "simulated_spacing": output_spacing,
        "params_json": params_json,
        "metadata": metadata,
        "preview_stats": {
            "original_center_slice_stats": preview_before,
            "simulated_center_slice_stats": preview_after,
        },
    }

