# 《MedSim_Studio CT Simulation -> CT Artifact 接口规范 v1.1》
文档日期：2026-06-28

## 1. 目的与范围
本文档用于 `MedSim_Studio / ct-simulation` 与 `ct-artifact` 组之间的接口对接，内容严格基于当前代码实现整理，仅描述已存在或已在代码中明确体现的能力。

当前推荐采用双接口策略：

1. 如果 B 组需要原始 atlas CT / organ label，使用 `GET /api/v1/simulation/phantom`。
2. 如果 B 组需要经过 CT 扫描参数仿真的 CT 体数据，使用 `POST /api/v1/simulation/ct-params/preview`。

明确约定：

- `/phantom` 是原始 phantom / atlas CT 数据入口。
- `/ct-params/preview` 是参数仿真后的 simulated CT 输出入口。
- 当前 `ct-params/preview` 是 image-domain approximation，不是真实 scanner physics simulation，不用于医学诊断。

## 2. 代码核对范围与结论
本文档依据以下文件核对：

- `docs/interface_ct_simulation_to_artifact.md`
- `backend/app/api/v1/simulation.py`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/types/simulation.ts`
- `frontend/src/services/simulationService.ts`

关键结论：

- 后端真实路由前缀为 `/api/v1`。
- 原有接口 `GET /api/v1/simulation/phantom` 仍保留。
- 新接口 `POST /api/v1/simulation/ct-params/preview` 已实现。
- `ct-params/preview` 当前支持 `source="atlas"` 与 `source="procedural"`；传入其他 source 会返回 400。
- 前端 `/simulation` 页面已支持 CT Scan Params Simulation 参数面板、Original vs Simulated 双切片对比、`params_json` 复制/下载、`standardized_case` 复制/下载。
- 联调文档必须以后端原始 HTTP JSON 字段为准，即 snake_case 字段名为准。

## 3. 原始 Phantom / Atlas 接口

### 3.1 Method / URL
`GET /api/v1/simulation/phantom`

### 3.2 用途
用于返回原始 phantom 数据：

- `source=atlas` 时，返回原始 atlas CT 与可选 organ label。
- `source!=atlas` 时，当前代码会落入 procedural phantom 分支。

补充说明：

- 该接口输出的是原始 phantom / atlas CT。
- 若需要模拟低剂量、层厚、kVp、pitch、FOV、matrix、重建核、contrast phase 等，应使用 `POST /api/v1/simulation/ct-params/preview`。

### 3.3 Query Parameters

| 参数 | 类型 | 当前实现 | 说明 |
|---|---|---|---|
| `source` | string | 是 | 推荐值：`atlas` 或 `procedural` |
| `case_id` | string | 是 | atlas case id，例如 `s0001` |
| `size` | int | 是 | 当前代码限制 `64 <= size <= 320` |
| `scan_direction` | string | 是 | atlas 模式支持 `head_to_feet`、`feet_to_head` |

补充说明：

- `case_id` 仅在 `source=atlas` 时生效。
- `scan_direction` 仅在 atlas 模式下做方向处理。
- 当前 B 组常用 atlas 调用参数：`source=atlas&case_id=s0001&size=160&scan_direction=head_to_feet`。

### 3.4 响应示例
```json
{
  "volume_base64": "<base64 raw float32 bytes>",
  "label_base64": "<base64 raw uint8 bytes or null>",
  "metadata": {
    "width": 160,
    "height": 144,
    "depth": 128,
    "spacing": [1.5, 1.2, 1.2],
    "source": "atlas",
    "case_id": "s0001",
    "scan_axis": "z",
    "scan_direction": "head_to_feet",
    "flipped_z": false,
    "window_presets": {
      "soft": { "windowLevel": 40.0, "windowWidth": 400.0 },
      "lung": { "windowLevel": -600.0, "windowWidth": 1500.0 },
      "bone": { "windowLevel": 500.0, "windowWidth": 2000.0 }
    },
    "body_threshold_hu": -500.0,
    "label_map": {
      "0": "background",
      "1": "left_adrenal_gland",
      "2": "right_adrenal_gland"
    },
    "slice_label_presence": {
      "lung": [10, 52],
      "liver": [60, 95]
    },
    "label_nonzero_counts": {
      "9": 123456
    }
  }
}
```

### 3.5 响应字段说明

- `volume_base64`
  - 原始 CT volume 的 base64 raw bytes。
  - 对应 little-endian `float32`。
- `label_base64`
  - atlas label 存在时返回原始 `uint8` raw bytes。
  - 若无 label 文件，则返回 `null`。
- `metadata`
  - 当前 atlas / phantom 的元数据。
  - shape 可按 `shape = [depth, height, width]` 理解。

### 3.6 metadata 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `width` | int | x 轴尺寸 |
| `height` | int | y 轴尺寸 |
| `depth` | int | z 轴尺寸 |
| `spacing` | number[3] | 顺序为 `(z, y, x)` |
| `source` | string | `atlas` 或 `procedural` |
| `case_id` | string | atlas case id |
| `scan_axis` | string | 当前 atlas 固定为 `z` |
| `scan_direction` | string | `head_to_feet` 或 `feet_to_head` |
| `flipped_z` | bool | 是否为匹配扫描方向而做 z 翻转 |
| `window_presets` | object | 当前包含 `soft` / `lung` / `bone` |
| `body_threshold_hu` | number | 当前代码中为 `-500.0` |
| `label_map` | object | atlas label id 到器官名映射 |
| `slice_label_presence` | object | 部分器官 z 范围统计 |
| `label_nonzero_counts` | object | atlas label 非零体素计数 |
| `original_shape` | int[3] | atlas 原始 shape，轴序 `(z, y, x)` |
| `output_shape` | int[3] | 输出 shape，轴序 `(z, y, x)` |
| `original_spacing` | number[3] | atlas 原始 spacing，轴序 `(z, y, x)` |
| `output_spacing` | number[3] | 输出 spacing，轴序 `(z, y, x)` |
| `nifti_rz` | number | NIfTI z 向调试字段 |

### 3.7 label_map（当前 atlas 20 类）

| ID | Name |
|---|---|
| 0 | background |
| 1 | left_adrenal_gland |
| 2 | right_adrenal_gland |
| 3 | colon |
| 4 | duodenum |
| 5 | esophagus |
| 6 | gallbladder |
| 7 | left_kidney |
| 8 | right_kidney |
| 9 | liver |
| 10 | left_lung_lower_lobe |
| 11 | right_lung_lower_lobe |
| 12 | right_lung_middle_lobe |
| 13 | left_lung_upper_lobe |
| 14 | right_lung_upper_lobe |
| 15 | pancreas |
| 16 | small_bowel |
| 17 | spleen |
| 18 | stomach |
| 19 | trachea |
| 20 | urinary_bladder |

### 3.8 数据约定

| 项目 | 约定 |
|---|---|
| axis order | `(z, y, x)` |
| CT dtype | `float32` |
| endian | little-endian |
| label dtype | `uint8` |
| label background | `0 = background` |
| spacing order | `(z, y, x)` |
| base64 standard | 标准 RFC 4648 base64 |
| error format | 业务错误通常为 `{"detail": "..."}` |

补充说明：

- `volume_base64` 对应原始连续字节流，不是 `.nii.gz` 文件内容。
- `label_base64` 同样是原始 `uint8` 数组字节流，不是压缩文件。
- 当前后端内部统一采用 `(z, y, x)` 约定。

### 3.9 /phantom 解码示例

#### 解码 CT volume
```python
import base64
import numpy as np

volume = np.frombuffer(
    base64.b64decode(volume_base64),
    dtype=np.float32,
).reshape((depth, height, width))
```

#### 解码 organ label
```python
import base64
import numpy as np

if label_base64 is not None:
    label = np.frombuffer(
        base64.b64decode(label_base64),
        dtype=np.uint8,
    ).reshape((depth, height, width))
else:
    label = None
```

## 4. CT 参数仿真接口：POST /api/v1/simulation/ct-params/preview

### 4.1 Method / URL
`POST /api/v1/simulation/ct-params/preview`

### 4.2 用途
基于 atlas CT 生成参数仿真后的 simulated CT volume，用于演示：

- 层厚变化
- 剂量 / mAs 噪声变化
- kVp 对比变化
- pitch 造成的 z 向退化
- FOV 改变
- matrix 分辨率变化
- reconstruction kernel 平滑 / 锐化
- contrast phase 经验增强

明确说明：

- 当前接口面向 preview / 演示用途。
- 当前支持 `source="atlas"` 与 `source="procedural"`。
- 当前算法属于 image-domain approximation，不代表真实 CT scanner physics，不用于医学诊断。

### 4.3 请求示例
```json
{
  "source": "procedural",
  "case_id": null,
  "size": 160,
  "scan_direction": "head_to_feet",
  "params": {
    "slice_thickness_mm": 5.0,
    "dose_level": "low",
    "mAs": 50,
    "kVp": 120,
    "pitch": 1.2,
    "fov_mm": 250,
    "matrix_size": 256,
    "kernel": "bone",
    "contrast_phase": "venous"
  }
}
```

### 4.4 参数表

| 字段 | 类型 | 当前实现 | 说明 |
|---|---|---|---|
| `source` | string | 是 | 当前实际仅支持 `atlas`；其他值会返回 400 |
| `case_id` | string | 是 | atlas case id，例如 `s0001` |
| `size` | int | 是 | 输出体数据目标尺寸；当前请求模型限制 `64 <= size <= 192` |
| `scan_direction` | string | 是 | `head_to_feet` / `feet_to_head` |
| `params.slice_thickness_mm` | number | 是 | `0.625 / 1.0 / 2.5 / 5.0 / 10.0` |
| `params.dose_level` | string | 是 | `low / standard / high` |
| `params.mAs` | int | 是 | `30-300` |
| `params.kVp` | int | 是 | `80 / 100 / 120 / 140` |
| `params.pitch` | number | 是 | `0.5 / 0.8 / 1.0 / 1.2 / 1.5` |
| `params.fov_mm` | int | 是 | `150 / 250 / 350 / 500` |
| `params.matrix_size` | int | 是 | `256 / 512 / 1024` |
| `params.kernel` | string | 是 | `smooth / soft / standard / lung / bone / sharp` |
| `params.contrast_phase` | string | 是 | `noncontrast / arterial / venous / delayed` |

### 4.5 响应示例
```json
{
  "simulated_volume_base64": "<base64 raw float32 bytes>",
  "metadata": {
    "shape": [128, 144, 160],
    "spacing": [1.5, 1.7361111111, 1.5625],
    "hu_range": [-1024.0, 1705.2],
    "effective_slice_thickness_mm": 5.0,
    "warnings": [],
    "algorithm_notes": ["..."],
    "preview_stats": {
      "original_center_slice_stats": {"slice_index": 64, "min": -1000.0, "max": 1120.0, "mean": -235.0, "std": 380.0},
      "simulated_center_slice_stats": {"slice_index": 64, "min": -1024.0, "max": 1450.0, "mean": -210.0, "std": 340.0}
    }
  },
  "params_json": {
    "requested_params": {"...": "..."},
    "resolved_params": {"...": "..."},
    "algorithm_steps": [{"name": "slice_thickness"}],
    "approximation_notes": ["..."],
    "warnings": [],
    "input_shape": [128, 144, 160],
    "output_shape": [128, 144, 160],
    "input_spacing": [1.5, 1.2, 1.2],
    "output_spacing": [1.5, 1.7361111111, 1.5625],
    "hu_range_before": [-1000.0, 1600.0],
    "hu_range_after": [-1024.0, 1705.2]
  },
  "standardized_case": {
    "...": "..."
  }
}
```

### 4.6 响应字段说明

- `simulated_volume_base64`
  - base64 raw bytes。
  - dtype 为 `float32`。
  - byte order 为 little-endian。
  - axis_order 为 `zyx`。
  - reshape shape 来自 `standardized_case.volume.shape` 或 `metadata.shape`。
- `metadata`
  - 当前包含 `shape`、`spacing`、`hu_range`、`effective_slice_thickness_mm`、`warnings`、`algorithm_notes`、`preview_stats` 等字段。
  - 还会带上 `source`、`case_id`、`scan_direction`、`phantom_metadata`、`notes`。
- `params_json`
  - 当前包含 `requested_params`、`resolved_params`、`algorithm_steps`、`approximation_notes`、`warnings`、`input_shape`、`output_shape`、`input_spacing`、`output_spacing`、`hu_range_before`、`hu_range_after`、`notes`。
- `standardized_case`
  - 为下游模块推荐优先读取的统一元数据结构。

## 5. standardized_case 统一输出结构

### 5.1 结构示例
```json
{
  "case_id": "sim_atlas_s0001_YYYYMMDD_HHMMSS",
  "source": "procedural",
  "source_case_id": "procedural",
  "volume": {
    "encoding": "base64",
    "dtype": "float32",
    "byte_order": "little_endian",
    "axis_order": "zyx",
    "shape": [128, 144, 160],
    "spacing": [1.5, 1.7361111111, 1.5625],
    "origin": [0.0, 0.0, 0.0],
    "direction": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    "hu_range": [-1024.0, 1705.2],
    "slice_count": 128,
    "modality": "CT",
    "body_part": "upper_body",
    "image_kind": "simulated_ct",
    "image_data_field": "simulated_volume_base64"
  },
  "simulation": {
    "type": "ct_scan_params",
    "params_json": {},
    "algorithm": "image_domain_approximation",
    "approximation_warning": "This is an educational image-domain approximation, not a scanner-physics simulation."
  },
  "metadata": {}
}
```

### 5.2 结构说明

- `standardized_case` 不直接包含 `simulated_volume_base64`。
- 实际体数据位于顶层字段 `simulated_volume_base64`。
- `standardized_case.volume.image_data_field = "simulated_volume_base64"` 用于指向顶层大体数据字段。
- 这样可以避免重复传输大体数据。
- `volume.shape`、`volume.spacing`、`volume.axis_order`、`volume.dtype` 为下游解码的核心字段。
- `simulation.params_json` 为参数与算法记录的内嵌引用内容。
- 当前 preview response 中 `origin` 使用默认 `[0.0, 0.0, 0.0]`。
- 当前 preview response 中 `direction` 使用单位矩阵。
- 文档必须明确：当前 preview response 未传播 atlas 原始 `origin` / `direction`。

## 6. B 组解码 simulated_volume_base64 示例

```python
import base64
import numpy as np

shape = tuple(response["standardized_case"]["volume"]["shape"])
volume = np.frombuffer(
    base64.b64decode(response["simulated_volume_base64"]),
    dtype="<f4",
).reshape(shape)

spacing = response["standardized_case"]["volume"]["spacing"]
params_json = response["params_json"]
```

说明：

- `dtype="<f4"` 表示 little-endian `float32`。
- `shape` 轴序是 `z, y, x`。
- `spacing` 顺序是 `z, y, x`。

## 7. CT 参数近似算法说明
当前 `backend/app/simulation/ct_params_simulator.py` 中的参数仿真逻辑为 image-domain approximation，逐项如下：

1. `slice_thickness_mm`：使用 z 向高斯平滑做厚层近似。
2. `dose_level / mAs`：使用 Gaussian noise 近似剂量噪声，低剂量噪声更强。
3. `kVp`：使用 HU 对比度近似映射，不是基于能谱衰减的真实物理模型。
4. `pitch`：使用 z 向平滑做细节退化近似。
5. `fov_mm`：通过中心 crop / padding + resize 近似 FOV 变化。
6. `matrix_size`：通过降采样再插值回原大小近似分辨率变化。
7. `kernel`：通过 gaussian blur / unsharp mask / laplacian boost 近似 smooth、soft、standard、lung、bone、sharp 重建核效果。
8. `contrast_phase`：通过经验增强实现 `noncontrast / arterial / venous / delayed`，在有 atlas label 时优先利用 coarse organ labels。

必须强调：

- 以上全部为 image-domain approximation。
- 不代表真实 CT scanner physics。
- 不应用于医学诊断、定量测量或真实扫描器性能评估。

## 8. B 组对接推荐方式

### 8.1 场景 A：B 组只需要原始 atlas CT
调用：
```http
GET /api/v1/simulation/phantom?source=atlas&case_id=s0001&size=160&scan_direction=head_to_feet
```

读取：

- `volume_base64`
- `label_base64`
- `metadata`

### 8.2 场景 B：B 组需要 CT 参数仿真后的 simulated CT
调用：
```http
POST /api/v1/simulation/ct-params/preview
```

补充：

- 如果需要真实 atlas CT，用 `source=atlas`。
- 如果只需要演示级合成 CT，可用 `source=procedural`。

读取：

- `standardized_case`：统一结构和元数据
- `simulated_volume_base64`：真实体数据
- `params_json`：仿真参数和算法记录

## 9. 前端验证方式

1. 打开 `/simulation`。
2. 进入 `CT Phantom` tab。
3. 选择 `Real CT Atlas` 或 `Procedural Phantom`。
4. 点击对应的 phantom 生成按钮。
5. 设置 `CT Scan Params Simulation` 参数。
6. 点击 `Run CT Parameter Simulation`。
7. 查看 `Original vs Simulated` 双切片对比。
8. 使用 `Copy / Download Params JSON`。
9. 使用 `Copy / Download Standardized Case JSON`。

## 10. 需求完成度对照

| 需求 | 当前状态 | 说明 |
|---|---|---|
| D30 层厚模拟 | 已实现，演示级近似 | z 向平滑 |
| D31 剂量/mAs模拟 | 已实现，演示级近似 | mAs 控制噪声 |
| D32 kVp模拟 | 已实现，演示级近似 | HU 对比映射 |
| D33 Pitch模拟 | 已实现，演示级近似 | z 向退化 |
| D34 FOV模拟 | 已实现，演示级近似 | crop/padding + resize |
| D35 Matrix模拟 | 已实现，演示级近似 | 降采样再插值 |
| D36 重建核模拟 | 已实现，演示级近似 | 平滑/锐化 |
| D37 造影增强期相模拟 | 已实现，演示级近似 | 经验增强 |
| D38 原始vs仿真对比 | 已实现 | 前端双切片 |
| D39 低剂量仿真CT输出 | 已实现 | `simulated_volume_base64` |
| D40 参数JSON输出 | 已实现 | `params_json` + 前端下载 |
| D44 为B组提供可处理CT体数据 | 部分实现 | API 返回 base64 `float32` volume，未做文件包 |
| D45 为B组提供仿真CT数据 | 部分实现 | 同上 |
| D47 为B组提供参数信息 | 已实现 | `params_json` + `standardized_case` |
| D10 统一输出结构 | 部分实现 | `ct-params/preview` 已有 `standardized_case`，尚未覆盖所有模块 |

## 11. 当前未实现与后续计划
以下内容可以作为后续规划，但当前不能写成已实现：

- DICOM source 接入 `ct-params/preview`
- NRRD / NIfTI / DICOM 文件导出
- MinIO / 数据库登记
- B 组 artifact pipeline 返回展示
- simulated volume 3D rendering
- 更真实的物理仿真
- 完整异步 job / volume / labels / series 资源化接口

## 12. 错误格式与对接注意事项

### 12.1 当前错误格式
- 业务错误通常为 `{"detail": "错误描述"}`。
- `ct-params/preview` 中 `source` 若不是 `atlas` 或 `procedural`，当前会返回 400。
- 文件缺失时，通常返回 404。
- FastAPI 参数校验失败时，仍可能返回 422 且 `detail` 为数组结构。

### 12.2 对接注意事项
- B 组不要假设所有错误都统一为字符串型 `detail`。
- B 组解码体数据时，必须按 `(z, y, x)` 和 little-endian `float32` 解释 `simulated_volume_base64`。
- 若只需要原始 atlas CT 与器官标签，应优先使用 `/phantom`。
- 若需要参数仿真 CT，则应使用 `/ct-params/preview`。

## 13. 版本历史

### v1.1 - 2026-06-28
- 新增 CT 参数仿真 preview 接口说明。
- 新增 `standardized_case` 统一输出结构。
- 新增 `params_json` 参数记录说明。
- 新增 B 组解码 `simulated_volume_base64` 的说明。
- 更新 B 组双接口对接策略。
- 更新需求完成度对照。

### v1.0 - 2026-06-22
- 初版，基于当前 atlas / procedural phantom API 整理。
- 明确当前可联调 MVP 接口：`GET /api/v1/simulation/phantom`。
- 约定体数据、label、shape、spacing、axis order、base64 解码方式。
- 将 job 系列 `volume / labels / series` 设计保留为 planned extension。
