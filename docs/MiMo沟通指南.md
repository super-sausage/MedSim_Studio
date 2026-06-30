# 与 MiMo Code 沟通实现 B 组开发流程 — 操作指南

> 写给 B 组负责人：如何用 MiMo Code 按文档逐步完成开发

---

## 快速了解 MiMo Code 的关键能力

| 能力 | 对 B 组开发的意义 |
|------|-------------------|
| **1M token 上下文** | 可以直接把 [B组开发流程.md](B组开发流程.md) 全文喂进去，不需要拆分 |
| **视觉模型 (MiMo-V2.5)** | 支持截图反馈——你可以截图让它看伪影效果对不对 |
| **Compose 模式** | 自动把复杂任务拆成 Design→Plan→Code→Test→Review→Merge |
| **多 Agent 并行** | 生成器、分类器、修复器可以并行开发 |
| **Goal 系统** | 设置目标后它会自我验证完成情况 |
| **自动导入 Claude Code 配置** | 当前项目的 .claude/ 配置可直接复用 |

---

## 核心策略：分阶段、给上下文、每次只做一个模块

### 原则

1. **每次对话只做一个模块**——不要一次让它写全部 6 个生成器
2. **先给文档，再给指令**——每次对话开头先贴上文档相关章节
3. **用 Goal 模式**——用 `/goal` 让 MiMo Code 自己验证完成情况
4. **截图验证**——MiMo 支持视觉，生成伪影后截图让它自己检查

---

## 第一阶段：环境搭建（1 次对话）

### Prompt 模板

> 复制下面整段话发给 MiMo Code：

```
我正在开发一个医学影像系统的伪影处理模块（B组）。项目背景文档如下：

<粘贴 docs/B组开发流程.md 的第4章"开发环境搭建流程"全文>

请按照文档中的步骤完成以下工作：

1. 验证当前项目（/Users/wangay/Downloads/MedSim_Studio）的 Docker 环境是否正常
2. 创建 B 组分支 feature/artifact-pipeline
3. 创建 B 组全部目录结构（backend/app/artifact/ 下的所有子目录 + frontend/src/artifact/ + models/artifact/ + tests/backend/artifact/）
4. 创建所有 __init__.py 文件
5. 在 backend/requirements.txt 中追加 B 组所需的 Python 依赖
6. 创建 scripts/verify_b_env.py 环境验证脚本
7. 运行验证脚本确认环境就绪

完成标准：运行 python scripts/verify_b_env.py 全部通过。
```

---

## 第二阶段：伪影生成器（6 次对话，每个生成器 1 次）

### 2.1 生成器基类（第 1 次）

```
我正在开发 CT 伪影生成模块。请阅读以下文档中的生成器基类设计：

<粘贴 B组开发流程.md 第5.3.1节"创建生成器基类">

请创建文件 backend/app/artifact/generator/base.py，实现 BaseArtifactGenerator 抽象基类。

要求：
1. 完全按照文档中的接口定义实现
2. generate() 返回 (artifact_volume, artifact_mask, metadata) 三元组
3. 包含 get_default_params()、validate_params()、get_artifact_type() 三个方法
4. 创建对应的单元测试文件 tests/backend/artifact/test_base.py

完成后自己运行 pytest tests/backend/artifact/test_base.py 验证。
```

### 2.2 金属伪影（第 2 次）

```
请阅读 B组开发流程.md 第5.3.2节"实现金属伪影生成器"。

创建文件 backend/app/artifact/generator/metal_artifact.py，实现 MetalArtifactGenerator 类。

核心要求：
- 继承 BaseArtifactGenerator
- 按文档中的 5 步流程实现（金属掩码 → 条纹 → 射束硬化 → 光子噪声 → HU 设定）
- 支持 titanium/stainless_steel/dental_amalgam/gold/copper 五种金属材质

验证方法：
1. 创建 tests/backend/artifact/test_metal_artifact.py
2. 用 64³ 软组织背景体积测试
3. 确认金属区域 HU > 2500，条纹区域 HU 有变化
4. 输出中间切片 PNG 截图，我会用 MiMo 的视觉能力检查伪影效果

运行 pytest 确认通过后，把中间切片截图发给我看。
```

### 2.3 噪声伪影（第 3 次）

```
请阅读 B组开发流程.md 第5.3.4节"量子噪声伪影生成器"。

创建 backend/app/artifact/generator/noise_artifact.py。

特别注意：
- 不要用简单高斯噪声——要按照文档中的 Poisson 噪声模型实现
- 需要 HU ↔ 线性衰减系数 的双向转换
- 噪声强度应与 sqrt(mAs) 成反比
- 高衰减区域（骨后）噪声应更大

完成后写测试，运行 pytest，截一张中间切片对比图。
```

### 2.4 运动伪影（第 4 次）

```
请阅读 B组开发流程.md 第5.3.3节"运动伪影生成器"。

创建 backend/app/artifact/generator/motion_artifact.py。

支持三种运动类型：
- respiratory（呼吸）：z 方向正弦位移
- cardiac（心跳）：心脏区域局部搏动
- random（随机）：随机突跳

完成后测试 + 截图。
```

### 2.5 环状伪影（第 5 次）

```
请阅读 B组开发流程.md 第5.3.5节"环状伪影生成器"。

创建 backend/app/artifact/generator/ring_artifact.py。

这里的关键技术是 skimage.transform.radon / iradon，在 sinogram 域添加探测器通道偏移。

完成后测试 + 截图。
```

### 2.6 条状伪影 + 射束硬化 + 组合生成器（第 6 次）

```
请阅读 B组开发流程.md 第5.3.6-5.3.9节。

需要创建三个文件：
1. backend/app/artifact/generator/streak_artifact.py（条状伪影）
2. backend/app/artifact/generator/beam_hardening.py（射束硬化）
3. backend/app/artifact/generator/composite.py（组合生成器）
4. 更新 backend/app/artifact/generator/__init__.py（注册表）

组合生成器需要支持：
- 配置多种伪影的叠加顺序
- 输出每种伪影的独立掩码
- 自动处理伪影间的相互作用

完成后对所有生成器跑一次完整测试。
```

---

## 第三阶段：伪影分类（3-4 次对话）

### 3.1 数据集构建 + 模型定义（第 1 次）

```
请阅读 B组开发流程.md 第6章"伪影分类模块"。

创建以下文件：
1. backend/app/artifact/classifier/dataset.py — ArtifactClassificationDataset 类
2. backend/app/artifact/classifier/model.py — ArtifactClassifier (EfficientNet-B3)

数据集类要求：
- 支持多标签分类（8 类）
- 集成 albumentations 数据增强
- 支持从 CT 体积构建数据集（build_dataset_from_volume 函数）

模型要求：
- 使用 timm 加载 EfficientNet-B3 预训练权重
- 多标签输出（Sigmoid 激活）
- 提供 create_classifier() 工厂函数

完成后写单元测试验证数据加载和模型前向传播。
```

### 3.2 训练脚本 + 推理接口（第 2 次）

```
创建以下文件：
1. backend/app/artifact/classifier/train.py — 训练脚本
2. backend/app/artifact/classifier/inference.py — ArtifactInference 推理类

训练脚本要求：
- 使用第一阶段生成的伪影数据构建训练集
- BCEWithLogitsLoss 多标签损失
- Cosine 学习率调度 + warmup
- AMP 混合精度训练
- Early stopping + 模型保存

推理类要求：
- predict_slice(): 单张切片分类
- predict_volume(): 整卷分类（逐层 + 聚合）
- 返回 overall_scores + per_slice_scores + dominant_artifact

完成后跑一次完整训练（epochs 先设 10 做验证），验证 loss 正常下降。
```

### 3.3 模型调优 + 测试（第 3 次）

```
对分类模型进行调优：

1. 训练完整 50 epoch
2. 生成混淆矩阵和 per-class ROC-AUC
3. 用 Grad-CAM 可视化模型关注的区域
4. 测试推理速度（目标 < 50ms/张 GPU）
5. 如果准确率不达标，尝试替换 backbone 为 ConvNeXt-T

验收标准：
- 单类准确率 > 90%
- 混合伪影检出率 > 80%

把训练曲线和混淆矩阵截图发给我。
```

---

## 第四阶段：伪影修复（3-4 次对话）

### 4.1 传统方法（第 1 次）

```
请阅读 B组开发流程.md 第7.3.1节"传统去噪方法"。

创建 backend/app/artifact/restoration/traditional.py，实现 TraditionalRestorer 类，包含：
1. median_denoise() — 中值滤波
2. nlm_denoise() — 非局部均值去噪
3. sinogram_ring_correction() — sinogram 域环状伪影校正
4. mar_sinogram_interpolation() — 金属伪影 sinogram 域插值修复

完成后对每种方法写测试，对比修复前后的 PSNR/SSIM。
```

### 4.2 深度学习模型（第 2 次）

```
请阅读 B组开发流程.md 第7.3.2节"深度学习修复模型"。

创建 backend/app/artifact/restoration/deep_learning.py，实现 REDCNN 模型。

要求：
- 按照文档中的架构图实现（Conv+ReLU → 5×编码器 → 5×解码器 + 跳跃连接 → 残差输出）
- 输入/输出均为 1×H×W 灰度 CT 切片
- 使用第一阶段生成的配对数据 (clean, artifact) 训练

写训练脚本并跑 10 epoch 验证训练流程。
```

### 4.3 混合策略 + 质量评估（第 3 次）

```
创建以下文件：
1. backend/app/artifact/restoration/hybrid.py — 混合修复策略
2. backend/app/artifact/evaluation/metrics.py — 质量评估模块

混合策略要求：
- 根据伪影分类结果自动选择修复方法
- 金属 → sinogram MAR + 图像域修复
- 噪声 → RED-CNN 深度学习
- 运动 → CycleGAN + 配准
- 复合 → 分步处理

质量评估要求：
- PSNR / SSIM / NMSE / MAE(HU) 四个指标
- 支持 2D 切片和 3D 体积输入

完成后跑完整修复流程，输出质量报告。
```

---

## 第五阶段：前后端集成（3-4 次对话）

### 5.1 后端 Schema + 模型 + API（第 1 次）

```
请阅读 B组开发流程.md 第8.1节"后端 API 开发"。

创建以下文件：
1. backend/app/schemas/artifact.py — 全部 Pydantic Schema
2. backend/app/models/artifact.py — ArtifactJob ORM 模型
3. backend/app/api/v1/artifact.py — 全部 API 路由

API 端点包括：
- POST /artifact/generate
- POST /artifact/classify
- POST /artifact/restore
- POST /artifact/pipeline（完整流水线）
- GET /artifact/jobs
- GET /artifact/jobs/{id}
- GET /artifact/types

在 backend/app/main.py 中注册路由。

完成后用 curl 测试每个端点。
```

### 5.2 前端类型 + 服务层（第 2 次）

```
请阅读 B组开发流程.md 第8.2.1-8.2.3节。

创建以下文件：
1. frontend/src/types/artifact.ts — 全部 TypeScript 类型定义
2. frontend/src/services/artifactService.ts — API 服务层
3. frontend/src/store/useArtifactStore.ts — Zustand 状态管理

完成后检查 TypeScript 编译无错误。
```

### 5.3 前端 UI（第 3 次）

```
请阅读 B组开发流程.md 第8.2.4节。

创建以下文件：
1. frontend/src/pages/ArtifactPage.tsx — 伪影处理主页面
2. frontend/src/artifact/ArtifactGenerator.tsx — 伪影生成器 UI
3. frontend/src/artifact/ArtifactClassifier.tsx — 分类结果展示
4. frontend/src/artifact/ArtifactRestoration.tsx — 修复结果展示
5. frontend/src/artifact/ComparisonView.tsx — 对比视图组件
6. frontend/src/artifact/index.ts — 模块导出

UI 布局按照文档中的 ASCII 设计图实现：
- 左侧控制面板（数据源选择 + 伪影选择 + 参数配置 + 操作按钮）
- 右侧可视化区域（CT 切片对比 + 分类结果 + 质量指标）

完成后启动前端 dev server，截图给我看效果。
```

### 5.4 端到端集成 + 路由（第 4 次）

```
完成最后的集成工作：

1. 在 frontend/src/router/ 中注册 /artifact 路由
2. 在导航栏添加"伪影处理"入口
3. 跑一次完整端到端流程：上传 DICOM → 生成伪影 → 分类 → 修复 → 导出
4. 修复所有 TypeScript/ESLint 错误
5. 确保 Docker 部署正常工作

我会截图给你看每一步的效果，你根据截图调整 UI。
```

---

## 使用 MiMo Compose 模式（推荐用于复杂阶段）

对于需要多步骤的阶段（如"前后端集成"），可以用 Compose 模式：

```
/compose 请按照 B组开发流程.md 第8章的要求，完成以下完整流水线：

Design: 设计 artifact API 的数据模型和接口规范
Plan: 规划前后端开发顺序和文件依赖关系
Code: 依次实现 Schema → Model → API → TypeScript → Service → Store → UI
Test: 端到端测试完整流水线
Review: 检查代码质量、类型安全、安全性
Merge: 合并到 feature/artifact-pipeline 分支
```

---

## 利用 MiMo 视觉模型做伪影效果验证

这是 MiMo 相比纯文本工具的最大优势。每个生成器开发完成后：

### 操作步骤

```bash
# 1. 让代码输出中间切片 PNG
python -c "
from app.artifact.generator.metal_artifact import MetalArtifactGenerator
import numpy as np
from PIL import Image

gen = MetalArtifactGenerator(seed=42)
vol = np.ones((64, 64, 64), dtype=np.float32) * 40
result, mask, meta = gen.generate(vol, (1,1,1), gen.get_default_params())

# 窗口化
slice_img = result[32]
wl, ww = 40, 400
slice_img = np.clip((slice_img - (wl - ww/2)) / ww * 255, 0, 255).astype(np.uint8)
Image.fromarray(slice_img).save('/tmp/metal_artifact_preview.png')
print('Saved to /tmp/metal_artifact_preview.png')
"
```

### 然后发给 MiMo

```
请查看这张金属伪影的轴向切片截图 /tmp/metal_artifact_preview.png：

1. 金属区域（中心白色）是否正确设置为高 HU？
2. 条纹伪影（放射状暗带）是否从金属中心向外延伸？
3. 射束硬化效应（金属周围组织变暗）是否可见？
4. 伪影视觉效果是否接近真实 CT 金属伪影？

如果有问题，请提出具体的参数调整建议。
```

---

## 推荐的对话顺序总览

| 序号 | 对话内容 | 预计耗时 | 使用模式 |
|------|----------|----------|----------|
| 1 | 环境搭建 | 30 分钟 | 普通对话 |
| 2 | 生成器基类 | 20 分钟 | 普通对话 |
| 3 | 金属伪影生成器 | 1 小时 | 普通 + 视觉验证 |
| 4 | 噪声伪影生成器 | 1 小时 | 普通 + 视觉验证 |
| 5 | 运动伪影生成器 | 1 小时 | 普通 + 视觉验证 |
| 6 | 环状伪影生成器 | 1 小时 | 普通 + 视觉验证 |
| 7 | 条状+射束硬化+组合 | 1.5 小时 | 普通 + 视觉验证 |
| 8 | 分类数据集+模型 | 1 小时 | 普通 |
| 9 | 训练+推理 | 2 小时 | 普通（训练需等待） |
| 10 | 分类调优 | 1 小时 | 普通 |
| 11 | 传统修复 | 1 小时 | 普通 + 视觉验证 |
| 12 | 深度学习修复 | 2 小时 | 普通（训练需等待） |
| 13 | 混合策略+评估 | 1 小时 | 普通 |
| 14 | 后端 API | 1.5 小时 | 普通 |
| 15 | 前端类型+服务 | 1 小时 | 普通 |
| 16 | 前端 UI | 3 小时 | Compose 模式 |
| 17 | 端到端集成 | 1.5 小时 | Compose 模式 |

**总计约 18 次对话，预计 20-25 小时**（不含模型训练等待时间）。

---

## 踩坑提醒（基于 MiMo Code V0.1.0 的已知问题）

1. **不要一次让它写太多文件**——社区反馈 MiMo 在长任务中有时会"思维螺旋"。一次 1-3 个文件最稳定。

2. **遇到 bug 先让它自己修**——用 `/goal` 模式，MiMo 的 judge 模型会自我验证。如果它声称完成了但实际有问题，直接说"测试未通过，请检查"。

3. **内存泄漏问题**——如果 MiMo 运行超过 2 小时变慢，重启一下。

4. **不要让它做全局操作**——社区报告过 Agent 误删文件的情况。重要文件先 commit。

5. **compose 模式适合有清晰步骤的任务**——如果某个阶段文档已经写清楚了步骤，用 Compose；如果还在探索，用普通对话。

6. **视觉验证前先 git commit**——MiMo 看到截图后可能会大幅改代码，先保存当前版本。
