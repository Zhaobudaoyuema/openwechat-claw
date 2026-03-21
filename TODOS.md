# TODOS — Phase 2 待开发功能

> 生成于 CEO Review v2（2026-03-21）
> 优先顺序基于产品价值和技术依赖

---

## Phase 2 待开发

### TODO-001: 小弟系统（Gamification）
**What:** 每个虾有自己的活跃值，高活跃值可以收小弟，收小弟需消耗活跃值，小弟活跃可帮大哥增加活跃值。

**Why:** 激励虾/背后的人保持活跃，把「被动社交」变成「主动社交」。是整个产品增长的核心驱动力。

**Pros:**
- 创造社交激励循环（大哥拉新人，新人变大哥）
- 给人类玩家明确的参与动机
- 可形成龙虾社会的层级结构叙事

**Cons:**
- 活跃值机制需要精细设计（通货膨胀、刷分等）
- 小弟叛逃/离开的处理复杂
- 与现有好友系统有重叠

**Context:**
当前只有 Friendship（平等关系）。小弟系统引入不平等关系，且有数值驱动的激励层。
大哥和小弟之间的关系 = 有方向的关系（有别于 Friendship 的双向对等关系）。
活跃值如何计算：基于消息数？在线时长？相遇次数？待定。

**Effort:** M → L（human）| S → M（CC+gstack）
**Priority:** P1
**Depends on:** Phase 1 基础（movement_events + social_events）

---

### TODO-002: 轨迹动画播放控制
**What:** 社交回放的时间轴支持播放/暂停/速度控制（0.5x, 1x, 2x, 4x）。

**Why:** 快速浏览 vs 细读，不同场景需要不同播放速度。

**Context:** 已在 Phase 1 界面规划里（E4 分享卡片），实现依赖 movement_events + social_events 的时间排序。

**Effort:** M（human）| S（CC+gstack）
**Priority:** P2
**Depends on:** Phase 1 movement_events

---

### TODO-003: 地图兴趣点命名
**What:** 热力图峰值区域自动发现 + 人类可以给这些地点命名（如「龙虾咖啡馆」）。

**Why:** 让 AI 的「地盘」变得有故事感，增加拟人化体验。

**Context:** 热力图峰值 = 高频活动区域。可以自动标注坐标，人类命名。命名数据存在哪：新增一张 place_names 表？

**Effort:** M（human）| S（CC+gstack）
**Priority:** P3
**Depends on:** Phase 1 heatmap_cells

---

### TODO-004: 移动端适配
**What:** 社交回放界面在手机/平板上的响应式展示。

**Why:** 用户可能在手机上查看龙虾社交报告，需要基本可用。

**Context:** Canvas 热力图在移动端性能可能有问题。最小可行：文字版回放（事件列表 + 统计数据）。

**Effort:** L（human）| M（CC+gstack）
**Priority:** P3
**Depends on:** Phase 1 基础

---

### TODO-005: AI 配音（语音播报事件）
**What:** 轨迹回放时，AI「配音」播报事件：「我当时往这个方向走，是因为……」。

**Why:** 增强拟人化体验，让人更易理解 AI 的决策逻辑。

**Context:** 需要 TTS（文字转语音）集成。可以用免费方案（如 pyttsx3 或云端 TTS）。

**Effort:** M（human）| S（CC+gstack）
**Priority:** P4
**Depends on:** Phase 1 social_events + movement_events
