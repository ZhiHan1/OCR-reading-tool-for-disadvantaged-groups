# 前端（WEB）

> 域编号：`KB-WEB-*`。位置：`qt_web/web/`。

## 1. 技术选择

- 原生 JS（传统 `<script>`），**不用 ES module**：页面用 `file://` 加载，
  ES module 会被 CORS 拦截。
- 全部挂到单一全局 `window.APP`，避免污染。
- 动画优先用 Web Animations API（`el.animate`），不引第三方库。
- 样式按层拆分：tokens → layout → components → panels → light-overrides → pages。

## 2. 文件清单与加载顺序（KB-WEB-001）

```
index.html
├─ css/tokens.css          设计令牌（颜色/间距/时长）
├─ css/layout.css          骨架：sidebar / main / topbar / content
├─ css/components.css      按钮、卡片、开关、标签页
├─ css/panels.css          面板、设置页、结果区
├─ css/light-overrides.css 亮色主题覆盖
├─ css/pages.css           单页专用样式
├─ js/vendor/qwebchannel.js  Qt 官方通道客户端（无版本号）
└─ js/ 18 个业务脚本（全部带 ?v=YYYYMMDDxx）
     core/  namespace → dom → bridge → theme → keys → modal
     ui/    components → sidebar → topbar → result
     pages/ home → translate → tts → voice → settings → about
     最后   router → app
```

**顺序不可随意调整**：每个文件在定义时读取 `window.APP`，依赖前序文件已挂载。

## 3. 页面注册与侧边栏（KB-WEB-002）

```js
A.pages.voice_input = {
  title: "语音输入", desc: "...", icon: "🎤", group: "处理",
  render() { return htmlString; },   // 返回 innerHTML
  mount(root) { /* 绑定事件 */ },     // 可选
};
```

- 分组顺序写死在 `namespace.js` 的 `APP.navGroups = ["识别", "处理", "系统"]`。
- `ui/sidebar.js` 遍历 `APP.pages` 自动生成导航，**新增页面不需要改 HTML**。
- 现有页面：home(识别) / translate、tts、voice_input(处理) / settings、about(系统)。

**副作用边界**：路由每次切页都会 `render` + `mount` 重建 DOM，
所以事件监听必须用 `A.bridge.beginScope()` 注册，切页时统一释放；
否则反复进出页面会累积 handler，一次事件触发多次。

## 4. bridge：唯一通道（KB-WEB-003）

```js
A.bridge.call(action, payload)   // → Promise，失败 reject
A.bridge.on(event, fn)           // 订阅后端推送
const release = A.bridge.beginScope();  // 页面 mount 开始
release();                              // 页面卸载结束
```

要点：

- `call` 默认 20 秒超时；`pick_folder` / `pick_file` / `export_config` /
  无路径的 `asr_import_model` **不设超时**（等用户选目录可能很久）。
- 没有 QWebChannel 时（浏览器直接打开页面）自动降级为 mock，只用于调样式。
- `emit` 对单个 handler 的异常做 try/catch，一个出错不影响其它订阅者。
- 后端推送统一是 `{type, ...}`，由 `bridge` 分发成事件名。

## 5. 状态同步（KB-WEB-004）

```
后端 _build_state()  ──do_state()──▶  app.js::applyState()  ──▶  APP.state
                                                    │
页面 render() 读取 APP.state 生成 HTML              │
后续变更走后端推送事件（status / result / scan_state / region …）就地更新 DOM
```

**单一真相源在后端**。前端 `APP.state` 只是展示缓存，不参与持久化决策。
`APP.state` 初值在 `namespace.js` 给足默认键（theme、frost、enabled、regions、
running、hotkeys、asr、translation、voices、models、recognition、ocr、queueModes），
保证首帧不会读到 undefined。

## 6. 路由过渡动画（KB-WEB-005）

`js/router.js` 注释里写的设计意图，实现与之一致：

**退场 = 登场的严格反转**

- 登场向下弹的卡片（普通卡片，按视觉行分组），退场时**逐行反向**往上收（dy=-26）。
- 登场向上弹的卡片（`enter-up`：结果区、输出区、配置底栏），退场时**先**往下收（dy=+26）。
- 时长 200ms，交错 25ms，缓动 `cubic-bezier(.4,0,.7,.2)`。
- 结束前必须 `content.getAnimations().forEach(a => a.cancel())` 并清 `opacity`，
  否则 `fill: forwards` 会让新页面继承透明。

**登场 = 两阶段**

1. 普通卡片按行自上而下向下弹入：240ms / 行间交错 45ms / `cubic-bezier(.2,.8,.2,1)`。
2. 等第一阶段末行结束后，`enter-up` 卡片再自下往上弹入：250ms。
3. `prefers-reduced-motion` 时全部跳过（`reduceMotion()`）。

时长依据：Material / Framer Motion 的常见区间（单元素 200~250ms、交错 40~50ms）。

## 7. 主题扩散与设置页切换（KB-WEB-006）

**主题圆形扩散**（`js/core/theme.js::animateTransition`）

- 从点击坐标 `(x, y)` 创建全屏遮罩层，`clip-path: circle(0% → 150%)`，760ms。
- 遮罩层插在 `.app` **之前**，保证卡片文字始终在上层可见。
- **立即**切换主题，不等动画中段：否则前半段会闪一下旧色。
- 遮罩使用目标主题的固定渐变色值，而不是 CSS 变量（变量在切换后会被覆盖）。

**设置页标签逐级滚动**（`js/pages/settings.js::showTab`）

- `track` 里**永远按阅读顺序**铺 pane：`TABS[min(old,new) … max(old,new)]`，
  中间页真实掠过而不是留白。
- 视口位置**只由 `translateX` 决定**：`from = -fromIdx*100%`（旧页所在格）、
  `to = -toIdx*100%`（目标页所在格），`fromIdx = old-lo`、`toIdx = new-lo`。
- 中间页加 `blur(2px)` + `opacity 0.8`，首尾（`fromIdx` / `toIdx`）保持清晰。
- 每个索引差一个视口宽，`duration = 260 + dist*70` ms。

> **KB-WEB-009 向左切换闪旧页（2026-11-05 已修）**
> 旧实现按 `oldIdx → newIdx` 的**步进方向**铺 pane：向左时轨道变成 `[新, 旧]`，
> 但起始 `translateX` 让视口停在第 0 格（= 新页），于是动画一开始就直接显示目标页，
> 结束时 `onfinish` 又换成旧页 —— 用户看到「到位后先闪一下之前选中的页面」。
> 修复原则：**轨道 pane 按阅读顺序铺，视口位置只由 `translateX` 控制**。
> 另：`anim.onfinish` 里必须**先提交 DOM（只留目标 pane + 归零）再 `anim.cancel()`**，
> 反过来会露出「旧页归零」的一帧。
> 回归验证：`TABS` 首尾与相邻任意方向切换，`start.visible` 必须是旧页、
> `end.visible/active` 必须是目标页（见 gotchas.md §5.1）。

## 8. 版本号与缓存（KB-WEB-007）

`index.html` 中全部 JS/CSS 共享同一个版本号（当前 `?v=20261105c`）。
QWebEngine 对本地文件同样有缓存，**不改版本号 = 用户看到的还是旧代码**。

- 修改任何 JS/CSS 后必须 bump，例如 `20261105b → 20261105c`。
- **必须全文件统一**：24 个引用一起换，不要只改被改动的那个文件
  （2026-11-05 曾只改 `layout.css`，与其他 23 处不一致，随后统一回同一值）。
- 批量替换必须用字节级写法（见 KB-GOT-002），不要用 PowerShell 管道写回。
- (`js/vendor/qwebchannel.js` 无版本号，属特例，不要给它加。)

## 9. 已知交互约定

| 约定 | 位置 | 说明 |
| --- | --- | --- |
| 识别设置默认展开并贴底 | `pages/voice.js::mount` | `setSettingsOpen(true)`；标题仍可点击收起，末位卡片 |
| 识别三模式横排 | `pages/voice.js::INPUT_MODES` | manual / realtime / auto |
| 闲置退出下拉固定档位 | `pages/voice.js::IDLE_EXIT_OPTIONS` | 1/2/5/10/30 分钟 + **常驻放最后**（值 0），默认 5 分钟 |
| 麦克风下拉在「模型」组首行 | `pages/voice.js` + `api.py::_input_devices` | 启动时枚举一次并缓存；「刷新」再次枚举；空值 = 跟随系统默认 |
| 配置管理常驻设置页底部 | `pages/settings.js` | 独立 `card`，`cls: 'config-footer enter-up'`，只在该页渲染，切标签不销毁 |
| 快捷键录制期间暂停全局热键 | `keys.js` + `main.py::pause_hotkeys` | 避免按下的键被系统热键抢走 |
| 语音识别结果框**默认只读**，点「✏️ 编辑」才可改 | `pages/voice.js::setEditing` | 见 §11（KB-WEB-010） |

## 10. 文本可选性：只有文本框可复制（KB-WEB-008）

产品要求：界面文字（标题、说明、按钮、状态）**不能**被拖蓝选中复制，
只有单行输入框和多行文本框里的内容可以选中/复制。

实现放在 `css/layout.css` 末尾（纯 CSS，不改 JS）：

```css
html, body, body * { -webkit-user-select: none; -moz-user-select: none; user-select: none; }

body input,
body textarea,
body [contenteditable="true"],
body [contenteditable=""],
body .selectable { -webkit-user-select: text; -moz-user-select: text; user-select: text; }
```

要点：

- 例外规则用 `body input`（特异度 0,0,2）压过 `body *`（0,0,1），
  不依赖源码顺序，后续插入样式也不会失效。
- 覆盖到的可复制控件：`#asr-output`、`#result-text`、`#tr-input/#tr-output`、
  `#tts-text`、设置页所有 `input`（含 API Key / 模型路径）、`#om-body`。
- `select` 保持 `user-select: none`：下拉选项本身由系统弹层渲染，不受影响。
- 正文按 Ctrl+A 也选不中（实测 `getSelection().toString().length === 0`），
  所以 `Ctrl+A → Ctrl+C` 拿不到界面文字。
- 新增“允许复制”的纯文本区域时，给它加 `class="selectable"` 即可，
  不要放宽 `body *`。
- 验证方法：`getComputedStyle(el).userSelect` 应为 `none` / `text`；
  再对 6 个页面全量扫描，`user-select:text` 的元素必须只有 `input` / `textarea`。

## 11. 语音识别结果框默认只读（KB-WEB-010）

**需求**（2026-11-15）：用户反馈语音输入页的识别结果框不该能编辑，只能复制。

**但不能直接改成永久只读**：该框可编辑是为了配合「保存修改」——它对比
「模型原文」与「用户改后的文本」提取差异，写进个人词库候选
（`hotwords_learn`）。永久只读会丢掉这个学习能力。

**方案：默认只读 + 「✏️ 编辑」按钮临时解锁。**

| 状态 | `readOnly` | 光标 | 类名 | 按钮文案 |
| --- | --- | --- | --- | --- |
| 默认 | `true` | `default` | `ta` | ✏️ 编辑 |
| 编辑中 | `false` | `text` | `ta editing` | 🔒 完成编辑 |

实现要点：

1. `<textarea readonly>` 写在 HTML 里，JS 初始化**必须静默**
   （`setEditing(false, true)`），否则会覆盖页面下方的「就绪 · 松开自动识别」。
2. 编辑中收到新的识别结果时，**不要更新 `lastRaw`**（diff 基准），
   否则用户已做的纠错被冲掉、学不到。追加内容的行为保持不变。
3. 只读状态下点「保存修改」，提示引导去点「编辑」，而不是干说「未修改」。
4. 复制不受影响：复制走 `copy_text`（Python 剪贴板），不依赖可编辑。

守门测试：`tests/test_voice_output_readonly.py`（11 项）。
真实 GUI 探针：`tools/probe_voice_readonly.py`。

### 11.1 写前端 DOM 探针脚本的四个坑（实测踩过）

排查这个需求时，探针脚本连续失败多次，根因都不在业务代码。写同类脚本前先看这四条：

| 坑 | 现象 | 正确做法 |
| --- | --- | --- |
| **`A` 不是全局变量** | JS 里 `A.router` 报未定义或返回空 | 全局只有 `window.APP`，`A` 是各模块内部的局部别名（`core/namespace.js`）。JS 开头写 `const A = window.APP;`——`tools/smoke_gui.py` 就是这么做的 |
| **`router.go()` 异步提交** | 导航后立刻查 DOM 拿到旧页面 | 传 `{animate:false}` + `router.refresh()`，且**导航与断言分两次 `runJavaScript`**（KB-PERF-009） |
| **`loadFinished` 会错过** | 连信号后回调永不触发 | `WebMainWindow` 在**构造函数里**就 `load()` 了，等我们连信号时加载早已开始。改成轮询 `window.APP.router` 是否存在 |
| **同一同步块里连点两次** | 读不到中间态（class 没变） | 每次点击单独一次 `runJavaScript`，中间 `processEvents()` + 短 sleep |

另外两条通用约定：

- **返回值统一 `JSON.stringify` 包一层**。直接 `return {a:1}` 在 WebChannel 下
  可能拿到 `null`，看不出是 JS 报错还是序列化失败。
- 用 `processEvents()` 同步等回调（沿用 `tools/smoke_gui.py` 的写法），
  比嵌套 `QTimer` 回调链好调试。

> ⚠️ 教训：探针失败时**先怀疑探针本身**，不要急着改业务代码。
> 这次业务代码一次就写对了（`readOnly` / `editing` 类 / 按钮文案全对），
> 是探针的写法问题浪费了时间。
