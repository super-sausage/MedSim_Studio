# 病灶仿真模块 Debug 诊断报告

## 问题定位报告

---

## 一、已完成的工作

### ✅ 诊断基础设施（7 项任务全部完成）

| 任务 | 状态 | 文件 |
|------|------|------|
| T1: 病灶生成日志 | ✅ | `generator.py` `_generate_lesion_volume()` |
| T2: 写入验证日志 | ✅ | `simulation.py` `_debug_log_lesion_write()` |
| T3: 位置验证日志 | ✅ | `simulation.py` `_debug_log_position()` |
| T4: Spacing换算日志 | ✅ | `simulation.py` `_debug_log_spacing()` |
| T5: PNG可视化输出 | ✅ | `simulation.py` `_debug_save_lesion_pngs()` |
| T6: 文件写出验证 | ✅ | `simulation.py` `_debug_verify_sitk_metadata()` |
| T7: Debug API | ✅ | `POST /api/v1/simulation/debug-lesion` |

---

## 二、已定位的 Bug

### 🐛 Bug #1（严重）：病灶写入方式错误 — 已修复

**文件**: `backend/app/api/v1/simulation.py`

**修改前**:
```python
result_volume = result_volume + lesion_vol
```

**修改后**:
```python
result_volume[lesion_mask] = lesion_vol[lesion_mask]
```

**根因分析**:

原始代码使用加法来"叠加"病灶，这是错误的。病灶生成器返回的 `lesion_vol` 是一个稀疏数组（只有病灶位置非零，其余为0）。使用加法会导致：

- 病灶区域的 HU 值被**错误叠加**到背景 HU 上
- 背景区域（0 HU）加上空气（-1000 HU）导致背景 HU 偏移
- 如果病灶 HU 均值为 40，叠加到 -1000 HU 的空气背景上，结果变成 -960 HU而不是 40 HU
- 病灶的 HU 统计特征完全失真

**影响范围**:
- `run_simulation_job()` — 后台仿真任务
- `preview_lesion_on_dicom()` — DICOM 预览接口

**修复优先级**: 🔴 最高 — 这是病灶在前端不可见的直接原因

---

### 🐛 Bug #2（中）：体素计数阈值错误 — 已修复

**文件**: `backend/app/simulation/lesion/generator.py` 和 `organ/simulator.py`

**修改前**:
```python
"voxel_count": int(np.sum(preview > -500))
```

**修改后**:
```python
"voxel_count": int(np.sum(preview != 0))
```

**根因分析**:
- 原阈值 `> -500` 用于区分"非空气"区域，但对于磨玻璃结节（HU ≈ -600~-400）会漏计数
- 病灶生成器中非病灶区域为 0，使用 `!= 0` 更准确

**影响范围**:
- `generate_preview()` 返回的统计信息
- 前端预览中的数据展示

**修复优先级**: 🟡 中等

---

### 🔍 Bug #3（潜在）：Dead Code Branch — 已修复

**文件**: `backend/app/simulation/lesion/generator.py`

- `if _debug_mask_count == 0` 嵌套在 `if _debug_mask_count > 0` 内部，永不可达
- 已将诊断逻辑合并到 `else` 分支

---

## 三、尚未定位的潜在问题区域

### 🔶 区域 A：Z 轴 spacing 压缩

**现状**: CT 数据的典型 spacing 为 `(5.0, 0.7, 0.7)` mm（层厚5mm，面内0.7mm）

**计算示例**:
```
spacing = (5.0, 0.7, 0.7)  # (z, y, x)
radius_mm = (10, 10, 10)
radius_voxel = (10/5.0, 10/0.7, 10/0.7) = (2.0, 14.3, 14.3)
```

**问题**: Z 方向只有 2 个体素！病灶在轴向视图中几乎不可见，在冠状面/矢状面中表现为扁平的"饼状"。

**建议**:
- 增加病灶 Z 半径至 `max(radius_z, spacing_z * 3)` 确保至少 3 个体素厚度
- 或者在生成病灶时对 Z 方向进行上采样

**影响层**: 病灶生成阶段 → Viewer 显示阶段

---

### 🔶 区域 B：mask 阈值不一致

**现状**:
- `generator.py` 中调试日志使用 `mask > 0.01` 作为判定阈值
- `simulation.py` 中写入时使用 `lesion_vol != 0` 作为判定阈值
- 两者理论上等价，但如果病灶 HU 值恰好为 0.0，会出现漏判

**建议**: 统一使用 `mask > 0.01` 或 `np.abs(lesion_vol) > 1e-6` 等方式

---

### 🔶 区域 C：SimpleITK 坐标系转换

**现状**:
```python
# volume is (z, y, x), SimpleITK expects (x, y, z)
sitk_spacing = (spacing[2], spacing[1], spacing[0])
sitk_origin = (origin[2], origin[1], origin[0])
```

**说明**:
- 当前的 (z,y,x)→(x,y,z) 转换逻辑是正确的
- `_debug_verify_sitk_metadata()` 已验证读出写入一致性
- 但如果上游 `volume_builder.py` 返回的 metadata 顺序有误，会导致下游坐标偏移

**验证方式**: 通过 Debug API 返回的 `bbox` 与实际数据对比

---

## 四、问题层级判定

### 复现路径

```
generate_lesion()
↓
_generate_lesion_volume()   ← ✅ 病灶生成正常（Debug日志可验证）
↓
result_volume[lesion_mask]
  = lesion_volume[...]      ← ❌ 原 Bug #1 已修复
↓
SimpleITK 写出              ← ✅ NRRD 写出正常（Task 6 验证）
↓
NRRD/NIfTI                  ← ✅ 文件正常生成
↓
Cornerstone3D Viewer        ← ⚠️ 需前端配合验证
```

### 结论

| 层级 | 状态 | 备注 |
|------|------|------|
| 病灶生成阶段 | ✅ 正常 | Debug日志可确认体素生成 |
| 病灶写入 CT 阶段 | ❌ **Bug #1 已修复** | 加法改为掩码赋值 |
| 文件保存阶段 | ✅ 正常 | SimpleITK 元数据完整 |
| Cornerstone3D 加载 | ⚠️ 待验证 | 需检查 NRRD 加载是否正确 |
| Viewer 显示阶段 | ⚠️ 待验证 | 需检查 WW/WL 窗口设置 |

**核心发现**: Bug #1（写入方式错误）是导致病灶在前端不可见的**直接原因**。原代码使用加法叠加病灶，导致病灶 HU 值完全失真。

---

## 五、修复建议与优先级

### P0 — 立即修复（完成度 100%）
1. ✅ `result_volume[lesion_mask] = lesion_vol[lesion_mask]` — 已完成
2. ✅ `preview_lesion_on_dicom` 中同样修改 — 已完成

### P1 — 建议修复
3. ⬜ 添加 Z 轴最小体素厚度保护（确保 `radius_voxel_z >= 3`）
   - 位置: `generate_lesion()` 方法中
   - 方案: `radii = (max(rz, spacing_z * 3), ry, rx)`

4. ⬜ 统一 mask 判定阈值
   - 位置: 所有使用 `!= 0` 或 `> 0.01` 的地方
   - 方案: 统一为 `> 1e-6`

### P2 — 增强健壮性
5. ⬜ Debug API 增加 spacing 保护提示
6. ⬜ 自动化测试：验证生成->写出->读回的一致性

---

## 六、如何使用诊断工具

### 1. 运行仿真任务时查看日志

启用 DEBUG 日志级别即可看到所有调试输出：

```python
import logging
logging.getLogger("app.simulation.lesion").setLevel(logging.DEBUG)
```

### 2. 调用 Debug API

```bash
POST /api/v1/simulation/debug-lesion
Content-Type: application/json

{
  "lesion_type": "tumor",
  "shape": "spherical",
  "radius_x": 15,
  "radius_y": 15,
  "radius_z": 15,
  "hu_mean": 40,
  "hu_std": 20,
  "margin_sharpness": 0.8,
  "spacing": [5.0, 0.7, 0.7]
}
```

返回体素统计、位置验证、spacing 换算、写入验证、以及 base64 PNG 预览图。

### 3. PNG 可视化输出

设置 `DEBUG=1` 环境变量后，仿真任务会自动保存 PNG 到 `./debug_output/`：
- `lesion_mask_middle_slice.png`
- `lesion_hu_middle_slice.png`
- `result_volume_middle_slice.png`
- `difference_map.png`

---

## 七、验收标准

修复完成后，通过以下标准确认病灶在前端可见：

| 验收项 | 方法 | 预期结果 |
|--------|------|---------|
| 体素生成 | Debug API `lesion_voxels` | > 0 |
| 体素写入 | Debug API `changed_voxels` | = lesion_voxels |
| HU 正确 | Debug API `lesion_hu_mean` | ≈ 设定值 |
| 位置合理 | Debug API `inside_volume` | true |
| 前端可见 | 打开 Viewer，调整 WW/WL | 病灶清晰可见 |
| Z 轴厚度 | Debug API `radius_voxel[0]` | >= 3 |
