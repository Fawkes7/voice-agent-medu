# Prompt / 模型 / ASR·TTS 配置说明

本文档说明：**医生与病人 prompt 定义位置**、**inline / overall judge prompt**、**会话如何结束与任务是否完成**、**CT 字段含义**、**各组件用的模型**、以及 **ASR / TTS**。

> 仓库存在两条尚未完全合流的配置线，读文档时请先对号：
>
> | 线 | 典型分支 / 入口 | LLM | 语音 |
> |---|---|---|---|
> | **A. 研究主线（含情绪实验）** | `main`、`cursor/emotion-schema-experiment-*` | 固定 **DashScope Qwen**（`qwen-max` + omni） | **无** ASR/TTS |
> | **B. 语音 / 多厂商 LLM 线** | `origin/master` | `LLM_PROVIDER` 可切 **openai / gemini / qwen**；`.env.example` 默认 **gemini** | **ElevenLabs** ASR + TTS |
>
> - 仓库内**从未出现过 Kimi / Moonshot**。
> - 「我现在改成了 Gemini 吗？」→ 若你跑的是 **`origin/master` 且 `.env` 里 `LLM_PROVIDER=gemini`**：是，默认模型为 **`gemini-2.5-flash`**。若你跑的是 **`main` / 情绪实验分支**：仍是 **`qwen-max`**，没有 Gemini 开关。

---

## 0. 角色澄清（先读这一节）

系统里**没有「医生 AI」对话 agent**。问诊里的「医生」是**人类医学生**。

| 产品角色 | 实际是谁 | 消息 `role` | Prompt key |
|---|---|---|---|
| 医生 / 医学生 | 人类学员 | `student` | **无对话 system prompt** |
| 病人 / AI-SP | 标准化病人 LLM | `patient` | `sp_agent` |
| Tutor | 苏格拉底式辅导 LLM（仅 MA） | `tutor` | `tutor_agent` |
| Inline judge | 每轮静默 checklist 评判（仅 MA） | 无聊天气泡 | `turn_evaluator` |
| Overall judge | 会话结束整体 OSCE 评判（MA / Exam） | 无聊天气泡 | `final_evaluator` |

另有（仅 A 线）：`doctor_attitude` 用学生这句话分析态度，驱动 SP 情绪；**不是**医生对话 prompt。

---

## 1. Prompt 定义位置与完成判定

### 1.1 中央 Prompt Registry（YAML + DB）

默认模板在：

```
backend/app/prompts/
  sp_agent.yaml          # 病人 AI-SP
  tutor_agent.yaml       # Tutor（MA）
  turn_evaluator.yaml    # inline / 每轮 judge（MA）
  final_evaluator.yaml   # overall / 整场 judge（MA + Exam）
```

加载逻辑：`backend/app/prompts/__init__.py` 的 `PromptRegistry`

1. 启动时读 YAML → 内存缓存  
2. `seed_db_from_yaml`：DB 无 active 行则写入；YAML version 升级则换新 active  
3. 管理员可在 `/admin/prompts` 改版；会话首次学生消息会快照 `prompt_versions_json`

业务代码统一：`PromptRegistry.get("sp_agent")` 等。

| Method | 用到的 prompt |
|---|---|
| SA（single_agent） | `sp_agent` |
| MA（multi_agent） | `sp_agent` + `tutor_agent` + `turn_evaluator` + 结束时 `final_evaluator` |
| Exam | `sp_agent` + 结束时 `final_evaluator` |
| CT（control） | **不用 LLM prompt**（见 §1.5） |

### 1.2 病人（SP）prompt

- **定义**：`backend/app/prompts/sp_agent.yaml`  
- **拼装**：`backend/app/agents/sp_agent.py` → `build_sp_system_prompt()`  
- **病例注入**：`backend/app/cases/*.yaml` 的 `patient_profile` / `information_layers` / `emotional_model` / `pathophysiology`

占位符（运行时填入）：`{name}` `{age}` `{gender}` `{occupation}` `{personality}` `{appearance}` `{pathophysiology}` `{info_summary}` `{emotion_state}` `{emo_empathetic}` `{emo_cold}` `{emo_rushing}`。

核心规则：信息分层（主动说 / 问到才说 / 深入追问才说）+ 情绪语气。

### 1.3 「医生」prompt

- **对话**：无。学生说话即医生发言。  
- **A 线附加**：`backend/app/agents/doctor_attitude.py` 内联 `DOCTOR_ATTITUDE_SYSTEM_PROMPT`，分析学生本轮态度 → SP `emotion_state`。不进 Prompt Registry。

### 1.4 Inline judge vs Overall judge

| 名称 | Prompt | 代码入口 | 何时跑 | 输出要点 |
|---|---|---|---|---|
| **Inline judge**（Turn Evaluator） | `turn_evaluator.yaml` | `evaluator_agent.evaluate_exchange` | MA **每一轮**学生↔病人后 | `checked_items` → 更新 checklist |
| **Tutor**（不是 judge，但是 inline 旁路） | `tutor_agent.yaml` | `tutor_agent.evaluate_need_for_intervention` | MA 每轮（有冷却/上限） | `should_intervene` / `hint_content` |
| **Overall judge**（Final Evaluator） | `final_evaluator.yaml` | `final_evaluator.evaluate_exam` | MA / Exam **`end_session`** | checklist 整评 + 4 维 OSCE + 诊断对错 + 叙述反馈 |

评分细则注入：

- Checklist：`backend/app/evaluation/rubrics.yaml` → `{checklist_items}`  
- 整体量规：`backend/app/evaluation/holistic_rubric.yaml` → `{holistic_rubric}`

### 1.5 会话如何结束？「某项任务」怎样算完成？

**对话类（SA / MA / Exam）没有自动「任务完成」检测。**

1. 前端显式发 WebSocket `{ "type": "end_session" }`（见 `frontend` 的 WS hook；后端 `backend/app/main.py` 处理）。  
2. 仅断开连接**不会**结束会话、也**不会**跑 final evaluator。  
3. `end_session` 会写 `training_sessions.ended_at`；MA / Exam 再调 `evaluate_exam`。  
4. Inline checklist「打勾」≠ 会话结束：只表示该 history-taking 项本轮被判定覆盖。

**CT（对照组）** 是固定 4 阶段书面任务，无 LLM：

| stage_index | 标题（`stage_title` / API `title`） | 学生要做什么 | 结束条件 |
|---|---|---|---|
| 0 | 阶段 1 · 接诊：主诉与基本情况 | 写接下来想问的 5 个问题 | 提交后进下一阶段 |
| 1 | 阶段 2 · 系统询问结果 | 写鉴别方向 + 3 个追问 | 同上 |
| 2 | 阶段 3 · 深入追问结果 | 写主诊断 / 依据 / 处置 | 同上 |
| 3 | 阶段 4 · 答案揭示与教学要点 | 只读，无输入 | 提交后 `completed: true` → `end_session` |

实现：`backend/app/sessions/control.py` 的 `CT_STAGE_TITLES` / `CT_PROMPTS` / `submit_stage`。  
**没有**系统自动批改「写得对不对」；完成 = 走完阶段并落库。

### 1.6 `prompt_to_student` / `stage_title` / `disclosed_content` 是什么？

**仅 CT 使用**，与 SP/Tutor/Evaluator YAML **无关**。

| 字段 | 含义 | 从哪来 | 用在哪 |
|---|---|---|---|
| **`disclosed_content`** | 本阶段**展示给学生看的病历文本**（渐进披露） | 由 case YAML 渲染：`_render_voluntary` / `_render_layer` / `_render_reveal` | 前端 `ControlLearning.tsx` 正文；落库 `ct_steps.disclosed_content` |
| **`prompt_to_student`** | 本阶段**要求学生写下什么**的题目文案；阶段 4 为 `null` | `CT_PROMPTS[i]`（写死在 `control.py`） | 输入框上方说明；`ct_steps.prompt_to_student` |
| **`stage_title`** | 阶段人类可读标题 | `CT_STAGE_TITLES`；API 载荷字段名是 **`title`**，入库列名是 **`stage_title`** | UI 标题；分析导出 |

模型定义：`backend/app/models/ct_step.py`。

直观理解：

- `disclosed_content` = 「系统这一关给你看的病人信息」  
- `prompt_to_student` = 「请你根据上面信息完成的作业题」  
- `stage_title` = 「这一关叫什么」

---

## 2. 每一项用的模型是什么？

### 2.1 A 线（`main` / 情绪实验分支）— 当前此仓库检出常见状态

配置：`backend/app/config.py` + `backend/.env.example`

| 组件 | 模型 | 配置键 | 调用 |
|---|---|---|---|
| SP / Tutor / Turn Evaluator / Final Evaluator | **`qwen-max`** | `QWEN_MODEL`（默认 `qwen-max`） | `call_qwen` → DashScope `Generation` |
| **情绪检测（Doctor attitude → SP emotion）** | **`qwen3.8-omni-flash`** | `QWEN_OMNI_MODEL` | 见下节 |
| CT | 无 LLM | — | — |

需要：`DASHSCOPE_API_KEY`（放在 **`backend/.env`**，不要写进 `.env.example` 或 git）。

**历史**：从初始提交起对话 LLM 就是 **Qwen `qwen-max` via DashScope**；后来才加 omni 做态度检测。不是从 Kimi 迁过来的。

### 2.2 情绪检测模型（单独拆开）

问诊里有两段 LLM，**不要混成同一个模型**：

| 步骤 | 做什么 | 模型 | 配置 |
|---|---|---|---|
| ① 情绪 / 态度检测 | 读**学生（医生）本轮话**，判 SP 该切到 `baseline` / `empathetic` / `cold` / `rushing` | **`qwen3.8-omni-flash`** | `QWEN_OMNI_MODEL`；开关 `DOCTOR_ATTITUDE_USE_LLM`（默认 `true`） |
| ② 病人回复 | 用更新后的 `{emotion_state}` 生成 SP 回答 | **`qwen-max`** | `QWEN_MODEL` |

细节：

- **代码**：`backend/app/agents/doctor_attitude.py` → `detect_sp_emotion_shift`  
- **谁调用**：`sp_agent.generate_sp_response` 在拼 SP prompt / 调 `call_qwen` **之前**先跑检测  
- **Prompt**：内联 `DOCTOR_ATTITUDE_SYSTEM_PROMPT`（不在 `backend/app/prompts/*.yaml`）  
- **文本路径（当前默认）**：`call_qwen(..., model=settings.QWEN_OMNI_MODEL)` → DashScope `Generation`  
- **可选音频路径**：若传入 `doctor_audio_wav`，走 `dashscope.MultiModalConversation`，模型仍是 **`QWEN_OMNI_MODEL`**（同为 `qwen3.8-omni-flash`）  
- **降级**：`DOCTOR_ATTITUDE_USE_LLM=false`、无 key、或 LLM 失败 → `detect_emotion_shift_regex`（规则匹配，不调模型）  
- **Gemini / master 线**：当前 **没有** `doctor_attitude`；换 `LLM_PROVIDER=gemini` **不会**自动改情绪检测模型（那条线根本没有这一步）

`.env` 相关项：

```bash
QWEN_MODEL=qwen-max                 # SP / tutor / judges
QWEN_OMNI_MODEL=qwen3.8-omni-flash  # 情绪检测专用
DOCTOR_ATTITUDE_USE_LLM=true
DASHSCOPE_API_KEY=...
```

### 2.3 B 线（`origin/master`）— Gemini / 多厂商

配置概念：`LLM_PROVIDER` + 可选 `LLM_MODEL`；统一 `call_llm`（OpenAI-compatible）。

| `LLM_PROVIDER` | 默认模型（`LLM_MODEL` 空时） | Key |
|---|---|---|
| `openai` | `gpt-4.1` | `OPENAI_API_KEY` |
| **`gemini`（`.env.example` 默认）** | **`gemini-2.5-flash`** | `GEMINI_API_KEY` |
| `qwen` | `qwen-max`（还可回退旧名 `QWEN_MODEL`） | `DASHSCOPE_API_KEY` |

| 组件 | 用哪个模型 |
|---|---|
| SP / Tutor / Turn Evaluator / Final Evaluator | **同一个** `settings.llm_model`（由 provider 决定） |
| Doctor attitude / 情绪检测 | **master 线当前没有该 agent**（无 `QWEN_OMNI_MODEL` 路径） |
| CT | 无 LLM |

因此：

- **原来（A 线 / 早期主线）**：对话全是 **`qwen-max`**；情绪检测后加为 **`qwen3.8-omni-flash`**。  
- **你在 master 上的示例配置**：`LLM_PROVIDER=gemini` → SP/Tutor/Judges 默认变成 **`gemini-2.5-flash`**；**仍无情绪检测模型**。  
- **不是 Kimi**。

代码默认值注意：`config.py` 里 `LLM_PROVIDER` 字段默认字符串可能是 `"qwen"`，但 **`.env.example` 写的是 `gemini`**——以你实际加载的 `.env` 为准。

---

## 3. ASR / TTS 分别是什么？

### 3.1 A 线（`main` / 情绪实验）

| 方向 | 实现 |
|---|---|
| ASR | **无**（文本输入） |
| TTS | **无**（文本气泡） |

情绪检测的 Omni **可选音频输入**（`doctor_audio_wav` → `MultiModalConversation`）是态度分析的多模态路径，**不是**产品 ASR；产品问诊输入仍是文本。

### 3.2 B 线（`origin/master`）— 语音问诊（主要用于 MA）

| 方向 | 厂商 | 模型 / 产品名 | 配置 / 代码 |
|---|---|---|---|
| **ASR（STT）** | ElevenLabs | **`scribe_v2_realtime`** | `ELEVENLABS_STT_MODEL`；浏览器拿 short-lived token（`/api/voice/scribe-token`）实时识别；语言默认 `zh` |
| **TTS** | ElevenLabs | **`eleven_flash_v2_5`** | `ELEVENLABS_TTS_MODEL`；服务端 `/api/voice/tts` 流式合成；`language_code=zh` |
| 病人音色 | ElevenLabs Voice ID | 默认 `EXAVITQu4vr4xnSDxMaL` | `ELEVENLABS_VOICE_ID_PATIENT` |
| Tutor 音色 | ElevenLabs Voice ID | 默认 `21m00Tcm4TlvDq8ikWAM` | `ELEVENLABS_VOICE_ID_TUTOR` |

关键文件：

- 后端：`backend/app/voice/elevenlabs.py`、`backend/app/api/voice.py`  
- 前端：`frontend/src/hooks/useScribeRealtime.ts`、`useFlashTts.ts`、`components/VoiceMicBar.tsx`

需要：`ELEVENLABS_API_KEY`。未配置时 voice config 返回 `enabled: false`，退回纯文本。

**未使用**：FunASR、Whisper、DashScope ASR、CosyVoice、浏览器 `speechSynthesis` 作为产品链路。

---

## 4. 一张总表（按「你现在想查什么」）

| 问题 | 答案 |
|---|---|
| 病人 prompt 在哪？ | `backend/app/prompts/sp_agent.yaml`（+ case YAML 注入） |
| 医生对话 prompt 在哪？ | **没有**；学生 = 医生 |
| Inline judge prompt？ | `turn_evaluator.yaml` → MA 每轮 |
| Overall judge prompt？ | `final_evaluator.yaml` → `end_session` |
| 怎么判断会话结束？ | 前端发 `end_session`；CT 则 4 阶段提交完 |
| checklist 某项完成？ | Turn evaluator 返回 `checked_items`（≠ 会话结束） |
| `disclosed_content`？ | CT 本阶段披露的病人信息文本 |
| `prompt_to_student`？ | CT 本阶段给学生的书面任务题 |
| `stage_title`？ | CT 阶段标题 |
| 原来对话模型？ | **`qwen-max`（DashScope）**；无 Kimi |
| **情绪检测模型？** | **`qwen3.8-omni-flash`**（`QWEN_OMNI_MODEL`）；与 SP 的 `qwen-max` 分开；开关 `DOCTOR_ATTITUDE_USE_LLM` |
| 改成 Gemini 了吗？ | **仅当跑 master 且 `LLM_PROVIDER=gemini`**：SP/Tutor/Judges → **`gemini-2.5-flash`**；`main`/情绪分支对话仍是 Qwen；**Gemini 线无情绪检测** |
| ASR？ | master：**ElevenLabs Scribe v2 Realtime**；否则无 |
| TTS？ | master：**ElevenLabs Flash v2.5**；否则无 |

---

## 5. 相关源码索引

| 主题 | 路径 |
|---|---|
| Prompt 注册表 | `backend/app/prompts/__init__.py` |
| SP / Tutor / Evaluators | `backend/app/agents/*.py` |
| **情绪检测（Omni）** | `backend/app/agents/doctor_attitude.py`（由 `sp_agent.py` 调用） |
| MA / SA / Exam / CT | `backend/app/sessions/*.py` |
| CT 字段与阶段文案 | `backend/app/sessions/control.py` |
| WS `end_session` | `backend/app/main.py` |
| A 线模型配置 | `backend/app/config.py`、`backend/.env.example`（`QWEN_MODEL` / `QWEN_OMNI_MODEL`） |
| B 线模型 + 语音配置 | `origin/master` 上同名文件 + `backend/app/voice/` |
| 研究方法总览 | `docs/METHODOLOGY.md`、`README.md` |
