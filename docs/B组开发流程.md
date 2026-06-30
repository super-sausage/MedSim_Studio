# B 组（图像质量与伪影处理）开发流程文档

> **文档版本**: v1.0
> **创建日期**: 2026-06-29
> **负责人**: B 组
> **对接方**: A 组（CT 影像与病灶仿真）

---

## 目录

1. [项目现状分析](#1-项目现状分析)
2. [A 组接口与数据规范](#2-a-组接口与数据规范)
3. [B 组开发目标与架构设计](#3-b-组开发目标与架构设计)
4. [开发环境搭建流程](#4-开发环境搭建流程)
5. [第一阶段：伪影生成模块](#5-第一阶段伪影生成模块)
6. [第二阶段：伪影分类模块](#6-第二阶段伪影分类模块)
7. [第三阶段：去伪影修复模块](#7-第三阶段去伪影修复模块)
8. [第四阶段：前后端集成与可视化](#8-第四阶段前后端集成与可视化)
9. [测试与验证规范](#9-测试与验证规范)
10. [里程碑与交付节点](#10-里程碑与交付节点)

---

## 1. 项目现状分析

### 1.1 项目概览

**MedSim Studio** 是一个基于 Web 的医学影像仿真平台，目前由 A 组完成核心开发。项目采用前后端分离架构：

| 层次 | 技术栈 | 说明 |
|------|--------|------|
| **前端** | React 18 + TypeScript + Vite | SPA 应用，含 Cornerstone3D(2D) + vtk.js(3D) |
| **后端** | FastAPI + Python 3.11 | RESTful API，含仿真引擎 + AI 推理 |
| **数据库** | PostgreSQL | 关系型元数据存储 |
| **对象存储** | MinIO (S3 兼容) | DICOM 像素数据 + 仿真结果存储 |
| **AI 服务** | MONAI + PyTorch + nnUNet | 器官分割模型 |
| **部署** | Docker + docker-compose | 容器化编排 |

### 1.2 项目目录结构（关键模块）

```
MedSim_Studio/
├── frontend/src/
│   ├── pages/
│   │   ├── ViewerPage.tsx          # 影像查看器（MPR三视图 + 3D渲染）
│   │   ├── StudiesPage.tsx         # DICOM 研究管理
│   │   ├── SimulationPage.tsx      # 病灶仿真界面（2326行，A组核心产出）
│   │   └── SegmentationPage.tsx    # AI 分割界面
│   ├── viewer/                     # Cornerstone3D 2D 渲染引擎
│   ├── vtk/                        # vtk.js 3D 体渲染引擎
│   ├── simulation/                 # 仿真前端逻辑
│   │   ├── lesion/                 # 病灶生成
│   │   ├── organ/                  # 器官模拟
│   │   ├── deformation/            # 形变场
│   │   ├── hu/                     # HU 值操作
│   │   └── generators/             # 生成器管线
│   ├── services/                   # API 服务层
│   ├── store/                      # Zustand 状态管理
│   └── types/simulation.ts         # 仿真类型定义（含 CT 参数类型）
│
├── backend/app/
│   ├── api/v1/
│   │   ├── simulation.py           # ★ 核心仿真API（B组对接入口）
│   │   ├── dicom.py                # DICOM 上传/管理
│   │   ├── segmentation.py         # AI 分割接口
│   │   └── health.py               # 健康检查
│   ├── simulation/
│   │   ├── ct_params_simulator.py  # ★ CT扫描参数仿真引擎
│   │   ├── phantom_generator.py    # ★ CT幻影生成器（程序化+图谱）
│   │   ├── volume_builder.py       # ★ 从DICOM构建HU体积
│   │   ├── lesion/generator.py     # 病灶生成器
│   │   ├── organ/simulator.py      # 器官模拟器
│   │   ├── deformation/field.py    # 形变场
│   │   ├── hu/modifier.py          # HU修改器
│   │   └── exporter.py             # 导出（NRRD/NIfTI/DICOM）
│   ├── schemas/simulation.py       # Pydantic 数据模型
│   ├── models/simulation.py        # SQLAlchemy ORM 模型
│   ├── dicom/                      # DICOM 解析与存储
│   └── ai/                         # AI 分割模块
│       ├── monai/                  # MONAI 模型
│       ├── nnunet_custom/          # 自定义 nnUNet（6类）
│       ├── nnunet_custom_20/       # 自定义 nnUNet（20类）
│       └── totalsegmentator/       # TotalSegmentator
│
├── docs/
│   ├── interface_ct_simulation_to_artifact.md  # ★ A-B组接口规范
│   ├── architecture/system-design.md           # 系统架构文档
│   └── api/overview.md                         # API概览
│
├── models/                         # 预训练模型文件
│   ├── phantom_atlas/              # CT 图谱数据
│   └── nnunet701_full_handoff/     # nnUNet 训练产出
│
├── docker/                         # Docker 构建配置
├── datasets/                       # 数据集目录
├── scripts/                        # 工具脚本
├── tests/                          # 测试
├── docker-compose.yml              # 服务编排
└── .env.example                    # 环境变量模板
```

### 1.3 A 组已实现功能清单

#### 1.3.1 CT 影像基础能力

| 功能 | 状态 | 关键技术 |
|------|------|----------|
| DICOM 文件上传与解析 | ✅ 完成 | pydicom + PostgreSQL + MinIO |
| DICOM 研究/序列/实例管理 | ✅ 完成 | SQLAlchemy ORM |
| MPR 三视图渲染（Axial/Sagittal/Coronal） | ✅ 完成 | Cornerstone3D |
| 3D 体渲染（GPU 加速） | ✅ 完成 | vtk.js ray casting |
| Window/Level 调节 | ✅ 完成 | Cornerstone3D |
| 裁切平面、相机预设 | ✅ 完成 | vtk.js |

#### 1.3.2 仿真引擎

| 功能 | 状态 | 说明 |
|------|------|------|
| **CT 幻影生成 — 程序化** | ✅ 完成 | 几何体素构建上半身CT（身体轮廓/肺/脊柱/肋骨/心脏/肝脏/脾脏/肾脏/主动脉/气管） |
| **CT 幻影生成 — 图谱化** | ✅ 完成 | 从真实 CT NIfTI + 20类器官标注加载（`models/phantom_atlas/`） |
| **CT 扫描参数仿真** | ✅ 完成 | image-domain 近似仿真（非物理级仿真，教育用途） |
| **病灶生成** | ✅ 完成 | 5种类型（Tumor/Nodule/Cyst/Calcification/Metastasis），5种形状（球形/椭球形/不规则/分叶/毛刺） |
| **器官模拟** | ✅ 完成 | 9种器官的HU值模拟 |
| **形变场** | ✅ 完成 | 刚性/仿射/B样条/Demons |
| **仿真结果导出** | ✅ 完成 | NRRD / NIfTI / DICOM |

#### 1.3.3 CT 扫描参数仿真详情（`ct_params_simulator.py`）

A 组的 CT 扫描参数仿真器支持以下 8 个参数维度的 **image-domain 近似仿真**：

| 步骤 | 参数 | 实现方式 | 关键函数 |
|------|------|----------|----------|
| 1 | `slice_thickness_mm` (层厚) | Z 轴高斯模糊（sigma=厚度增量/2） | `_apply_slice_thickness()` |
| 2 | `dose_level` / `mAs` (剂量/管电流) | 高斯噪声（σ∝1/√mAs，剂量级别缩放） | `_apply_dose_noise()` |
| 3 | `kVp` (管电压) | HU 对比度重映射（80kVp→1.18x, 140kVp→0.93x） | `_apply_kvp_transform()` |
| 4 | `pitch` (螺距) | Z 轴模糊（pitch>1时，σ=(pitch-1)*0.8） | `_apply_pitch_effect()` |
| 5 | `fov_mm` (视野) | XY平面缩放 + 裁切/填充 | `_apply_fov_effect()` |
| 6 | `matrix_size` (矩阵) | 降采样/上采样模拟分辨率变化 | `_apply_matrix_effect()` |
| 7 | `kernel` (重建核) | 高斯平滑/Unsharp Mask锐化/Laplacian增强 | `_apply_kernel_effect()` |
| 8 | `contrast_phase` (造影期相) | 基于器官标签的经验HU增强 | `_apply_contrast_phase()` |

> **重要**：这些仿真都是 image-domain approximation，不是真实的 CT 物理模型（Monte Carlo / ray-tracing）。A 组在响应中明确标注了 `approximation_warning`。

### 1.4 数据库模型（与 B 组相关）

**SimulationJob 表**（`simulation_jobs`）:
- `id`, `study_id`, `series_id`: 作业标识与关联
- `status`: pending → running → completed / failed
- `lesion_count`, `organ_count`: 配置统计
- `output_path`: 仿真结果在 MinIO 中的 object_key
- `output_format`: dicom / nifti / nrrd

**LesionConfig 表**（`lesion_configs`）:
- 病灶类型、形状、中心坐标、半径(mm)、HU均值/标准差
- 边缘锐度、钙化比例、坏死比例、毛刺程度

**OrganConfig 表**（`organ_configs`）:
- 器官类型、HU均值/标准差、噪声/增强配置

> B 组需要新增自己的数据库模型，详见后续章节。

---

## 2. A 组接口与数据规范

### 2.1 接口文档位置

接口规范文件：[docs/interface_ct_simulation_to_artifact.md](docs/interface_ct_simulation_to_artifact.md)

### 2.2 核心接口（B 组消费侧）

#### 接口 1: `GET /api/v1/simulation/phantom` — 获取原始 CT 幻影

**用途**：获取未经参数仿真的原始 CT 体积数据。

```
GET /api/v1/simulation/phantom?source=atlas&case_id=s0001&size=160&scan_direction=head_to_feet
```

**响应**：
```json
{
  "volume_base64": "<float32 little-endian base64, 轴序 zyx>",
  "label_base64": "<uint8 base64, 可选, 仅 atlas 模式>",
  "metadata": {
    "width": 160, "height": 160, "depth": 160,
    "spacing": [1.5, 1.2, 1.2],
    "source": "atlas", "case_id": "s0001",
    "window_presets": { ... },
    "body_threshold_hu": -500.0
  }
}
```

**解码规范**：
- `volume_base64` → base64 解码 → `np.frombuffer(buf, dtype='<f4')` → reshape 为 `(depth, height, width)`（zyx 轴序）
- `label_base64` → base64 解码 → `np.frombuffer(buf, dtype=np.uint8)` → reshape 为 `(depth, height, width)`

#### 接口 2: `POST /api/v1/simulation/ct-params/preview` — CT 参数仿真预览

**用途**：对原始 CT 体积应用扫描参数仿真，获取仿真后的 CT 体积。

**请求体**：
```json
{
  "source": "atlas",
  "case_id": "s0001",
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
    "contrast_phase": "noncontrast"
  }
}
```

**响应**：
```json
{
  "simulated_volume_base64": "<float32 LE base64, zyx>",
  "metadata": {
    "shape": [160, 160, 160],
    "spacing": [1.5, 1.2, 1.2],
    "hu_range": [-1000.0, 2000.0],
    "algorithm_notes": [...],
    "warnings": [...]
  },
  "params_json": {
    "requested_params": { ... },
    "resolved_params": { ... },
    "algorithm_steps": [...],
    "input_shape": [...], "output_shape": [...],
    "hu_range_before": [...], "hu_range_after": [...]
  },
  "standardized_case": {
    "case_id": "sim_atlas_s0001_20260629_120000",
    "source": "atlas",
    "volume": {
      "encoding": "base64",
      "dtype": "float32",
      "byte_order": "little_endian",
      "axis_order": "zyx",
      "shape": [160, 160, 160],
      "spacing": [1.5, 1.2, 1.2],
      "origin": [0.0, 0.0, 0.0],
      "direction": [[1,0,0],[0,1,0],[0,0,1]],
      "hu_range": [-1000.0, 2000.0],
      "slice_count": 160,
      "modality": "CT",
      "body_part": "upper_body",
      "image_kind": "simulated_ct",
      "image_data_field": "simulated_volume_base64"
    },
    "simulation": {
      "type": "ct_scan_params",
      "params_json": { ... },
      "algorithm": "image_domain_approximation",
      "approximation_warning": "This is an educational image-domain approximation..."
    }
  }
}
```

### 2.3 B 组三种数据获取场景

| 场景 | 接口 | source 参数 | 说明 |
|------|------|-------------|------|
| **场景 A**: 原始 atlas CT | `GET /simulation/phantom` | `source=atlas` | 获得原始 HU 体积 + 器官标签 |
| **场景 B**: atlas/procedural 参数仿真 CT | `POST /simulation/ct-params/preview` | `source=atlas` 或 `procedural` | 经过扫描参数仿真的体积 |
| **场景 C**: 用户上传 DICOM 参数仿真 CT | `POST /simulation/ct-params/preview` | `source=dicom` + `series_id` | 真实 DICOM 经参数仿真后的体积 |

### 2.4 体积数据解码模板（Python）

```python
import base64
import numpy as np

def decode_simulated_volume(b64_str: str, shape: tuple, dtype='<f4'):
    """解码 A 组返回的 base64 体积数据"""
    raw = base64.b64decode(b64_str)
    volume = np.frombuffer(raw, dtype=dtype).reshape(shape)
    # shape 为 (z, y, x)，与 metadata.shape 一致
    return volume.astype(np.float32)
```

---

## 3. B 组开发目标与架构设计

### 3.1 总体目标

B 组负责 **图像质量与伪影处理** 三大核心模块：

1. **伪影生成（Artifact Generation）**：在 CT 图像上模拟真实扫描中常见的各类伪影
2. **伪影分类（Artifact Classification）**：对含伪影的图像进行自动分类识别
3. **去伪影修复（Artifact Removal/Restoration）**：对含伪影图像进行修复还原

### 3.2 系统架构集成方案

B 组模块将以 **独立后端模块 + 对应前端界面** 的形式集成到 MedSim Studio 中：

```
┌─────────────────────────────────────────────────────────────┐
│                      Frontend (React)                       │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────┐ │
│  │  Viewer  │ │ Simulation│ │Segment-  │ │  ★ Artifact  │ │
│  │  Module  │ │ Module(A) │ │ation Mod │ │  Module (B)  │ │
│  └────┬─────┘ └─────┬─────┘ └────┬─────┘ └──────┬───────┘ │
└───────┼─────────────┼────────────┼───────────────┼─────────┘
        │             │            │               │
┌───────┼─────────────┼────────────┼───────────────┼─────────┐
│       ▼             ▼            ▼               ▼         │
│                   Backend (FastAPI)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │  DICOM   │ │Simulation│ │ Segment- │ │★ Artifact    │  │
│  │  Service │ │ Engine(A)│ │ ation(A) │ │  Engine (B)  │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
│                                                             │
│  ★ B组新增模块:                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  backend/app/artifact/                                │  │
│  │  ├── generator/        # 伪影生成器                     │  │
│  │  │   ├── metal.py      # 金属伪影 (beam hardening等)    │  │
│  │  │   ├── motion.py     # 运动伪影                       │  │
│  │  │   ├── noise.py      # 噪声伪影 (量子/电子噪声)        │  │
│  │  │   ├── ring.py       # 环状伪影                       │  │
│  │  │   ├── streak.py     # 条状伪影                       │  │
│  │  │   └── beam_hardening.py # 射束硬化伪影               │  │
│  │  ├── classifier/       # 伪影分类器                     │  │
│  │  │   ├── model.py      # 分类模型定义                    │  │
│  │  │   └── inference.py  # 推理管线                       │  │
│  │  ├── restoration/      # 伪影修复/去噪                   │  │
│  │  │   ├── traditional.py # 传统方法 (滤波/插值)           │  │
│  │  │   └── deep_learning.py # 深度学习方法                 │  │
│  │  ├── evaluation/       # 质量评估                        │  │
│  │  │   └── metrics.py    # PSNR/SSIM/NMSE等              │  │
│  │  └── utils/            # 工具函数                        │  │
│  │      ├── volume_io.py  # 体积数据读写                    │  │
│  │      └── sinogram.py   # 正弦图转换（Radon/iRadon)       │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 B 组新增目录结构

```
MedSim_Studio/
├── backend/app/
│   ├── artifact/                        # ★ B组核心模块
│   │   ├── __init__.py
│   │   ├── generator/                   # 伪影生成
│   │   │   ├── __init__.py
│   │   │   ├── base.py                  # 生成器基类
│   │   │   ├── metal_artifact.py        # 金属伪影
│   │   │   ├── motion_artifact.py       # 运动伪影
│   │   │   ├── noise_artifact.py        # 噪声伪影
│   │   │   ├── ring_artifact.py         # 环状伪影
│   │   │   ├── streak_artifact.py       # 条状伪影
│   │   │   ├── beam_hardening.py        # 射束硬化
│   │   │   └── partial_volume.py        # 部分容积效应
│   │   ├── classifier/                  # 伪影分类
│   │   │   ├── __init__.py
│   │   │   ├── model.py                 # CNN/ViT 分类模型
│   │   │   ├── dataset.py              # 数据集类
│   │   │   ├── train.py                # 训练脚本
│   │   │   └── inference.py            # 推理接口
│   │   ├── restoration/                 # 伪影修复
│   │   │   ├── __init__.py
│   │   │   ├── traditional.py           # 传统修复方法
│   │   │   ├── deep_learning.py         # 深度学习修复
│   │   │   └── hybrid.py               # 混合方法
│   │   ├── evaluation/                  # 质量评估
│   │   │   ├── __init__.py
│   │   │   └── metrics.py              # PSNR/SSIM/NMSE/IoU
│   │   └── utils/                       # 工具
│   │       ├── __init__.py
│   │       ├── volume_io.py            # A组体积数据编解码
│   │       ├── sinogram.py             # Radon/iRadon 变换
│   │       └── visualization.py        # 结果可视化
│   ├── api/v1/
│   │   └── artifact.py                 # ★ B组 API 路由
│   ├── models/
│   │   └── artifact.py                 # ★ B组数据库模型
│   └── schemas/
│       └── artifact.py                 # ★ B组 Pydantic schema
│
├── frontend/src/
│   ├── pages/
│   │   └── ArtifactPage.tsx            # ★ 伪影处理主页面
│   ├── artifact/                       # ★ 伪影前端模块
│   │   ├── index.ts
│   │   ├── ArtifactGenerator.tsx       # 伪影生成器UI
│   │   ├── ArtifactClassifier.tsx      # 伪影分类器UI
│   │   ├── ArtifactRestoration.tsx     # 伪影修复UI
│   │   └── ComparisonView.tsx          # 对比视图组件
│   ├── services/
│   │   └── artifactService.ts          # ★ 伪影 API 服务
│   ├── store/
│   │   └── useArtifactStore.ts         # ★ 伪影状态管理
│   └── types/
│       └── artifact.ts                 # ★ 伪影类型定义
│
├── models/
│   └── artifact/                       # ★ 伪影相关模型权重
│       ├── classifier/                 # 分类模型权重
│       └── restoration/                # 修复模型权重
│
└── tests/
    └── backend/
        └── artifact/                   # ★ B组单元测试
```

---

## 4. 开发环境搭建流程

### 4.1 前置条件

- **操作系统**: macOS / Linux / Windows (WSL2 推荐)
- **Python**: 3.11+
- **Node.js**: 20+
- **Docker**: 24+ (含 docker-compose)
- **GPU** (推荐): CUDA 12.x + cuDNN 8.x（深度学习推理加速，非必需）
- **磁盘空间**: ≥20GB（含模型权重和测试数据）

### 4.2 克隆并启动现有项目

```bash
# Step 1: 确认现有项目可运行
cd /Users/wangay/Downloads/MedSim_Studio

# Step 2: 复制环境配置
cp .env.example .env

# Step 3: Docker 启动所有基础服务
docker-compose up -d

# Step 4: 验证服务
curl http://localhost:8000/api/v1/health
# 预期: {"status": "healthy"}

curl http://localhost:8000/docs
# 预期: Swagger UI 页面可访问

curl http://localhost:3000
# 预期: 前端页面可访问
```

### 4.3 创建 B 组开发分支

```bash
cd /Users/wangay/Downloads/MedSim_Studio

# Step 1: 创建 B 组功能分支
git checkout -b feature/artifact-pipeline

# Step 2: 创建 B 组目录结构
mkdir -p backend/app/artifact/{generator,classifier,restoration,evaluation,utils}
mkdir -p frontend/src/artifact
mkdir -p models/artifact/{classifier,restoration}
mkdir -p tests/backend/artifact

# Step 3: 创建 __init__.py 文件
touch backend/app/artifact/__init__.py
touch backend/app/artifact/generator/__init__.py
touch backend/app/artifact/classifier/__init__.py
touch backend/app/artifact/restoration/__init__.py
touch backend/app/artifact/evaluation/__init__.py
touch backend/app/artifact/utils/__init__.py
```

### 4.4 Python 依赖补充

在 [backend/requirements.txt](backend/requirements.txt) 中追加 B 组所需依赖：

```txt
# B组 - 伪影处理
scikit-image>=0.22.0        # Radon/iRadon 变换、图像处理
torch>=2.1.0                # 深度学习框架
torchvision>=0.16.0         # 预训练模型 + 数据增强
timm>=0.9.0                 # 预训练 ViT/CNN 模型库
albumentations>=1.4.0       # 图像数据增强
monai>=1.3.0                # 医学影像 AI（复用A组已有）
```

安装：

```bash
cd backend
pip install scikit-image torch torchvision timm albumentations
```

### 4.5 环境验证脚本

创建 `scripts/verify_b_env.py`：

```python
#!/usr/bin/env python3
"""验证 B 组开发环境"""
import sys

def check_module(name):
    try:
        __import__(name)
        print(f"  ✅ {name}")
        return True
    except ImportError:
        print(f"  ❌ {name} NOT FOUND")
        return False

print("B 组环境检查:")
print("核心依赖:")
check_module("numpy")
check_module("scipy")
check_module("skimage")
check_module("torch")
check_module("torchvision")
check_module("timm")

print("\nA 组接口测试:")
try:
    import requests
    r = requests.get("http://localhost:8000/api/v1/health")
    assert r.status_code == 200
    print("  ✅ Backend API 可达")
except Exception as e:
    print(f"  ❌ Backend API 不可达: {e}")

print("\n体积解码测试:")
try:
    import numpy as np
    import base64
    # 模拟解码
    vol = np.random.randn(32, 32, 32).astype(np.float32)
    b64 = base64.b64encode(vol.tobytes()).decode()
    decoded = np.frombuffer(base64.b64decode(b64), dtype='<f4').reshape((32, 32, 32))
    assert np.allclose(vol, decoded)
    print("  ✅ 体积解码逻辑正确")
except Exception as e:
    print(f"  ❌ 解码测试失败: {e}")

print("\n检查完成")
```

运行：
```bash
python scripts/verify_b_env.py
```

---

## 5. 第一阶段：伪影生成模块

### 5.1 目标

实现 6 大类 CT 伪影的仿真生成，可叠加到 A 组产出的 CT 体积数据上。

### 5.2 需要实现的伪影类型

| 编号 | 伪影类型 | 英文名 | 产生机理 | 实现优先级 |
|------|----------|--------|----------|-----------|
| AF1 | 金属伪影 | Metal Artifact | 高密度金属（假牙/植入物/支架）引起射束硬化+散射 | P0 |
| AF2 | 运动伪影 | Motion Artifact | 患者呼吸/心跳/移动导致 | P0 |
| AF3 | 量子噪声 | Quantum Noise | 光子数不足（低剂量扫描） | P0 |
| AF4 | 环状伪影 | Ring Artifact | 探测器通道增益不一致 | P1 |
| AF5 | 条状伪影 | Streak Artifact | 光子饥饿/金属/锥束效应 | P1 |
| AF6 | 射束硬化 | Beam Hardening | 低能光子被优先吸收，X射线束"硬化" | P1 |
| AF7 | 部分容积效应 | Partial Volume | 层厚过大，体素内组织混合 | P2 |

### 5.3 详细实现步骤

#### 5.3.1 步骤 1：创建生成器基类

**文件**：[backend/app/artifact/generator/base.py](backend/app/artifact/generator/base.py)

```python
"""
伪影生成器基类

定义所有伪影生成器的统一接口：接收 CT 体积 -> 返回含伪影体积 + 伪影掩码。
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple, Optional
import numpy as np


class BaseArtifactGenerator(ABC):
    """伪影生成器抽象基类"""

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    @abstractmethod
    def generate(
        self,
        volume: np.ndarray,
        spacing: Tuple[float, float, float],
        params: Dict[str, Any],
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """
        生成伪影。

        Args:
            volume:  输入 CT 体积 (z, y, x), float32, HU 值
            spacing: 体素间距 (z, y, x) mm
            params:  伪影参数字典

        Returns:
            (artifact_volume, artifact_mask, metadata) 三元组:
            - artifact_volume: 叠加伪影后的 CT 体积 (shape 同输入)
            - artifact_mask:   伪影影响区域掩码 (0/1, shape 同输入)
            - metadata:        生成参数记录
        """
        ...

    @abstractmethod
    def get_default_params(self) -> Dict[str, Any]:
        """返回该伪影类型的默认参数"""
        ...

    @abstractmethod
    def validate_params(self, params: Dict[str, Any]) -> bool:
        """验证参数合法性"""
        ...

    def get_artifact_type(self) -> str:
        """返回伪影类型标识"""
        return self.__class__.__name__.replace("Generator", "").lower()
```

**验证方法**：
```python
# 验证基类不可直接实例化
def test_base_class():
    import pytest
    with pytest.raises(TypeError):
        BaseArtifactGenerator()  # 抽象类不可实例化
```

#### 5.3.2 步骤 2：实现金属伪影生成器（优先级 P0）

**文件**：[backend/app/artifact/generator/metal_artifact.py](backend/app/artifact/generator/metal_artifact.py)

**实现原理**：

金属伪影（Metal Artifact）是 CT 成像中最常见的伪影之一，由高密度金属物体（如牙科填充物、骨科植入物、动脉瘤夹）引起。其物理机制包括：

1. **射束硬化（Beam Hardening）**：低能光子被金属优先吸收，导致投射数据中的非线性衰减
2. **光子饥饿（Photon Starvation）**：金属遮挡后到达探测器的光子数极少，信噪比极低
3. **散射辐射（Scatter）**：金属边缘产生额外散射

**图像域近似方法**（与 A 组仿真引擎一致，采用 image-domain approximation）：

```
输入: CT 体积 (HU), 金属位置 (center + shape), 金属材质参数

Step 1: 生成金属区域掩码 (3D 椭球/自定义形状)
Step 2: 以金属区域为中心生成暗带条纹 (dark streaks), 方向为各角度辐射状
Step 3: 叠加射束硬化效应: 金属区域周围软组织 HU 值降低 (非线性衰减)
Step 4: 添加 Poisson 噪声模拟光子饥饿
Step 5: 将金属区域内部 HU 值设为极高值 (3000+ HU)

输出: (含金属伪影的体积, 伪影掩码, 参数记录)
```

**伪代码实现**：

```python
import numpy as np
from scipy.ndimage import gaussian_filter, distance_transform_edt
from .base import BaseArtifactGenerator
from typing import Dict, Any, Tuple


class MetalArtifactGenerator(BaseArtifactGenerator):
    """金属伪影生成器 — 模拟高密度金属物体引起的射束硬化+条纹伪影"""

    METAL_HU = {
        "titanium": 2500.0,
        "stainless_steel": 3071.0,
        "dental_amalgam": 3071.0,
        "gold": 3071.0,
        "copper": 2800.0,
    }

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "metal_type": "titanium",
            "metal_hu": 2500.0,
            "center": [0.5, 0.5, 0.5],     # 归一化坐标 [z, y, x], 各维在 [0,1]
            "radius_mm": [5.0, 5.0, 5.0],   # 金属物体半径 (z, y, x) mm
            "streak_intensity": 0.7,          # 条纹强度 [0, 1]
            "beam_hardening_strength": 0.5,   # 射束硬化强度 [0, 1]
            "photon_starvation_noise": 0.3,   # 光子饥饿噪声水平 [0, 1]
        }

    def validate_params(self, params: Dict[str, Any]) -> bool:
        required = ["metal_type", "center", "radius_mm"]
        return all(k in params for k in required)

    def generate(
        self,
        volume: np.ndarray,
        spacing: Tuple[float, float, float],
        params: Dict[str, Any],
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        # 合并默认参数
        p = {**self.get_default_params(), **params}
        nz, ny, nx = volume.shape

        # Step 1: 创建金属区域掩码（3D 椭球）
        metal_hu = p["metal_hu"]
        center_voxel = (
            int(p["center"][0] * nz),
            int(p["center"][1] * ny),
            int(p["center"][2] * nx),
        )
        radius_voxel = (
            p["radius_mm"][0] / spacing[0],
            p["radius_mm"][1] / spacing[1],
            p["radius_mm"][2] / spacing[2],
        )

        z_idx, y_idx, x_idx = np.indices(volume.shape, dtype=np.float32)
        metal_dist = np.sqrt(
            ((z_idx - center_voxel[0]) / radius_voxel[0]) ** 2 +
            ((y_idx - center_voxel[1]) / radius_voxel[1]) ** 2 +
            ((x_idx - center_voxel[2]) / radius_voxel[2]) ** 2
        )
        metal_mask = metal_dist <= 1.0

        # Step 2: 生成条纹伪影（条状暗带，辐射状从金属中心发出）
        result = volume.copy().astype(np.float32)

        # 对每个轴向切片生成条纹
        for z in range(nz):
            slice_2d = result[z]
            cy_z, cx_z = center_voxel[1], center_voxel[2]
            yy, xx = np.indices(slice_2d.shape, dtype=np.float32)

            # 极坐标角度
            theta = np.arctan2(yy - cy_z, xx - cx_z)
            r = np.sqrt((yy - cy_z) ** 2 + (xx - cx_z) ** 2)

            # 条纹: 多个角度的暗带
            n_streaks = 12
            streak_pattern = np.zeros_like(slice_2d)
            for i in range(n_streaks):
                angle = 2 * np.pi * i / n_streaks + self.rng.uniform(-0.1, 0.1)
                angular_dist = np.abs(np.sin(theta - angle))
                streak_weight = np.exp(-angular_dist ** 2 / 0.02)  # 窄角度
                streak_decay = np.exp(-r / max(ny, nx) * 3)       # 随距离衰减
                streak_pattern += streak_weight * streak_decay

            streak_pattern = np.clip(streak_pattern, 0, 1)
            # 暗条纹降低 HU
            dark_streaks = streak_pattern * p["streak_intensity"] * 800.0
            # 只影响金属区域外且非背景区域
            tissue_mask_z = volume[z] > -500  # 组织区域
            affect_mask_z = tissue_mask_z & (~metal_mask[z])
            result[z][affect_mask_z] -= dark_streaks[affect_mask_z]

        # Step 3: 射束硬化效应 (金属周围组织 HU 降低)
        dist_to_metal = distance_transform_edt(~metal_mask)
        bh_radius = 30  # 影响半径 (体素)
        bh_mask = (dist_to_metal > 0) & (dist_to_metal <= bh_radius)
        bh_weight = (1 - dist_to_metal[bh_mask] / bh_radius) * p["beam_hardening_strength"]
        # 非线性 HU 降低
        hu_reduction = bh_weight * 200.0 * (1 + self.rng.normal(0, 0.1, size=np.sum(bh_mask)))
        result[bh_mask] -= hu_reduction.astype(np.float32)

        # Step 4: 光子饥饿噪声 (金属投影方向 Poisson 噪声增强)
        if p["photon_starvation_noise"] > 0:
            noise_sigma = p["photon_starvation_noise"] * 25.0
            noise = self.rng.normal(0, noise_sigma, size=volume.shape).astype(np.float32)
            # 噪声强度随到金属的距离增大而减弱
            dist_weight = np.clip(1 - dist_to_metal / bh_radius, 0, 1)
            tissue_mask = volume > -200  # 只影响非空气区域
            result[tissue_mask] += (noise * dist_weight)[tissue_mask]

        # Step 5: 设置金属区域为极高 HU
        result[metal_mask] = metal_hu

        # 构建伪影掩码 (受伪影影响的区域)
        artifact_mask = (metal_mask | (dist_to_metal <= bh_radius) | (np.abs(result - volume) > 5)).astype(np.float32)

        metadata = {
            "artifact_type": self.get_artifact_type(),
            "params": p,
            "metal_mask_voxel_count": int(np.sum(metal_mask)),
            "artifact_region_voxel_count": int(np.sum(artifact_mask > 0)),
        }

        return np.clip(result, -1024, 3071), artifact_mask, metadata
```

**期待实现的效果**：
- 金属区域呈现极高 HU 值（2500-3071 HU）
- 金属周围出现放射状暗色条纹（streak artifacts）
- 金属附近软组织 HU 值降低（射束硬化 effect）
- 可配置金属类型、位置、大小、伪影强度

**验证方法**：
1. **单元测试**：对不同金属类型/位置/大小生成伪影，检查输出形状、值域、掩码覆盖率
2. **可视化检查**：导出中间轴向切片为 PNG，人工确认条纹方向/强度合理
3. **定量指标**：对比生成前后的中心切片统计数据（均值、标准差变化）
4. **边界情况**：金属在体积边缘、金属超出体积边界、极小/极大金属尺寸

```python
# tests/backend/artifact/test_metal_artifact.py
def test_metal_artifact_basic():
    from app.artifact.generator.metal_artifact import MetalArtifactGenerator
    gen = MetalArtifactGenerator(seed=42)
    vol = np.ones((64, 64, 64), dtype=np.float32) * 40  # 软组织背景
    spacing = (1.0, 1.0, 1.0)
    params = gen.get_default_params()
    result, mask, meta = gen.generate(vol, spacing, params)

    assert result.shape == vol.shape
    assert result.dtype == np.float32
    assert mask.shape == vol.shape
    assert np.any(result != vol)  # 确实产生了变化
    assert 2500 <= np.max(result) <= 3071  # 金属区域 HU 极高
    print("✅ 金属伪影基本测试通过")
```

#### 5.3.3 步骤 3：实现运动伪影生成器（优先级 P0）

**文件**：[backend/app/artifact/generator/motion_artifact.py](backend/app/artifact/generator/motion_artifact.py)

**实现原理**：

运动伪影由患者扫描过程中的运动（呼吸、心跳、身体移动）引起。在图像域中模拟，通过施加局部平移/旋转形变场实现。

```
输入: CT 体积 (HU), 运动参数 (类型/幅度/方向/频率)

Step 1: 根据运动类型生成时变位移场
  - 呼吸运动: z方向正弦平移 (频率 ~0.25 Hz, 幅度 5-20mm)
  - 心跳运动: 局部搏动 (心脏区域小幅度快速位移)
  - 随机运动: 随机突跳 (患者突然移动)
  
Step 2: 对不同 z 切片应用不同位移 (模拟逐层扫描中的运动不一致)
Step 3: 双线性插值重采样
Step 4: 添加运动导致的模糊 (沿运动方向高斯卷积)

输出: (含运动伪影的体积, 伪影掩码, 参数记录)
```

**期待实现的效果**：
- 器官边界模糊/重影 (motion blur/ghosting)
- 解剖结构错位 (misalignment between slices)
- 可通过参数控制运动类型（呼吸/心跳/随机）和幅度

**验证方法**：
1. 对静态假体施加运动参数，检查切片间连续性破坏
2. 生成前后比较特定解剖结构的位置偏移量
3. 检查运动模糊方向是否与运动方向一致
4. 极端参数下不应产生裁剪或 NaN

#### 5.3.4 步骤 4：实现量子噪声伪影生成器（优先级 P0）

**文件**：[backend/app/artifact/generator/noise_artifact.py](backend/app/artifact/generator/noise_artifact.py)

**实现原理**：

CT 量子噪声主要由到达探测器的 X 射线光子数不足引起。噪声特性为：
- **Poisson 分布**：光子计数服从 Poisson 统计
- **剂量依赖性**：噪声方差 ∝ 1/剂量
- **组织依赖性**：高衰减区域（骨骼后）噪声更大

**图像域近似**（不同于 A 组的简单高斯噪声，这里实现更真实的物理模型）：

```
Step 1: HU -> 线性衰减系数 μ 转换: μ = (HU/1000 + 1) * μ_water
Step 2: 计算等效光子数: N = N0 * exp(-∫μ ds)，N0 ∝ mAs
Step 3: 施加 Poisson 噪声: N_noisy ~ Poisson(N)
Step 4: 反投影重建 → 含噪声 HU 体积
Step 5: 可选：模拟电子噪声 (Gaussian 加性噪声)
```

```python
def hu_to_attenuation(hu: np.ndarray, mu_water: float = 0.2) -> np.ndarray:
    """HU -> 线性衰减系数 (cm⁻¹)"""
    return (hu / 1000.0 + 1.0) * mu_water

def attenuation_to_hu(mu: np.ndarray, mu_water: float = 0.2) -> np.ndarray:
    """线性衰减系数 -> HU"""
    return (mu / mu_water - 1.0) * 1000.0

def apply_quantum_noise(
    volume: np.ndarray,
    mAs: float,
    reference_mAs: float = 150.0,
    slice_thickness_mm: float = 1.0,
) -> np.ndarray:
    """施加量子噪声到 CT 体积"""
    N0 = 1e5 * (mAs / reference_mAs) * (slice_thickness_mm / 1.0)
    mu = hu_to_attenuation(volume)
    # 模拟投影域光子计数
    expected_photons = N0 * np.exp(-mu)
    expected_photons = np.clip(expected_photons, 0.1, None)
    detected_photons = np.random.poisson(expected_photons)
    # 反投影重建
    mu_noisy = -np.log(np.clip(detected_photons / N0, 1e-6, None))
    return attenuation_to_hu(mu_noisy)
```

**期待实现的效果**：
- 低剂量扫描呈现明显颗粒感噪声
- 噪声纹理与真实 CT 低剂量噪声相似
- 高衰减区域（骨后）噪声更强
- 噪声强度与 mAs 呈反比关系

**验证方法**：
1. 测量噪声标准差与 mAs 的 -0.5 次方关系 (σ ∝ 1/√mAs)
2. 噪声分布检验：ROI 内噪声应近似 Gaussian（高计数极限）
3. 与真实低剂量 CT 图像的噪声功率谱 (NPS) 对比
4. 人体模型中不同组织区域的噪声水平差异检查

#### 5.3.5 步骤 5：实现环状伪影生成器（优先级 P1）

**文件**：[backend/app/artifact/generator/ring_artifact.py](backend/app/artifact/generator/ring_artifact.py)

**实现原理**：

环状伪影由探测器通道增益不一致引起。在第三代 CT（旋转-旋转）中，缺陷探测器通道在投影域产生同心环。

```
Step 1: 对每个 z 切片执行 Radon 变换（图像域 -> 投影域/sinogram）
Step 2: 在 sinogram 中选择若干列（对应探测器通道）添加恒定偏移
Step 3: 执行逆 Radon 变换（投影域 -> 图像域）
Step 4: 得到含同心环伪影的图像
```

```python
from skimage.transform import radon, iradon

def generate_ring_artifact(
    volume: np.ndarray,
    num_rings: int = 3,
    intensity: float = 50.0,
) -> np.ndarray:
    """在 CT 体积的每个切片上生成环状伪影"""
    result = volume.copy()
    nz = volume.shape[0]

    for z in range(nz):
        slice_2d = volume[z]
        # Radon 变换
        theta = np.linspace(0., 180., max(slice_2d.shape), endpoint=False)
        sinogram = radon(slice_2d, theta=theta, circle=True)

        # 在随机探测器通道上添加偏移
        bad_channels = np.random.choice(sinogram.shape[1], size=num_rings, replace=False)
        for ch in bad_channels:
            sinogram[:, ch] += intensity * np.random.choice([-1, 1])

        # 逆 Radon 变换
        result[z] = iradon(sinogram, theta=theta, circle=True,
                           filter_name="ramp")

    return result.astype(np.float32)
```

**期待实现的效果**：
- 轴向切片上出现同心圆环（完整或部分圆弧）
- 环的亮度/暗度可配置
- 环的数量和位置可配置

**验证方法**：
1. 对均匀假体施加环状伪影，检查是否产生同心环
2. 在极坐标下验证环的半径一致性
3. 检查环状伪影的强度是否与配置一致

#### 5.3.6 步骤 6：实现条状伪影生成器（优先级 P1）

**文件**：[backend/app/artifact/generator/streak_artifact.py](backend/app/artifact/generator/streak_artifact.py)

**实现原理**：

条状伪影由多种原因引起（光子饥饿、金属、锥束效应），表现为穿过高密度区域的直线暗带/亮带。

```
Step 1: 识别高密度区域位置（如金属、骨骼）
Step 2: 对每个轴向切片，在 sinogram 域中对通过这些高密度区域的投影路径添加不一致性
Step 3: 逆 Radon 变换得到含条状伪影的图像
Step 4: 可选：添加锥束伪影（多层螺旋 CT 特有，z 方向）
```

**期待实现的效果**：
- 高密度结构之间出现直线暗带
- 暗带方向沿 X 射线投影路径
- 可控制条带强度和密度

#### 5.3.7 步骤 7：实现射束硬化伪影生成器（优先级 P1）

**文件**：[backend/app/artifact/generator/beam_hardening.py](backend/app/artifact/generator/beam_hardening.py)

**实现原理**：

射束硬化是多色 X 射线束通过物体时，低能光子优先被吸收，导致束流平均能量升高（"硬化"），表现为：
- **杯状伪影（Cupping）**：均匀物体中心 HU 低于边缘
- **暗带（Dark bands）**：骨骼之间的低密度带

**图像域近似**：

```
Step 1: 计算每个像素到物体边缘的距离
Step 2: 施加径向 HU 降低（杯状效应），降低量与路径长度成正比
Step 3: 在骨骼之间施加额外的 HU 降低（暗带效应）
```

**期待实现的效果**：
- 均匀假体扫描呈现中心暗、边缘亮的杯状效应
- 颅底/骨盆区域骨骼间出现特征性暗带
- 效应强度与物体密度和大小成正比

#### 5.3.8 步骤 8：创建伪影生成器注册表

**文件**：[backend/app/artifact/generator/__init__.py](backend/app/artifact/generator/__init__.py)

```python
"""伪影生成器注册表 — 统一管理所有生成器类型"""

from .metal_artifact import MetalArtifactGenerator
from .motion_artifact import MotionArtifactGenerator
from .noise_artifact import NoiseArtifactGenerator
from .ring_artifact import RingArtifactGenerator
from .streak_artifact import StreakArtifactGenerator
from .beam_hardening import BeamHardeningGenerator

ARTIFACT_GENERATORS = {
    "metal": MetalArtifactGenerator,
    "motion": MotionArtifactGenerator,
    "noise": NoiseArtifactGenerator,
    "ring": RingArtifactGenerator,
    "streak": StreakArtifactGenerator,
    "beam_hardening": BeamHardeningGenerator,
}

def get_generator(artifact_type: str):
    """根据类型名获取生成器实例"""
    if artifact_type not in ARTIFACT_GENERATORS:
        raise ValueError(f"Unknown artifact type: {artifact_type}. "
                         f"Available: {list(ARTIFACT_GENERATORS.keys())}")
    return ARTIFACT_GENERATORS[artifact_type]()

def list_artifact_types():
    """列出所有可用伪影类型"""
    return list(ARTIFACT_GENERATORS.keys())
```

#### 5.3.9 步骤 9：创建组合生成器

**文件**：[backend/app/artifact/generator/composite.py](backend/app/artifact/generator/composite.py)（待创建）

组合生成器允许同时施加多种伪影，模拟真实扫描中的复合伪影场景。

**期待实现的效果**：
- 支持配置多种伪影的叠加顺序
- 自动处理伪影间的相互作用
- 输出每种伪影的独立掩码和组合掩码

---

## 6. 第二阶段：伪影分类模块

### 6.1 目标

开发伪影自动分类系统，可识别 CT 图像中存在的伪影类型。

### 6.2 分类类别定义

| 类别 ID | 伪影类型 | 标签 |
|---------|---------|------|
| 0 | 无伪影 (Clean) | clean |
| 1 | 金属伪影 | metal |
| 2 | 运动伪影 | motion |
| 3 | 量子噪声 | noise |
| 4 | 环状伪影 | ring |
| 5 | 条状伪影 | streak |
| 6 | 射束硬化 | beam_hardening |
| 7 | 混合伪影 (多类同时存在) | mixed |

### 6.3 详细实现步骤

#### 6.3.1 步骤 1：构建训练数据集

**数据来源**：使用第一阶段生成的伪影数据作为训练基础。

```
数据集构建流程：

Step 1: 从 A 组获取干净 CT 体积（atlas phantom / 程序化 phantom / DICOM）
Step 2: 使用伪影生成器为每个体积施加不同伪影
Step 3: 从每个体积的中间切片提取 2D 图像（512×512）
Step 4: 标注每张图像的伪影类型（可多标签）
Step 5: 划分训练集/验证集/测试集（70% / 15% / 15%）

数据增强：
- 随机旋转 (±15°)
- 随机翻转 (水平/垂直)
- 随机亮度/对比度调整 (±10%)
- 随机裁剪 + 缩放
```

**文件**：[backend/app/artifact/classifier/dataset.py](backend/app/artifact/classifier/dataset.py)

```python
import torch
from torch.utils.data import Dataset
import numpy as np
from typing import List, Tuple, Optional
import albumentations as A


class ArtifactClassificationDataset(Dataset):
    """伪影分类数据集 — 支持多标签分类"""

    def __init__(
        self,
        images: List[np.ndarray],        # (N, H, W) 灰度图
        labels: List[List[int]],         # 多热编码 [N, num_classes]
        transform: Optional[A.Compose] = None,
    ):
        self.images = images
        self.labels = labels
        self.transform = transform

    def __len__(self): return len(self.images)

    def __getitem__(self, idx):
        img = self.images[idx]
        label = torch.tensor(self.labels[idx], dtype=torch.float32)

        if self.transform:
            augmented = self.transform(image=img)
            img = augmented["image"]

        # 转换为 3 通道 (灰度复制) + 归一化
        img = np.stack([img] * 3, axis=0).astype(np.float32)
        img = (img - img.mean()) / (img.std() + 1e-6)
        return torch.from_numpy(img), label


def build_dataset_from_volume(
    volume: np.ndarray,     # (z, y, x) CT 体积
    slice_indices: List[int],
    label_vector: List[int],
) -> Tuple[List[np.ndarray], List[List[int]]]:
    """从 CT 体积构建分类数据集"""
    images = []
    labels = []
    for z in slice_indices:
        if 0 <= z < volume.shape[0]:
            slice_img = volume[z].copy()
            # HU 窗口化 (软组织窗)
            wl, ww = 40, 400
            slice_img = np.clip((slice_img - (wl - ww/2)) / ww * 255, 0, 255)
            images.append(slice_img.astype(np.uint8))
            labels.append(label_vector)
    return images, labels
```

#### 6.3.2 步骤 2：构建分类模型

**模型选型**：

| 模型 | 优点 | 缺点 | 推荐场景 |
|------|------|------|----------|
| ResNet-50 | 成熟稳定，训练快 | 精度中等 | 快速原型验证 |
| EfficientNet-B3 | 精度/效率平衡好 | 需要更多调参 | 正式使用 |
| ViT-B/16 | 全局感受野，适合纹理 | 需要更多数据 | 大数据量场景 |
| ConvNeXt-T | 现代CNN设计 | 训练稍慢 | 追求最优精度 |

**推荐方案**：使用 EfficientNet-B3 作为 baseline，后续可升级到 ConvNeXt。

**文件**：[backend/app/artifact/classifier/model.py](backend/app/artifact/classifier/model.py)

```python
import torch
import torch.nn as nn
import timm


class ArtifactClassifier(nn.Module):
    """多标签伪影分类器"""

    def __init__(
        self,
        num_classes: int = 8,
        backbone: str = "efficientnet_b3",
        pretrained: bool = True,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.backbone = timm.create_model(
            backbone,
            pretrained=pretrained,
            num_classes=0,  # 移除分类头
        )
        feature_dim = self.backbone.num_features
        self.classifier = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(feature_dim, 512),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(512, num_classes),
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        features = self.backbone(x)
        logits = self.classifier(features)
        return self.sigmoid(logits)  # 多标签输出 [0,1]


def create_classifier(
    num_classes: int = 8,
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
) -> ArtifactClassifier:
    """工厂函数：创建分类器实例"""
    model = ArtifactClassifier(num_classes=num_classes)
    model = model.to(device)
    return model
```

**期待实现的效果**：
- 对单一伪影类型分类准确率 > 90%
- 对混合伪影检测召回率 > 80%
- 推理速度：单张图片 < 50ms (GPU)
- 输出每个伪影类别的置信度分数

#### 6.3.3 步骤 3：训练脚本

**文件**：[backend/app/artifact/classifier/train.py](backend/app/artifact/classifier/train.py)

**训练配置**：
```python
TRAIN_CONFIG = {
    "batch_size": 32,
    "epochs": 50,
    "learning_rate": 1e-4,
    "weight_decay": 1e-5,
    "lr_scheduler": "cosine",
    "warmup_epochs": 5,
    "loss": "bce_with_logits",  # 多标签二值交叉熵
    "early_stopping_patience": 10,
    "mixed_precision": True,     # AMP 混合精度
}
```

**验证方法**：
1. **训练监控**：记录 train/val loss、per-class accuracy、F1-score
2. **混淆矩阵**：每个类别的一对一混淆统计
3. **ROC-AUC**：每个类别的 AUC 值
4. **CAM/Grad-CAM**：可视化模型关注的区域是否对应伪影位置

#### 6.3.4 步骤 4：推理接口

**文件**：[backend/app/artifact/classifier/inference.py](backend/app/artifact/classifier/inference.py)

```python
class ArtifactInference:
    """伪影分类推理器"""

    CLASS_NAMES = [
        "clean", "metal", "motion", "noise",
        "ring", "streak", "beam_hardening", "mixed",
    ]

    def __init__(self, model_path: str):
        self.model = self._load_model(model_path)
        self.transform = self._get_transform()

    def predict_slice(self, slice_2d: np.ndarray) -> Dict[str, float]:
        """对单张切片进行伪影分类"""
        ...

    def predict_volume(self, volume: np.ndarray) -> Dict[str, Any]:
        """对整个 CT 体积进行伪影分类，返回每层和整体的分类结果"""
        slice_results = []
        for z in range(volume.shape[0]):
            result = self.predict_slice(volume[z])
            slice_results.append(result)

        # 聚合结果
        overall = {
            name: np.mean([r[name] for r in slice_results])
            for name in self.CLASS_NAMES
        }
        return {
            "overall_scores": overall,
            "per_slice_scores": slice_results,
            "dominant_artifact": max(overall, key=overall.get),
        }
```

---

## 7. 第三阶段：去伪影修复模块

### 7.1 目标

对含伪影的 CT 图像进行去伪影修复，使其尽量恢复原始干净图像。

### 7.2 技术路线

采用 **传统方法 + 深度学习方法** 双路线：

| 方法 | 技术 | 适用伪影 | 优点 | 缺点 |
|------|------|----------|------|------|
| **传统方法** | | | | |
| 中值滤波 | Median Filter | 椒盐噪声 | 快速、简单 | 模糊边缘 |
| 非局部均值 | NLM (Non-Local Means) | 高斯/量子噪声 | 保持纹理 | 计算量大 |
| 小波去噪 | Wavelet Denoising | 高斯噪声 | 多尺度 | 参数选择困难 |
| sinogram 域处理 | Radon域修复 | 环状/条状 | 物理准确 | 需投影域计算 |
| 金属伪影去除 | MAR (Metal Artifact Reduction) | 金属伪影 | 针对性强 | 需金属分割 |
| **深度学习方法** | | | | |
| DnCNN | 残差学习去噪 | 通用噪声 | 成熟、稳定 | 仅去噪 |
| RED-CNN | 残差编解码 | 低剂量噪声 | CT 专用 | 需要配对数据 |
| CycleGAN | 非配对翻译 | 通用风格转换 | 无需配对数据 | 训练不稳定 |
| DU-GAN | 双域 U-Net GAN | 金属伪影 | 效果优秀 | 训练复杂 |
| SwinIR | Transformer 恢复 | 通用伪影 | SOTA 效果 | 显存大 |

**推荐方案**：
- 噪声伪影 → RED-CNN（已有成熟 CT 去噪方案）
- 金属伪影 → sinogram 域 MAR + 图像域 U-Net 混合方法
- 运动伪影 → CycleGAN（无需精确配对数据）
- 环状/条状 → sinogram 域检测 + 插值修复

### 7.3 详细实现步骤

#### 7.3.1 步骤 1：传统去噪方法

**文件**：[backend/app/artifact/restoration/traditional.py](backend/app/artifact/restoration/traditional.py)

```python
import numpy as np
from scipy.ndimage import median_filter, gaussian_filter
from skimage.restoration import denoise_nl_means, estimate_sigma
from skimage.transform import radon, iradom
from typing import Dict, Any, Optional


class TraditionalRestorer:
    """传统方法伪影修复器"""

    @staticmethod
    def median_denoise(volume: np.ndarray, kernel_size: int = 3) -> np.ndarray:
        """中值滤波去噪 (适用于椒盐噪声/环状伪影)"""
        return median_filter(volume, size=kernel_size).astype(np.float32)

    @staticmethod
    def nlm_denoise(
        volume: np.ndarray,
        patch_size: int = 5,
        patch_distance: int = 11,
        h: Optional[float] = None,
    ) -> np.ndarray:
        """非局部均值去噪 (适用于量子噪声)"""
        if h is None:
            h = 0.8 * estimate_sigma(volume, channel_axis=None)
        result = np.zeros_like(volume)
        for z in range(volume.shape[0]):
            result[z] = denoise_nl_means(
                volume[z],
                patch_size=patch_size,
                patch_distance=patch_distance,
                h=h,
                fast_mode=True,
            )
        return result.astype(np.float32)

    @staticmethod
    def sinogram_ring_correction(
        volume: np.ndarray,
        threshold: float = 0.1,
    ) -> np.ndarray:
        """sinogram 域环状伪影校正"""
        result = np.zeros_like(volume)
        for z in range(volume.shape[0]):
            theta = np.linspace(0., 180., max(volume[z].shape), endpoint=False)
            sino = radon(volume[z], theta=theta, circle=True)
            # 中值滤波去除纵向条纹 (环在sinogram中表现为纵向条纹)
            sino_corrected = sino - median_filter(
                sino, size=(1, 5)
            ) * threshold
            result[z] = iradom(sino_corrected, theta=theta, circle=True,
                                filter_name="ramp")
        return result.astype(np.float32)

    @staticmethod
    def mar_sinogram_interpolation(
        volume: np.ndarray,
        metal_mask: np.ndarray,
    ) -> np.ndarray:
        """
        金属伪影减少 (MAR) — sinogram 域插值方法

        1. 识别金属区域对应的 sinogram 轨迹
        2. 对受影响的投影数据进行插值
        3. 逆 Radon 变换重建
        """
        result = np.zeros_like(volume)
        for z in range(volume.shape[0]):
            theta = np.linspace(0., 180., max(volume[z].shape), endpoint=False)
            sino = radon(volume[z], theta=theta, circle=True)

            # 检测金属影响区域
            metal_sino = radon(metal_mask[z].astype(float), theta=theta, circle=True)
            affected = metal_sino > 0.01

            # 线性插值修复
            sino_corrected = sino.copy()
            for ch in range(sino.shape[1]):
                if affected[:, ch].any():
                    good = ~affected[:, ch]
                    if good.sum() >= 2:
                        sino_corrected[:, ch] = np.interp(
                            np.arange(sino.shape[0]),
                            np.where(good)[0],
                            sino[good, ch],
                        )

            result[z] = iradom(sino_corrected, theta=theta, circle=True,
                                filter_name="ramp")
        return result.astype(np.float32)
```

#### 7.3.2 步骤 2：深度学习修复模型

**文件**：[backend/app/artifact/restoration/deep_learning.py](backend/app/artifact/restoration/deep_learning.py)

**模型架构**：RED-CNN（Residual Encoder-Decoder CNN）

```
输入: 含伪影的 CT 切片 (1×H×W)
  |
  ├─ Conv + ReLU (64通道)
  ├─ 编码器: 5× Conv + ReLU (64→64→64→64→64)
  ├─ 解码器: 5× Deconv + ReLU (64→64→64→64→64)
  ├─ 跳跃连接 (编码器-解码器)
  ├─ Conv (1通道)
  └─ 残差连接: output = input - residual
输出: 去伪影 CT 切片 (1×H×W)
```

```python
import torch
import torch.nn as nn


class REDCNN(nn.Module):
    """Residual Encoder-Decoder CNN for CT denoising"""

    def __init__(self, in_channels: int = 1, out_channels: int = 1,
                 num_features: int = 96, num_layers: int = 5):
        super().__init__()
        self.conv_first = nn.Sequential(
            nn.Conv2d(in_channels, num_features, 3, padding=1),
            nn.ReLU(inplace=True),
        )

        # 编码器
        self.encoders = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(num_features, num_features, 3, padding=1),
                nn.ReLU(inplace=True),
            )
            for _ in range(num_layers)
        ])

        # 解码器
        self.decoders = nn.ModuleList([
            nn.Sequential(
                nn.Conv2d(num_features, num_features, 3, padding=1),
                nn.ReLU(inplace=True),
            )
            for _ in range(num_layers)
        ])

        self.conv_last = nn.Conv2d(num_features, out_channels, 3, padding=1)

    def forward(self, x):
        residual = x
        out = self.conv_first(x)

        # 编码 + 保存跳跃连接
        skip_connections = []
        for encoder in self.encoders:
            out = encoder(out)
            skip_connections.append(out)

        # 解码 + 跳跃连接
        for i, decoder in enumerate(self.decoders):
            out = decoder(out)
            out = out + skip_connections[-(i + 1)]

        out = self.conv_last(out)
        return residual - out  # 残差学习
```

**训练数据**：
- 使用第一阶段伪影生成器生成配对数据 (clean, artifact)
- 也可使用非配对数据 + CycleGAN 损失

**期待实现的效果**：
- PSNR 提升 ≥ 3dB（相比含伪影图像）
- SSIM 提升 ≥ 0.05
- 去伪影后不应引入新的伪影
- 保持解剖结构边缘不模糊

#### 7.3.3 步骤 3：混合修复策略

**文件**：[backend/app/artifact/restoration/hybrid.py](backend/app/artifact/restoration/hybrid.py)

```
混合修复流程：

Step 1: 伪影分类 → 确定伪影类型
Step 2: 根据伪影类型选择修复策略:
  - 金属伪影 → sinogram MAR + 图像域精细修复
  - 量子噪声 → RED-CNN 深度学习去噪
  - 运动伪影 → CycleGAN + 配准补偿
  - 环状伪影 → sinogram 域检测 + 中值滤波
  - 复合伪影 → 分步处理 (先金属后噪声)
Step 3: 后处理: 保边滤波 + HU 值裁剪
Step 4: 输出修复结果 + 修复质量评估
```

---

## 8. 第四阶段：前后端集成与可视化

### 8.1 后端 API 开发

#### 8.1.1 步骤 1：Pydantic Schema 定义

**文件**：[backend/app/schemas/artifact.py](backend/app/schemas/artifact.py)

```python
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ArtifactGenerateRequest(BaseModel):
    """伪影生成请求"""
    source: Literal["atlas", "procedural", "dicom"] = "atlas"
    case_id: Optional[str] = None
    series_id: Optional[str] = None
    size: int = Field(160, ge=64, le=192)
    artifact_types: List[str]  # e.g., ["metal", "noise"]
    artifact_params: Dict[str, Dict[str, Any]] = {}  # 每种伪影的参数
    ct_params: Optional[Dict[str, Any]] = None  # 可选：先应用CT参数仿真


class ArtifactGenerateResponse(BaseModel):
    """伪影生成响应"""
    artifact_volume_base64: str       # 含伪影的体积
    clean_volume_base64: str          # 原始干净体积 (用于对比)
    artifact_masks_base64: Dict[str, str]  # 每种伪影的掩码
    metadata: Dict[str, Any]
    standardized_case: Dict[str, Any]


class ArtifactClassifyRequest(BaseModel):
    """伪影分类请求"""
    volume_base64: str
    shape: List[int]
    spacing: List[float]


class ArtifactClassifyResponse(BaseModel):
    """伪影分类响应"""
    overall_scores: Dict[str, float]
    per_slice_scores: List[Dict[str, float]]
    dominant_artifact: str
    confidence: float


class ArtifactRestoreRequest(BaseModel):
    """伪影修复请求"""
    volume_base64: str
    artifact_types: List[str]
    method: Literal["traditional", "deep_learning", "auto"] = "auto"
    shape: List[int]
    spacing: List[float]


class ArtifactRestoreResponse(BaseModel):
    """伪影修复响应"""
    restored_volume_base64: str
    residual_map_base64: str           # 修复前后的差异图
    quality_metrics: Dict[str, float]  # PSNR/SSIM/NMSE
    metadata: Dict[str, Any]
```

#### 8.1.2 步骤 2：数据库模型定义

**文件**：[backend/app/models/artifact.py](backend/app/models/artifact.py)

```python
from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from app.database.session import Base


class ArtifactJob(Base):
    """伪影处理作业"""
    __tablename__ = "artifact_jobs"

    id = Column(String, primary_key=True, index=True)
    simulation_job_id = Column(String, ForeignKey("simulation_jobs.id"), nullable=True)
    job_type = Column(String, nullable=False)  # generate / classify / restore
    status = Column(String, default="pending")  # pending/running/completed/failed

    # 配置
    config = Column(JSON, nullable=True)

    # 输出
    output_path = Column(String, nullable=True)
    output_format = Column(String, default="nrrd")

    # 质量指标
    quality_metrics = Column(JSON, nullable=True)

    # 时间
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # 关联
    simulation_job = relationship("SimulationJob", foreign_keys=[simulation_job_id])
```

#### 8.1.3 步骤 3：API 路由

**文件**：[backend/app/api/v1/artifact.py](backend/app/api/v1/artifact.py)

主要端点规划：

| Method | Endpoint | 说明 |
|--------|----------|------|
| `POST` | `/artifact/generate` | 生成伪影 |
| `POST` | `/artifact/classify` | 伪影分类 |
| `POST` | `/artifact/restore` | 伪影修复 |
| `POST` | `/artifact/pipeline` | 完整流水线（生成→分类→修复）|
| `GET` | `/artifact/jobs` | 作业列表 |
| `GET` | `/artifact/jobs/{id}` | 作业详情 |
| `GET` | `/artifact/types` | 支持的伪影类型列表 |

#### 8.1.4 步骤 4：注册路由到主应用

在 `backend/app/main.py` 中添加：

```python
from app.api.v1.artifact import router as artifact_router
app.include_router(artifact_router, prefix="/api/v1")
```

### 8.2 前端开发

#### 8.2.1 步骤 1：TypeScript 类型定义

**文件**：[frontend/src/types/artifact.ts](frontend/src/types/artifact.ts)

```typescript
/** 伪影类型 */
export type ArtifactType =
  | 'metal'
  | 'motion'
  | 'noise'
  | 'ring'
  | 'streak'
  | 'beam_hardening'
  | 'partial_volume';

/** 伪影生成参数 */
export interface ArtifactParams {
  metal?: MetalArtifactParams;
  motion?: MotionArtifactParams;
  noise?: NoiseArtifactParams;
  ring?: RingArtifactParams;
  streak?: StreakArtifactParams;
  beamHardening?: BeamHardeningParams;
}

export interface MetalArtifactParams {
  metalType: 'titanium' | 'stainless_steel' | 'dental_amalgam';
  center: [number, number, number];
  radiusMm: [number, number, number];
  streakIntensity: number;
  beamHardeningStrength: number;
}

export interface MotionArtifactParams {
  motionType: 'respiratory' | 'cardiac' | 'random';
  amplitudeMm: number;
  frequencyHz: number;
  direction: 'z' | 'xy' | 'all';
}

export interface NoiseArtifactParams {
  noiseType: 'quantum' | 'electronic' | 'mixed';
  doseLevel: 'low' | 'standard' | 'high';
  mAs: number;
}

// ... 其他伪影参数类型

/** 伪影生成请求/响应 */
export interface ArtifactGenerateRequest {
  source: 'atlas' | 'procedural' | 'dicom';
  caseId?: string;
  seriesId?: string;
  size: number;
  artifactTypes: ArtifactType[];
  artifactParams: Record<string, Record<string, unknown>>;
  ctParams?: Record<string, unknown>;
}

export interface ArtifactGenerateResponse {
  artifactVolumeBase64: string;
  cleanVolumeBase64: string;
  artifactMasksBase64: Record<string, string>;
  metadata: Record<string, unknown>;
  standardizedCase: StandardizedCtCase;
}

/** 伪影分类结果 */
export interface ArtifactClassificationResult {
  overallScores: Record<string, number>;
  perSliceScores: Array<Record<string, number>>;
  dominantArtifact: string;
  confidence: number;
}

/** 伪影修复结果 */
export interface ArtifactRestorationResult {
  restoredVolumeBase64: string;
  residualMapBase64: string;
  qualityMetrics: {
    psnr: number;
    ssim: number;
    nmse: number;
  };
}
```

#### 8.2.2 步骤 2：API 服务层

**文件**：[frontend/src/services/artifactService.ts](frontend/src/services/artifactService.ts)

#### 8.2.3 步骤 3：状态管理

**文件**：[frontend/src/store/useArtifactStore.ts](frontend/src/store/useArtifactStore.ts)

使用 Zustand 管理以下状态：
- 伪影生成配置
- 伪影处理结果（3D 体积 + 2D 切片预览）
- 分类/修复结果
- 对比视图状态（原始/伪影/修复）

#### 8.2.4 步骤 4：UI 组件开发

**主页面**：[frontend/src/pages/ArtifactPage.tsx](frontend/src/pages/ArtifactPage.tsx)

页面布局设计：
```
┌─────────────────────────────────────────────────────┐
│  Artifact Processing                           [帮助] │
├──────────────┬──────────────────────────────────────┤
│  控制面板     │         可视化区域                    │
│              │                                      │
│  ┌──────────┐│  ┌────────────────────────────────┐  │
│  │ 数据源    ││  │                                │  │
│  │ ○ Atlas  ││  │      CT 切片对比视图             │  │
│  │ ○ Proced ││  │                                │  │
│  │ ○ DICOM  ││  │  [原始] [含伪影] [修复后]       │  │
│  ├──────────┤│  │                                │  │
│  │ 伪影选择  ││  │   Axial  |  Sagittal  | Coronal │  │
│  │ ☑ 金属   ││  │                                │  │
│  │ ☑ 噪声   ││  └────────────────────────────────┘  │
│  │ ☐ 运动   ││                                      │
│  │ ☐ 环状   ││  ┌────────────────────────────────┐  │
│  ├──────────┤│  │  伪影分类结果                    │  │
│  │ 参数配置  ││  │  ████████████ Metal: 92%       │  │
│  │          ││  │  ████ Noise: 35%                │  │
│  ├──────────┤│  │  ██ Motion: 12%                 │  │
│  │ [生成伪影]││  └────────────────────────────────┘  │
│  │ [分类]   ││                                      │
│  │ [修复]   ││  ┌────────────────────────────────┐  │
│  │ [导出]   ││  │  修复质量评估                    │  │
│  └──────────┘│  │  PSNR: 35.2 dB  SSIM: 0.946   │  │
│              │  └────────────────────────────────┘  │
└──────────────┴──────────────────────────────────────┘
```

#### 8.2.5 步骤 5：路由集成

在 [frontend/src/router/](frontend/src/router/) 中添加 B 组页面路由：

```typescript
{
  path: '/artifact',
  element: <ArtifactPage />,
  name: '伪影处理',
}
```

---

## 9. 测试与验证规范

### 9.1 测试层级与分工

| 测试层级 | 工具 | 覆盖目标 | 负责人 |
|----------|------|----------|--------|
| **单元测试** | pytest | 每个生成器/分类器/修复器的独立功能 | 开发者 |
| **集成测试** | pytest + requests | API 端到端流程、A/B组接口兼容性 | 开发者 |
| **视觉验证** | matplotlib + 人工评审 | 伪影视觉真性、修复效果主观评价 | 全员 |
| **定量评估** | 自定义脚本 | PSNR/SSIM/NMSE 等客观指标 | 开发者 |
| **性能测试** | locust | API 响应时间、并发处理能力 | 专项 |

### 9.2 单元测试结构

```
tests/backend/artifact/
├── conftest.py                      # 测试 fixtures
├── test_metal_artifact.py           # 金属伪影生成器测试
├── test_motion_artifact.py          # 运动伪影生成器测试
├── test_noise_artifact.py           # 噪声伪影生成器测试
├── test_ring_artifact.py            # 环状伪影生成器测试
├── test_streak_artifact.py          # 条状伪影生成器测试
├── test_beam_hardening.py           # 射束硬化生成器测试
├── test_composite_generator.py      # 组合生成器测试
├── test_classifier_model.py         # 分类模型测试
├── test_classifier_inference.py     # 分类推理测试
├── test_restoration_traditional.py  # 传统修复测试
├── test_restoration_dl.py           # 深度学习修复测试
├── test_integration_api.py          # API 集成测试
└── test_volume_io.py                # 体积数据 I/O 测试
```

### 9.3 测试数据准备

```python
# tests/backend/artifact/conftest.py
import pytest
import numpy as np

@pytest.fixture
def simple_ct_volume():
    """简单均匀 CT 体积 (64³, 软组织 HU=40)"""
    vol = np.ones((64, 64, 64), dtype=np.float32) * 40.0
    vol[:, :, :10] = -1000.0  # 左侧空气
    vol[:, :, -10:] = -1000.0  # 右侧空气
    return vol

@pytest.fixture
def complex_ct_volume():
    """复杂假体 (含骨骼、软组织、空气)"""
    vol = np.full((64, 64, 64), -1000.0, dtype=np.float32)
    # 软组织椭球
    z, y, x = np.indices((64, 64, 64), dtype=float)
    dist = np.sqrt(((z-32)/20)**2 + ((y-32)/25)**2 + ((x-32)/22)**2)
    vol[dist <= 1] = 40.0
    # 脊柱
    spine_dist = np.sqrt(((y-48)/6)**2 + ((x-32)/8)**2)
    vol[(dist <= 1) & (spine_dist <= 1)] = 800.0
    return vol.astype(np.float32)

@pytest.fixture
def standard_spacing():
    return (1.0, 1.0, 1.0)
```

### 9.4 定量评估指标

**文件**：[backend/app/artifact/evaluation/metrics.py](backend/app/artifact/evaluation/metrics.py)

```python
import numpy as np
from skimage.metrics import (
    peak_signal_noise_ratio,
    structural_similarity,
    normalized_root_mse,
)

def compute_image_quality(original: np.ndarray, restored: np.ndarray) -> dict:
    """计算图像质量指标"""
    data_range = original.max() - original.min()

    psnr_val = peak_signal_noise_ratio(original, restored, data_range=data_range)

    # SSIM (逐层计算取平均)
    ssim_vals = [
        structural_similarity(
            original[z], restored[z],
            data_range=data_range,
        )
        for z in range(original.shape[0])
    ]
    ssim_val = np.mean(ssim_vals)

    # NMSE
    nmse_val = normalized_root_mse(original, restored)

    # MAE (Mean Absolute Error in HU)
    mae_hu = np.mean(np.abs(original - restored))

    return {
        "psnr_db": round(float(psnr_val), 2),
        "ssim": round(float(ssim_val), 4),
        "nmse": round(float(nmse_val), 6),
        "mae_hu": round(float(mae_hu), 2),
    }
```

### 9.5 视觉验证标准

对每种伪影类型，需要生成 **验证图集** 并人工评审：

1. **原始干净切片** (参考标准)
2. **含伪影切片** (标注伪影类型和参数)
3. **修复后切片** (标注修复方法和指标)
4. **差异图** (伪影图 - 干净图，修复图 - 干净图)

验收标准：
- 伪影视觉特征与真实 CT 伪影一致 (由医学物理师确认)
- 修复结果不引入新的伪影
- 解剖结构边界保持清晰
- HU 值在合理范围内 [-1024, 3071]

---

## 10. 里程碑与交付节点

### 10.1 项目时间线

```
Week 1-2:  环境搭建 + 体积I/O + 生成器基类 + 金属伪影
Week 3-4:  运动伪影 + 量子噪声 + 环状伪影
Week 5-6:  条状伪影 + 射束硬化 + 组合生成器 + 测试
───────────────── M1: 伪影生成 v1.0 ─────────────────
Week 7-8:  分类数据集构建 + 模型训练
Week 9-10: 分类模型调优 + 推理接口 + 集成测试
───────────────── M2: 伪影分类 v1.0 ─────────────────
Week 11-12: 传统修复方法
Week 13-15: 深度学习修复模型 + 训练
Week 16-17: 混合修复策略 + 质量评估
───────────────── M3: 伪影修复 v1.0 ─────────────────
Week 18-19: 后端 API 开发 + 数据库集成
Week 20-21: 前端 UI 开发 + 可视化
Week 22:    系统集成 + 端到端测试 + 性能优化
───────────────── M4: 系统集成 v1.0 ─────────────────
Week 23-24: Bug 修复 + 文档完善 + 验收交付
───────────────── M5: 最终交付 ─────────────────────
```

### 10.2 里程碑定义

| 里程碑 | 内容 | 交付物 | 验收标准 |
|--------|------|--------|----------|
| **M1** | 伪影生成 v1.0 | 6种伪影生成器 + 单元测试 | 所有生成器通过单元测试和视觉验证 |
| **M2** | 伪影分类 v1.0 | 分类模型 + 训练脚本 + 推理接口 | 单类准确率>90%，混合检出>80% |
| **M3** | 伪影修复 v1.0 | 传统+深度学习修复方法 | PSNR提升≥3dB, SSIM提升≥0.05 |
| **M4** | 系统集成 v1.0 | 完整的前后端系统 | 所有API正常，前端功能完整 |
| **M5** | 最终交付 | 可部署的完整系统 | 通过集成测试、文档齐全 |

### 10.3 风险与应对

| 风险 | 影响 | 概率 | 应对策略 |
|------|------|------|----------|
| A 组接口变更 | 高 | 中 | 接口版本化，封装适配层，定期与 A 组同步 |
| 深度学习训练数据不足 | 中 | 中 | 使用伪影生成器合成训练数据 + 数据增强 |
| GPU 资源不足 | 中 | 低 | 使用 Google Colab / 校内 GPU 集群 |
| 修复效果不达预期 | 高 | 中 | 备选传统方法，混合策略提高鲁棒性 |
| 集成复杂度高 | 中 | 中 | 尽早进行接口联调，持续集成 CI |

---

## 附录

### A. B 组开发检查清单

#### 环境与基础设施
- [ ] Docker 环境可正常运行 A 组项目
- [ ] Python 依赖安装完成
- [ ] B 组目录结构创建完成
- [ ] Git 分支创建完成

#### 伪影生成
- [ ] 生成器基类实现并通过测试
- [ ] 金属伪影生成器实现并通过测试
- [ ] 运动伪影生成器实现并通过测试
- [ ] 噪声伪影生成器实现并通过测试
- [ ] 环状伪影生成器实现并通过测试
- [ ] 条状伪影生成器实现并通过测试
- [ ] 射束硬化生成器实现并通过测试
- [ ] 组合生成器实现并通过测试
- [ ] 所有生成器视觉验证通过

#### 伪影分类
- [ ] 分类数据集构建完成
- [ ] 分类模型定义完成
- [ ] 训练脚本完成并成功运行
- [ ] 模型评估结果达标
- [ ] 推理接口开发完成
- [ ] 分类 API 开发完成

#### 伪影修复
- [ ] 传统去噪方法实现完成
- [ ] sinogram 域修复方法实现完成
- [ ] 深度学习修复模型定义完成
- [ ] 修复模型训练完成
- [ ] 混合修复策略实现完成
- [ ] 质量评估模块开发完成
- [ ] 修复 API 开发完成

#### 集成
- [ ] 后端 API 路由注册
- [ ] 数据库模型迁移完成
- [ ] 前端类型定义完成
- [ ] 前端 API 服务层完成
- [ ] 前端 UI 组件开发完成
- [ ] 端到端测试通过
- [ ] 文档写作完成

### B. 关键参考文件

- A 组核心仿真 API: [backend/app/api/v1/simulation.py](backend/app/api/v1/simulation.py)
- CT 参数仿真引擎: [backend/app/simulation/ct_params_simulator.py](backend/app/simulation/ct_params_simulator.py)
- CT 幻影生成器: [backend/app/simulation/phantom_generator.py](backend/app/simulation/phantom_generator.py)
- 体积构建器: [backend/app/simulation/volume_builder.py](backend/app/simulation/volume_builder.py)
- 病灶生成器: [backend/app/simulation/lesion/generator.py](backend/app/simulation/lesion/generator.py)
- 仿真 Schema: [backend/app/schemas/simulation.py](backend/app/schemas/simulation.py)
- 仿真 ORM 模型: [backend/app/models/simulation.py](backend/app/models/simulation.py)
- A-B 接口规范: [docs/interface_ct_simulation_to_artifact.md](docs/interface_ct_simulation_to_artifact.md)
- 架构文档: [docs/architecture/system-design.md](docs/architecture/system-design.md)

---

> **文档维护说明**：本文件随开发进展持续更新。每个里程碑完成后，更新对应章节的实现细节和实际效果。
