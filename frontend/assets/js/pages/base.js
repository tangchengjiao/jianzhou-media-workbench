/* 页面：工作总台 / IP档案 / 账号运营 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 工作总台 ================= */
  window.PageWorkbench = {
    data() {
      return {
        s: null, actions: [], signals: [], tasks: { running: [], recent_done: [] },
        chat: [{ role: 'bot', html: '<div class="intent-line">就绪 · 可解析意图：状态查询 / 创建任务 / 调整计划 / 开始复盘 / 页面跳转</div>我在。你可以直接说：「今天最值得处理什么」「把对标爆款加入提取队列」或「继续上次的 IP 访谈」。涉及费用的动作，我会先生成执行计划等你确认。' }],
        cmd: '', listening: false, speechRecognition: null,
        quickCmds: ['今天最值得处理什么？', '把对标爆款加入提取队列', '根据本周剩余时间重新调整排期', '继续上次的IP访谈'],
      };
    },
    async mounted() {
      await this.load();
      this._t = setInterval(this.loadTasks, 2000);
    },
    unmounted() { clearInterval(this._t); },
    methods: {
      go,
      async load() {
        try {
          const [s, a, sig] = await Promise.all([
            api.get('/api/workbench/status'), api.get('/api/workbench/actions'),
            api.get('/api/workbench/signals'),
          ]);
          this.s = s; this.actions = a; this.signals = sig;
          await this.loadTasks();
        } catch (e) { handleError(e); }
      },
      async loadTasks() { try { this.tasks = await api.get('/api/workbench/tasks'); } catch (e) {} },
      async sendCmd(text) {
        const v = (text || this.cmd).trim();
        if (!v) return;
        this.chat.push({ role: 'user', html: v.replace(/</g, '&lt;') });
        this.cmd = '';
        this.$nextTick(() => { const l = document.getElementById('wbChatLog'); if (l) l.scrollTop = l.scrollHeight; });
        try {
          const r = await api.post('/api/workbench/commands/parse', { text: v });
          (r.actions || []).forEach((action) => { if (action.kind === 'modal' && action.target === 'plan') action.signal_id = r.signal_id; });
          const btns = (r.actions || []).map((a) =>
            `<button class="btn btn-sm ${a.primary ? 'btn-pri' : 'btn-ghost'}" data-act='${JSON.stringify(a).replace(/'/g, '&#39;')}'>${a.label}</button>`).join('');
          this.chat.push({
            role: 'bot',
            html: `<div class="intent-line">意图 <code>${r.intent}</code> · 白名单工具 ${(r.tools || []).map((t) => `<code>${t}</code>`).join(' ')}</div>${r.reply}<div class="msg-actions">${btns}</div>`,
          });
        } catch (e) { handleError(e); }
        this.$nextTick(() => { const l = document.getElementById('wbChatLog'); if (l) l.scrollTop = l.scrollHeight; });
      },
      async onChatClick(e) {
        const btn = e.target.closest('button[data-act]');
        if (!btn) return;
        const act = JSON.parse(btn.dataset.act);
        if (act.kind === 'page') go(act.target);
        else if (act.kind === 'modal' && act.target === 'plan') await this.openExtractionPlan(act.signal_id);
        else toast(act.target || '已处理', 'warn');
      },
      async openExtractionPlan(signalId) {
        try {
          if (!signalId) { go('radar'); toast('请先在内容雷达选择一条真实信号', 'warn'); return; }
          const plan = await api.post('/api/extraction/plans', { signal_id: signalId });
          bus.emit('open-plan', plan);
        } catch (e) { handleError(e); }
      },
      startVoiceCommand() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) { toast('当前 Chrome 未提供浏览器语音识别，请使用文字指令或到 IP 档案使用可审计的 ASR 访谈', 'warn'); return; }
        if (this.listening && this.speechRecognition) { this.speechRecognition.stop(); return; }
        const recognition = new SpeechRecognition();
        recognition.lang = 'zh-CN'; recognition.interimResults = false; recognition.maxAlternatives = 1;
        recognition.onstart = () => { this.listening = true; };
        recognition.onend = () => { this.listening = false; };
        recognition.onerror = (event) => { this.listening = false; toast('语音指令识别失败：' + event.error, 'err'); };
        recognition.onresult = (event) => { this.cmd = event.results[0][0].transcript; toast('语音已转为文字，请确认后发送'); };
        this.speechRecognition = recognition; recognition.start();
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">{{ s ? (new Date().getHours()<12?'早上好':new Date().getHours()<18?'下午好':'晚上好') + '，' + s.greeting_name : '…' }}</div>
          <div class="page-q">我现在最应该处理什么？</div></div>
        <div class="mini-note" style="text-align:right">自动执行仅限：公开元数据同步 · 数据快照 · 到期提醒 · 本地缓存检查<br>下载 / ASR / 拆解 / 生成 等动作均需你主动确认</div>
      </div>

      <div class="card section-gap" v-if="s">
        <div class="card-head"><div class="card-title">当前运营状态 <span class="badge b-pri">{{ s.cycle.name }}</span></div>
          <span class="mini-note">IP 版本 <b class="mono">{{ s.ip_version }}</b> · 激活数据包 {{ s.active_packages }} 个</span></div>
        <div class="card-body">
          <div style="display:flex;gap:24px;align-items:center;margin-bottom:14px;flex-wrap:wrap">
            <div style="min-width:200px">
              <div style="font-size:11px;color:var(--ink3);margin-bottom:4px">本周目标 · 发布 {{ s.cycle.weekly_target }} 条</div>
              <div style="display:flex;align-items:center;gap:10px">
                <div class="progress" style="flex:1"><i :style="{width:(s.cycle.weekly_target?Math.min(100,s.cycle.weekly_done/s.cycle.weekly_target*100):0)+'%'}"></i></div>
                <b>{{ s.cycle.weekly_done }} / {{ s.cycle.weekly_target }}</b></div>
            </div>
            <div class="mini-note">{{ s.cycle.week }} · 核心目标：{{ s.cycle.goal }}</div>
          </div>
          <div class="stat-strip">
            <div class="stat"><div class="stat-l">内容库存</div><div class="stat-v">{{ s.inventory }} <small>条</small></div></div>
            <div class="stat"><div class="stat-l">待提取</div><div class="stat-v">{{ s.todo_extract }}</div></div>
            <div class="stat"><div class="stat-l">待拆解</div><div class="stat-v">{{ s.todo_analysis }}</div></div>
            <div class="stat"><div class="stat-l">待发布</div><div class="stat-v">{{ s.todo_publish }}</div></div>
            <div class="stat"><div class="stat-l">待复盘</div><div class="stat-v" style="color:var(--warn)">{{ s.todo_retro }}</div></div>
            <div class="stat"><div class="stat-l">本周剩余可投入</div><div class="stat-v">{{ s.cycle.hours_left }} <small>小时</small></div></div>
          </div>
        </div>
      </div>

      <div class="grid g21">
        <div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">今日行动队列 <span class="card-sub">按优先级 · 最多 5 项</span></div></div>
            <div class="card-body">
              <div class="action-item" v-for="(a,i) in actions" :key="i" :class="'p-'+a.priority">
                <span class="ai-tag badge" :class="a.cls">{{ a.tag }}</span>
                <div class="ai-body"><div class="ai-title">{{ a.title }}</div><div class="ai-meta">{{ a.meta }}</div></div>
                <button class="btn btn-sm" :class="{'btn-pri':a.primary}" @click="a.tag==='对标高表现'?openExtractionPlan(a.signal_id):go(a.page)">{{ a.button }}</button>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><div class="card-title">操盘手对话 <span class="card-sub">总台只识别意图、调用白名单工具并调度页面，不会替你静默执行高成本动作</span></div></div>
            <div class="card-body chat-box">
              <div class="chat-log" id="wbChatLog" @click="onChatClick">
                <div class="msg" v-for="(m,i) in chat" :key="i" :class="m.role" v-html="m.html"></div>
              </div>
              <div class="cmd-chips">
                <button class="chip" v-for="q in quickCmds" :key="q" @click="sendCmd(q)">{{ q }}</button>
              </div>
              <div class="chat-input">
                <input class="input" v-model="cmd" placeholder="输入指令（语音快速指令仅创建草稿，不修改正式档案）" @keydown.enter="sendCmd()">
                <button class="btn" :class="{'btn-danger':listening}" :title="listening?'停止语音输入':'语音快速指令'" @click="startVoiceCommand">{{ listening ? '■' : '🎙' }}</button>
                <button class="btn btn-pri" @click="sendCmd()">发送</button>
              </div>
            </div>
          </div>
        </div>

        <div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">重要信号 <span class="card-sub">仅展示通过阈值筛选的变化</span></div></div>
            <div class="card-body" style="padding-top:4px">
              <div class="signal-line" v-for="(x,i) in signals" :key="i">
                <span class="badge" :class="x.cls">{{ x.tag }}</span>
                <span><span v-html="x.html"></span><div class="mini-note">{{ x.note }}</div></span>
              </div>
              <div class="empty" v-if="!signals.length">暂无达到展示条件的真实变化。连接账号并完成至少两次同步后，这里会显示趋势证据。</div>
            </div>
          </div>
          <div class="card section-gap">
            <div class="card-head"><div class="card-title">任务队列</div></div>
            <div class="card-body" style="padding-top:4px">
              <div class="kv-row" v-for="t in tasks.running" :key="t.id">
                <span>{{ t.title }}</span>
                <span class="badge b-pri pulse" v-if="t.status==='RUNNING'"><span class="sdot"></span>执行中 {{ t.progress }}%</span>
                <span class="badge b-neu" v-else>{{ t.status==='QUEUED'?'排队中':'取消中' }}</span>
              </div>
              <div class="kv-row" v-for="t in tasks.recent_done" :key="t.id">
                <span>{{ t.title }}</span><span class="badge b-ok">已完成</span>
              </div>
              <div class="mini-note" style="margin-top:8px">任务不存在「自动成功」：每个状态转换都由明确事件驱动，可在系统设置 → 系统诊断中查询 Trace ID。</div>
            </div>
          </div>
          <div class="callout c-info" style="margin-bottom:0"><span>ⓘ</span><span><b>无静默兜底：</b>任何外部服务失败都会在此明示原因并暂停，不会返回假数据、不会自动切换模型、不会把失败任务标记为完成。</span></div>
        </div>
      </div>
    </section>`,
  };

  /* ================= IP 档案 ================= */
  window.PageProfile = {
    data() {
      return {
        profile: null, versions: [], candidates: [], corpus: [], style: {},
        topics: [], sessions: [], activeSession: null, answer: '',
        recording: false, mediaRecorder: null, finishing: false,
        versionOpen: false, versionSaving: false, versionError: null,
        versionDraft: {}, versionReason: '',
      };
    },
    async mounted() { await this.load(); },
    methods: {
      async load() {
        try {
          const [p, v, c, cp, st, tp, ss] = await Promise.all([
            api.get('/api/profile/active'), api.get('/api/profile/versions'),
            api.get('/api/profile/candidates'), api.get('/api/profile/corpus'),
            api.get('/api/profile/style'), api.get('/api/profile/interviews/topics'),
            api.get('/api/profile/interviews'),
          ]);
          this.profile = p; this.versions = v; this.candidates = c;
          this.corpus = cp; this.style = st; this.topics = tp; this.sessions = ss;
        } catch (e) { handleError(e); }
      },
      fs(key) { return (this.profile.content.field_sources || {})[key]; },
      trace(title, src) { bus.emit('open-trace', { title, src: src.src, quote: src.quote }); },
      async confirmField(c) {
        try { const r = await api.post('/api/profile/candidates/' + c.id + '/confirm'); toast(r.note); await this.load(); }
        catch (e) { handleError(e); }
      },
      async rejectField(c) {
        try { const r = await api.post('/api/profile/candidates/' + c.id + '/reject'); toast(r.note, 'warn'); await this.load(); }
        catch (e) { handleError(e); }
      },
      async startInterview(topic, mode) {
        try {
          const r = await api.post('/api/profile/interviews', { topic, mode: mode || 'text' });
          this.activeSession = r; await this.load();
        } catch (e) { handleError(e); }
      },
      async resumeSession(s) {
        try { this.activeSession = await api.get('/api/profile/interviews/' + s.id); }
        catch (e) { handleError(e); }
      },
      async sendAnswer() {
        const v = this.answer.trim();
        if (!v || !this.activeSession) return;
        this.answer = '';
        try {
          const r = await api.post('/api/profile/interviews/' + this.activeSession.id + '/turns', { text: v });
          this.activeSession = r;
          this.$nextTick(() => { const l = document.getElementById('ivLog'); if (l) l.scrollTop = l.scrollHeight; });
        } catch (e) { handleError(e); }
      },
      async toggleRecord() {
        if (this.recording) { this.mediaRecorder && this.mediaRecorder.stop(); this.recording = false; return; }
        if (!navigator.mediaDevices || !window.MediaRecorder) {
          toast('当前环境不支持麦克风采集，请改用文字访谈（模式切换由你显式选择）', 'warn'); return;
        }
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const chunks = [];
          this.mediaRecorder = new MediaRecorder(stream);
          this.mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
          this.mediaRecorder.onstop = async () => {
            stream.getTracks().forEach((t) => t.stop());
            const blob = new Blob(chunks, { type: 'audio/webm' });
            const fd = new FormData();
            fd.append('file', blob, 'answer.webm');
            try {
              const r = await api.upload('/api/profile/interviews/' + this.activeSession.id + '/turns/audio', fd);
              this.activeSession = r.data || r;
              if (r.stt && r.stt.demo) toast('演示 STT 转写已标注来源（demo）', 'warn');
            } catch (e) { handleError(e); }
          };
          this.mediaRecorder.start();
          this.recording = true;
          toast('录音中…再次点击结束（按键说话模式）');
        } catch (e) {
          toast('麦克风权限被拒绝：DEVICE_PERMISSION_DENIED。可显式改用文字模式。', 'err');
        }
      },
      async pauseInterview() {
        if (!this.activeSession) return;
        try { const r = await api.post('/api/profile/interviews/' + this.activeSession.id + '/pause'); toast(r.note, 'warn'); await this.load(); this.activeSession = null; }
        catch (e) { handleError(e); }
      },
      async finishInterview() {
        if (!this.activeSession) return;
        this.finishing = true;
        try {
          const r = await api.post('/api/profile/interviews/' + this.activeSession.id + '/finish');
          toast(r.note); this.activeSession = null; await this.load();
        } catch (e) { handleError(e); }
        this.finishing = false;
      },
      async activateVersion(v) {
        try { const r = await api.post('/api/profile/versions/' + v.id + '/activate'); toast('已激活 ' + v.version_no); await this.load(); }
        catch (e) { handleError(e); }
      },
      openVersionEditor() {
        const content = this.profile && this.profile.content ? this.profile.content : {};
        this.versionDraft = {
          ip_name: content.ip_name || '', positioning: content.positioning || '',
          audience: content.audience || '', value: content.value || '',
          pillars_text: (content.pillars || []).join('、'), key_opinion: content.key_opinion || '',
          monetization: content.monetization || '', banned_words_text: (content.banned_words || []).join('、'),
        };
        this.versionReason = ''; this.versionError = null; this.versionOpen = true;
        this.$nextTick(() => document.getElementById('profileVersionReason')?.focus());
      },
      async saveVersion() {
        if (!this.versionReason.trim()) { this.versionError = { code: 'VALIDATION_FAILED', message: '请填写本次修改原因。' }; return; }
        this.versionSaving = true; this.versionError = null;
        try {
          const content = { ...this.profile.content, ...this.versionDraft };
          content.pillars = this.versionDraft.pillars_text.split(/[、,，]/).map((x) => x.trim()).filter(Boolean);
          content.banned_words = this.versionDraft.banned_words_text.split(/[、,，]/).map((x) => x.trim()).filter(Boolean);
          delete content.pillars_text; delete content.banned_words_text;
          const result = await api.post('/api/profile/versions', { content, reason: this.versionReason.trim(), activate: true });
          this.versionOpen = false; await this.load(); toast(result.note);
        } catch (e) { this.versionError = e; }
        this.versionSaving = false;
      },
    },
    computed: {
      pendingCandidates() { return this.candidates.filter((c) => c.status === 'pending'); },
      sessionTurns() { return this.activeSession ? this.activeSession.turns : []; },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">IP档案</div><div class="page-q">我是谁，我为谁创作，我通常怎么表达？</div></div>
        <div><button class="btn" id="createProfileVersionBtn" @click="openVersionEditor">创建新版本</button>
          <button class="btn btn-pri" @click="startInterview('完整建档','text')">开始访谈</button></div>
      </div>

      <div class="card section-gap" v-if="profile">
        <div class="card-head"><div class="card-title">IP 总览 <span class="badge b-ok">{{ profile.version_no }} · 当前激活</span></div>
          <span class="mini-note">最近更新 {{ profile.created_at.slice(0,10) }} · 每个字段均可追溯来源</span></div>
        <div class="card-body"><div class="dl">
          <div><div class="dt">IP 名称</div><div class="dd">{{ profile.content.ip_name }}</div></div>
          <div><div class="dt">当前定位 <span class="trace" v-if="fs('positioning')" @click="trace('当前定位', fs('positioning'))">来源</span></div><div class="dd">{{ profile.content.positioning }}</div></div>
          <div><div class="dt">目标用户 <span class="trace" v-if="fs('audience')" @click="trace('目标用户', fs('audience'))">来源</span></div><div class="dd">{{ profile.content.audience }}</div></div>
          <div><div class="dt">核心价值</div><div class="dd">{{ profile.content.value }}</div></div>
          <div><div class="dt">内容支柱 <span class="trace" v-if="fs('pillars')" @click="trace('内容支柱', fs('pillars'))">来源</span></div><div class="dd">{{ (profile.content.pillars||[]).join(' · ') }}</div></div>
          <div><div class="dt">代表观点</div><div class="dd">“{{ profile.content.key_opinion }}”</div></div>
          <div><div class="dt">转化方向</div><div class="dd">{{ profile.content.monetization }}</div></div>
          <div><div class="dt">禁用表达 <span class="badge b-warn" v-if="pendingCandidates.length">待确认 {{ pendingCandidates.length }}</span></div>
            <div class="dd">{{ (profile.content.banned_words||[]).map(w=>'“'+w+'”').join('') || '—' }}</div></div>
        </div></div>
      </div>

      <!-- 访谈进行中 -->
      <div class="card section-gap" v-if="activeSession">
        <div class="card-head"><div class="card-title">访谈进行中 · {{ activeSession.topic }}
          <span class="badge b-info">{{ activeSession.mode === 'voice' ? '语音模式' : '文字模式' }}</span>
          <span class="badge b-neu">{{ activeSession.id }}</span></div>
          <span class="mini-note">一次只问一个问题 · 结论需逐项确认</span></div>
        <div class="card-body">
          <div class="iv-progress">
            <span class="mini-note">第 {{ activeSession.current_q }} / {{ activeSession.total_q }} 问</span>
            <div class="progress"><i :style="{width:(activeSession.current_q/activeSession.total_q*100)+'%'}"></i></div>
          </div>
          <div class="iv-log" id="ivLog">
            <div class="iv-msg" v-for="t in sessionTurns" :key="t.id" :class="t.role==='user'?'a':'q'">{{ t.text }}</div>
          </div>
          <div class="chat-input" style="margin-top:10px">
            <input class="input" v-model="answer" placeholder="输入回答，或按住麦克风语音作答" @keydown.enter="sendAnswer">
            <button class="btn" :class="{'btn-danger':recording}" @click="toggleRecord">{{ recording ? '■ 停止' : '🎙' }}</button>
            <button class="btn btn-pri" @click="sendAnswer">回答</button>
          </div>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="btn btn-sm" @click="pauseInterview">暂停（保存进度）</button>
            <button class="btn btn-sm btn-pri" :disabled="finishing" @click="finishInterview">结束访谈并生成字段候选</button>
            <span class="mini-note">中断不会生成「建档成功」的假结果；切换模式需你显式选择并记录来源。</span>
          </div>
        </div>
      </div>

      <div class="grid g2 section-gap">
        <div class="card">
          <div class="card-head"><div class="card-title">语音访谈 <span class="card-sub">操盘手角色 · 结论需逐项确认后才写入正式档案</span></div></div>
          <div class="card-body">
            <div class="field"><label>选择访谈主题</label>
              <div class="chips"><button class="chip" v-for="t in topics" :key="t" @click="startInterview(t,'text')">{{ t }}</button></div>
            </div>
            <div class="field"><label>访谈流程</label>
              <div class="iv-flow"><span>主题</span><em>→</em><span>预计问题</span><em>→</em><span>对谈</span><em>→</em><span>实时转写</span><em>→</em><span>操盘手追问</span><em>→</em><span>摘要</span><em>→</em><span>字段提取</span><em>→</em><span>逐项确认</span><em>→</em><span>新版本</span></div>
            </div>
            <div class="callout c-warn" style="margin-bottom:10px" v-if="sessions.find(s=>s.status==='PAUSED')">
              <span>⏸</span><span>会话 {{ sessions.find(s=>s.status==='PAUSED').id }}「{{ sessions.find(s=>s.status==='PAUSED').topic }}」暂停在第 {{ sessions.find(s=>s.status==='PAUSED').current_q }} 问<br>
              <span class="mini-note">已保存进度 · 中断不会生成“建档成功”的假结果</span></span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap">
              <template v-if="sessions.find(s=>s.status==='PAUSED')">
                <button class="btn btn-pri" @click="resumeSession(sessions.find(s=>s.status==='PAUSED'))">继续访谈</button>
              </template>
              <button class="btn" @click="startInterview('表达风格','voice')">新建语音访谈</button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">待确认字段 <span class="badge b-warn">{{ pendingCandidates.length }} 项</span>
            <span class="card-sub">推断（inferred）字段不会自动进入正式档案</span></div></div>
          <div class="card-body">
            <div class="empty" v-if="!pendingCandidates.length">暂无待确认字段</div>
            <div class="field-cand" v-for="c in pendingCandidates" :key="c.id" :class="{inferred: c.inference_type==='inferred'}">
              <div class="fc-body">
                <div class="fc-name">{{ c.field_path }}
                  <span class="badge" :class="c.inference_type==='inferred'?'b-warn':'b-ok'">{{ c.inference_type==='inferred'?'模型推断':c.inference_type==='summarized'?'摘要':'明确表述' }}</span></div>
                <div class="fc-val">{{ c.value }}</div>
                <div class="fc-src">来源：{{ c.evidence }} · 置信度 {{ c.confidence }}</div>
              </div>
              <button class="btn btn-sm btn-pri" @click="confirmField(c)">{{ c.inference_type==='inferred'?'确认':'接受' }}</button>
              <button class="btn btn-sm btn-ghost" @click="rejectField(c)">拒绝</button>
            </div>
          </div>
        </div>
      </div>

      <div class="grid g2 section-gap">
        <div class="card">
          <div class="card-head"><div class="card-title">个人语料 <span class="card-sub">{{ corpus.reduce((a,b)=>a+b.count,0) }} 份 · 每份语料独立授权</span></div>
            <button class="btn btn-sm" @click="go('packages')">去导入</button></div>
          <div class="card-body" style="padding-top:4px">
            <div class="kv-row" v-for="c in corpus" :key="c.id">
              <span>{{ c.name }} · {{ c.count }} 份 <span class="badge b-neu" v-if="c.valid_until">有效期至 {{ c.valid_until }}</span></span>
              <span>
                <i class="tag-perm" :class="{on:c.perms.style}">风格提取</i>
                <i class="tag-perm" :class="{on:c.perms.fact}">事实引用</i>
                <i class="tag-perm" :class="{on:c.perms.generate}">生成新稿</i>
              </span>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">表达画像 <span class="badge b-vio">可编辑建议 · 非不可变事实</span></div>
            <button class="btn btn-sm" @click="openVersionEditor">编辑档案</button></div>
          <div class="card-body" style="padding-top:4px"><div class="style-grid">
            <div class="style-item" v-for="(v,k) in style" :key="k"><span>{{ k }} <span class="mini-note">· {{ v.src }}</span></span><b>{{ v.value }}</b></div>
          </div></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">档案版本 <span class="card-sub">正式修改均生成不可变版本 · 原版本只读保留</span></div></div>
        <div class="card-body" style="padding-top:4px"><table class="tbl">
          <tr><th>版本</th><th>变更内容</th><th>变更原因</th><th>来源</th><th>生效时间</th><th>操作</th></tr>
          <tr v-for="v in versions" :key="v.id">
            <td class="mono">{{ v.version_no }}</td><td>{{ v.reason.split('：')[0] }}</td><td>{{ v.reason }}</td>
            <td>{{ v.source }}</td><td>{{ v.created_at.slice(0,10) }}</td>
            <td><span class="badge b-ok" v-if="v.active">激活中</span>
              <button class="btn btn-sm btn-ghost" v-else @click="activateVersion(v)">激活</button></td>
          </tr>
        </table></div>
      </div>

      <div class="modal profile-version-modal" :class="{open:versionOpen}" role="dialog" aria-modal="true" aria-labelledby="profileVersionTitle" @click.self="versionOpen=false">
        <div class="modal-card">
          <div class="modal-head"><div class="modal-title" id="profileVersionTitle">创建 IP 档案新版本</div><button class="modal-x" aria-label="关闭" @click="versionOpen=false">×</button></div>
          <div class="modal-body">
            <div class="callout c-info"><span>版本</span><span>保存会创建新的不可变版本并立即激活；旧版本保留在下方，可随时切回。</span></div>
            <div class="grid g2"><label class="field"><span>IP 名称</span><input class="input" v-model="versionDraft.ip_name"></label><label class="field"><span>核心价值</span><input class="input" v-model="versionDraft.value"></label></div>
            <label class="field"><span>当前定位</span><textarea class="input" rows="2" v-model="versionDraft.positioning"></textarea></label>
            <label class="field"><span>目标用户</span><textarea class="input" rows="2" v-model="versionDraft.audience"></textarea></label>
            <label class="field"><span>内容支柱（顿号分隔）</span><input class="input" v-model="versionDraft.pillars_text"></label>
            <label class="field"><span>代表观点</span><textarea class="input" rows="2" v-model="versionDraft.key_opinion"></textarea></label>
            <div class="grid g2"><label class="field"><span>转化方向</span><input class="input" v-model="versionDraft.monetization"></label><label class="field"><span>禁用表达（顿号分隔）</span><input class="input" v-model="versionDraft.banned_words_text"></label></div>
            <label class="field"><span>修改原因（必填）</span><input class="input" id="profileVersionReason" v-model="versionReason" placeholder="例如：根据近 30 天数据调整目标用户"></label>
            <div class="inline-error" role="alert" v-if="versionError"><b>{{ versionError.code }}</b><span>{{ versionError.message }}</span></div>
          </div>
          <div class="modal-foot"><button class="btn" @click="versionOpen=false">取消</button><button class="btn btn-pri" :disabled="versionSaving" @click="saveVersion">{{ versionSaving ? '保存中…' : '创建并激活版本' }}</button></div>
        </div>
      </div>
    </section>`,
  };

  /* ================= 账号运营 ================= */
  window.PageAccount = {
    data() {
      return {
        accounts: [], cycle: null, suggestions: [], datasource: { platforms: [] },
        selected: null, snapshots: [], fanSeries: '', fanArea: '',
        addOpen: false, adding: false, addError: null, lastResult: null,
        cycleOpen: false, cycleSaving: false, cycleDraft: {},
        form: { platform: 'douyin', link: '', display_name: '', source_mode: 'provider', sync_frequency: '每 6 小时' },
      };
    },
    computed: {
      platformOptions() { return this.datasource.platforms || []; },
      selectedPlatform() {
        return this.platformOptions.find((item) => item.id === this.form.platform)
          || { label: '账号', id_label: '主页链接 / 平台 ID', placeholder: '粘贴账号主页链接或输入平台 ID' };
      },
    },
    async mounted() {
      await this.load();
      const params = new URLSearchParams(location.search);
      if (params.get('account') === 'add') {
        this.openAdd({ platform: params.get('platform'), link: params.get('link') });
        ['account', 'platform', 'link'].forEach((key) => params.delete(key));
        const query = params.toString();
        history.replaceState({}, '', location.pathname + (query ? '?' + query : '') + location.hash);
      }
    },
    methods: {
      async load() {
        try {
          const [a, c, s, ds] = await Promise.all([
            api.get('/api/accounts'), api.get('/api/accounts/cycle/current'),
            api.get('/api/accounts/suggestions/list'), api.get('/api/datasource/status'),
          ]);
          this.accounts = a; this.cycle = c; this.suggestions = s; this.datasource = ds;
          if (this.selected) {
            const current = a.find((item) => item.id === this.selected.id);
            if (current) await this.openAccount(current);
            else this.closeAccount();
          } else if (a.length) await this.openAccount(a[0], false);
        } catch (e) { handleError(e); }
      },
      openAdd(prefill) {
        const data = prefill || {};
        const platform = this.platformOptions.some((item) => item.id === data.platform) ? data.platform : 'douyin';
        this.form = { platform, link: data.link || '', display_name: '', source_mode: 'provider', sync_frequency: '每 6 小时' };
        this.addError = null; this.addOpen = true;
        this.$nextTick(() => document.getElementById('ownAccountLinkInput')?.focus());
      },
      async addAccount() {
        if (!this.form.link.trim()) {
          this.addError = { code: 'VALIDATION_FAILED', message: '请填写账号主页链接或平台账号 ID。' }; return;
        }
        this.adding = true; this.addError = null;
        try {
          const result = await api.post('/api/accounts', { ...this.form, link: this.form.link.trim() });
          this.lastResult = result;
          await this.load();
          this.addOpen = false;
          const added = this.accounts.find((item) => item.id === result.id);
          if (added) await this.openAccount(added);
          toast(result.note);
          refreshTopbar();
        } catch (error) { this.addError = error; }
        this.adding = false;
      },
      async openAccount(account, scroll = true) {
        try {
          this.selected = await api.get('/api/accounts/' + encodeURIComponent(account.id));
          this.snapshots = this.selected.snapshots || [];
          this.renderFanSeries();
          if (scroll) this.$nextTick(() => document.getElementById('accountDetail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
        } catch (e) { handleError(e); }
      },
      closeAccount() { this.selected = null; this.snapshots = []; this.fanSeries = ''; this.fanArea = ''; },
      renderFanSeries() {
        const pts = this.snapshots.slice(-10);
        if (!pts.length) { this.fanSeries = ''; this.fanArea = ''; return; }
        const min = Math.min(...pts.map((p) => p.followers));
        const max = Math.max(...pts.map((p) => p.followers));
        const W = 220, H = 64;
        const coords = pts.map((p, i) => {
          const x = pts.length === 1 ? W / 2 : (i / (pts.length - 1)) * W;
          const y = H - 14 - ((p.followers - min) / (max - min || 1)) * (H - 24);
          return `${x.toFixed(0)},${y.toFixed(0)}`;
        });
        this.fanSeries = coords.join(' ');
        this.fanArea = `0,${H} ` + this.fanSeries + ` ${W},${H}`;
      },
      async sync(acc) {
        try {
          const r = await api.post('/api/accounts/' + acc.id + '/sync');
          if (r.manual) { toast(r.note, 'warn'); return; }
          acc.status = 'syncing';
          toast(r.note);
          await this.waitJob(r.job_id);
        } catch (e) { handleError(e); }
      },
      async waitJob(jobId) {
        for (let i = 0; i < 80; i += 1) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const job = await api.get('/api/jobs/' + encodeURIComponent(jobId));
          if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(job.status)) {
            await this.load(); refreshTopbar();
            if (job.status === 'SUCCEEDED') toast('账号同步完成，资料与今日快照已刷新');
            else handleError(job.error_json || { code: 'ACCOUNT_SYNC_FAILED', message: '账号同步失败', trace_id: job.trace_id });
            return;
          }
        }
        toast('同步仍在后台执行，可稍后刷新查看结果', 'warn');
      },
      fixError() { location.href = '/?settings=source#/settings'; },
      async saveFrequency() {
        if (!this.selected) return;
        try {
          const result = await api.patch('/api/accounts/' + encodeURIComponent(this.selected.id), { sync_frequency: this.selected.sync_frequency });
          this.selected = { ...this.selected, ...result.account }; await this.load(); toast(result.note);
        } catch (e) { handleError(e); }
      },
      async removeAccount(account) {
        if (!confirm(`确认删除自有账号「${account.display_name || account.handle}」及其本地快照？`)) return;
        try { const result = await api.delete('/api/accounts/' + encodeURIComponent(account.id)); this.lastResult = null; await this.load(); toast(result.note, 'warn'); refreshTopbar(); }
        catch (e) { handleError(e); }
      },
      openCycle() { this.cycleDraft = { ...this.cycle }; this.cycleOpen = true; },
      async saveCycle() {
        this.cycleSaving = true;
        try {
          const result = await api.post('/api/accounts/cycle/current', {
            goal: this.cycleDraft.goal, platforms: this.cycleDraft.platforms,
            hours_per_week: Number(this.cycleDraft.hours_per_week), complexity: this.cycleDraft.complexity,
            promotion: this.cycleDraft.promotion, rhythm: this.cycleDraft.rhythm,
          });
          this.cycle = result.cycle; this.cycleOpen = false; toast(result.note);
        } catch (e) { handleError(e); }
        this.cycleSaving = false;
      },
      async setRhythm(r) {
        try { const result = await api.post('/api/accounts/cycle/current', { rhythm: r }); this.cycle = result.cycle; toast('更新节奏已保存'); }
        catch (e) { handleError(e); }
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">账号运营</div><div class="page-q">我的账号现在是什么状态，这个周期应该怎么运营？</div></div>
        <button class="btn btn-pri" id="addOwnAccountBtn" @click="openAdd">+ 添加账号</button>
      </div>

      <div class="callout c-ok section-gap" role="status" aria-live="polite" v-if="lastResult">
        <span>✓</span><span><b>{{ lastResult.account.display_name }}</b> 已出现在账号列表中 · {{ lastResult.account.platform_label }} · {{ lastResult.account.sync_frequency }}同步
        <button class="text-btn" @click="openAccount(lastResult.account)">查看账号结果</button></span>
      </div>

      <div class="empty section-gap" v-if="!accounts.length"><b>还没有绑定自有账号</b><span>添加主页链接后，系统会解析公开资料并建立可追踪快照；失败会展示真实原因。</span><button class="btn btn-pri" @click="openAdd">添加第一个账号</button></div>

      <div class="grid g2 section-gap" v-else>
        <div class="card" v-for="a in accounts" :key="a.id" :style="a.status==='error'?'border-color:var(--err-line)':''">
          <div class="card-body" style="padding:14px 16px">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <div><b>{{ a.platform_label }}</b> <span style="color:var(--ink3)">{{ a.display_name }}</span></div>
              <span class="badge b-ok" v-if="a.status==='ok'">同步正常</span>
              <span class="badge b-warn" v-else-if="a.status==='syncing'">同步中</span>
              <span class="badge b-err" v-else-if="a.status==='error'">同步失败</span>
              <span class="badge b-neu" v-else>手动账号</span>
            </div>
            <div class="callout c-err" style="margin:10px 0" v-if="a.status==='error'">
              <span>✕</span><span style="font-size:11.5px"><b>{{ a.extra.error_code || 'ACCOUNT_SYNC_FAILED' }}</b> · {{ a.note }}<br><b>系统未返回任何替代数据。</b>
              <span class="trace" v-if="a.extra.trace_id" @click="bus.emit('open-error',{code:a.extra.error_code,message:a.note,stage:'account_sync',provider:a.bound_provider,retryable:false,trace_id:a.extra.trace_id,details:{charged:false}})">Trace {{ a.extra.trace_id.slice(0,12) }}…</span></span>
            </div>
            <div style="display:flex;gap:20px;margin:10px 0;font-size:12px;color:var(--ink2)">
              <span>粉丝 <b style="color:var(--ink)">{{ a.followers.toLocaleString() }}</b></span>
              <span>{{ a.sync_frequency }}</span>
              <span v-if="a.last_sync_at">最近同步 {{ a.last_sync_at.slice(5,16).replace('T',' ') }}</span>
            </div>
            <div style="display:flex;gap:6px">
              <button class="btn btn-sm btn-pri" @click="openAccount(a)">查看数据</button>
              <button class="btn btn-sm" v-if="a.status==='error'" @click="fixError">修复配置</button>
              <button class="btn btn-sm" v-if="a.bound_provider!=='manual'" :disabled="a.status==='syncing'" @click="sync(a)">{{ a.status==='error' ? '重试同步' : '立即同步' }}</button>
              <button class="btn btn-sm" v-else @click="go('packages')">导入后台数据</button>
              <button class="btn btn-sm btn-ghost" @click="removeAccount(a)">删除</button>
            </div>
          </div>
        </div>
      </div>

      <div class="card section-gap" id="accountDetail" v-if="selected">
        <div class="card-head"><div class="card-title">{{ selected.display_name }} · 账号数据 <span class="badge b-ok" v-if="selected.status==='ok'">真实快照</span></div><button class="btn btn-sm" @click="closeAccount">收起</button></div>
        <div class="card-body">
          <div class="grid g3">
            <div><div class="dt">平台与账号 ID</div><div class="dd">{{ selected.platform_label }} · {{ selected.extra.uid }}</div></div>
            <div><div class="dt">公开资料</div><div class="dd">粉丝 {{ selected.followers.toLocaleString() }} · 作品 {{ selected.video_count.toLocaleString() }}</div></div>
            <div><div class="dt">数据来源</div><div class="dd">{{ selected.bound_provider }} · {{ selected.last_sync_at ? selected.last_sync_at.replace('T',' ') : '尚未同步' }}</div></div>
          </div>
          <p class="mini-note" v-if="selected.bio">简介：{{ selected.bio }}</p>
          <p v-if="selected.homepage"><a class="text-btn" :href="selected.homepage" target="_blank" rel="noreferrer">打开原账号主页 ↗</a></p>
          <div class="settings-actions"><label class="field" style="max-width:260px"><span>同步频率</span><select class="input" v-model="selected.sync_frequency"><option>每 1 小时</option><option>每 3 小时</option><option>每 6 小时</option><option>每 12 小时</option><option>每天</option><option>仅手动</option></select></label><button class="btn" @click="saveFrequency">保存频率</button></div>
          <div class="grid g2 section-gap">
            <div><div class="card-title">粉丝快照</div><svg viewBox="0 0 220 64" style="width:100%;margin-top:10px" v-if="snapshots.length>1"><polygon :points="fanArea" fill="rgba(228,87,46,.12)"/><polyline :points="fanSeries" fill="none" stroke="#E4572E" stroke-width="2" stroke-linecap="round"/></svg><div class="callout c-info" style="margin-top:10px" v-else-if="snapshots.length===1"><span>首个快照</span><span><b>{{ Number(snapshots[0].followers||0).toLocaleString() }}</b> 粉丝；再次同步后显示趋势，不用单点伪造曲线。</span></div><div class="empty" v-else>尚无快照</div></div>
            <div><div class="card-title">快照记录</div><div class="kv-row" v-for="ss in snapshots.slice().reverse().slice(0,6)" :key="ss.id"><span>{{ ss.date }}</span><b>{{ Number(ss.followers||0).toLocaleString() }} 粉丝 · {{ ss.posts || 0 }} 作品</b></div><div class="empty" v-if="!snapshots.length">同步成功后在这里显示</div></div>
          </div>
        </div>
      </div>

      <div class="grid g2 section-gap" v-if="cycle">
        <div class="card">
          <div class="card-head"><div class="card-title">当前运营周期 <span class="badge b-pri">{{ cycle.name }}</span></div>
            <button class="btn btn-sm" id="editCycleBtn" @click="openCycle">调整</button></div>
          <div class="card-body" style="padding-top:6px">
            <div class="kv-row"><span>核心目标</span><b>{{ cycle.goal }}</b></div>
            <div class="kv-row"><span>重点平台</span><b>{{ cycle.platforms }}</b></div>
            <div class="kv-row"><span>每周可投入</span><b>{{ cycle.hours_per_week }} 小时</b></div>
            <div class="kv-row"><span>可接受制作复杂度</span><b>{{ cycle.complexity }}</b></div>
            <div class="kv-row"><span>推广期</span><b>{{ cycle.promotion }}</b></div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">更新节奏</div></div>
          <div class="card-body">
            <div class="chips" style="margin-bottom:10px">
              <button class="chip" v-for="r in cycle.rhythms" :key="r" :class="{active: cycle.rhythm===r}" @click="setRhythm(r)">{{ r }}</button>
            </div>
            <div class="callout c-info" style="margin-bottom:0"><span>ⓘ</span><span>基于近 8 周完成率 <b>{{ Math.round(cycle.completion_rate*100) }}%</b>，建议保持每周 4 条。系统不会默认把「多发」当作正确答案。</span></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">运营建议 <span class="card-sub">每条建议必须显示依据</span></div></div>
        <div class="card-body" style="padding-top:4px">
          <div class="signal-line" v-for="(sg,i) in suggestions" :key="i">
            <span class="badge" :class="sg.cls">{{ sg.tag }}</span>
            <span style="flex:1">{{ sg.text }}<div class="mini-note">{{ sg.basis }}</div></span>
          </div>
        </div>
      </div>

      <div class="modal own-account-add-modal" :class="{open:addOpen}" role="dialog" aria-modal="true" aria-labelledby="ownAccountAddTitle" @click.self="addOpen=false">
        <div class="modal-card">
          <div class="modal-head"><div class="modal-title" id="ownAccountAddTitle">添加自有账号</div><button class="modal-x" aria-label="关闭" @click="addOpen=false">×</button></div>
          <div class="modal-body">
            <div class="callout c-info"><span>1</span><span>填写主页链接或平台 ID；系统会先解析公开资料，成功后才写入账号列表和首条快照。</span></div>
            <div class="platform-support"><button v-for="platform in platformOptions" :key="platform.id" type="button" class="platform-support-item" :class="{active:form.platform===platform.id}" @click="form.platform=platform.id">{{ platform.label }}</button></div>
            <div class="grid g2">
              <label class="field"><span>平台</span><select class="input" id="ownAccountPlatform" v-model="form.platform"><option v-for="platform in platformOptions" :key="platform.id" :value="platform.id">{{ platform.group }} · {{ platform.label }}</option></select></label>
              <label class="field"><span>数据方式</span><select class="input" v-model="form.source_mode"><option value="provider">TikHub 公开数据（推荐）</option><option value="manual">手动导入后台数据</option></select></label>
            </div>
            <label class="field"><span>{{ selectedPlatform.id_label }}</span><input class="input" id="ownAccountLinkInput" v-model="form.link" :placeholder="selectedPlatform.placeholder" @keyup.enter="addAccount"><small v-if="form.source_mode==='provider'">会执行一次真实账号解析与概况读取，可能产生接口费用。</small><small v-else>仅建立入口，不生成粉丝数等猜测数据。</small></label>
            <label class="field" v-if="form.source_mode==='manual'"><span>显示名称（可选）</span><input class="input" v-model="form.display_name" placeholder="例如：见舟AI"></label>
            <label class="field"><span>同步频率</span><select class="input" id="ownAccountSyncFrequency" v-model="form.sync_frequency" :disabled="form.source_mode==='manual'"><option>每 1 小时</option><option>每 3 小时</option><option>每 6 小时</option><option>每 12 小时</option><option>每天</option><option>仅手动</option></select></label>
            <div class="inline-error" role="alert" v-if="addError"><b>{{ addError.code }}</b><span>{{ addError.message }}</span><button class="text-btn" v-if="addError.code==='CONFIG_MISSING_API_KEY'" @click="addOpen=false;fixError()">前往系统设置</button></div>
          </div>
          <div class="modal-foot"><button class="btn" :disabled="adding" @click="addOpen=false">取消</button><button class="btn btn-pri" id="confirmAddOwnAccountBtn" :disabled="adding" @click="addAccount">{{ adding ? '正在解析并绑定…' : '解析并添加账号' }}</button></div>
        </div>
      </div>

      <div class="modal account-cycle-modal" v-if="cycle" :class="{open:cycleOpen}" role="dialog" aria-modal="true" aria-labelledby="accountCycleTitle" @click.self="cycleOpen=false">
        <div class="modal-card">
          <div class="modal-head"><div class="modal-title" id="accountCycleTitle">调整运营周期</div><button class="modal-x" aria-label="关闭" @click="cycleOpen=false">×</button></div>
          <div class="modal-body">
            <label class="field"><span>核心目标</span><input class="input" v-model="cycleDraft.goal"></label>
            <div class="grid g2"><label class="field"><span>重点平台</span><input class="input" v-model="cycleDraft.platforms"></label><label class="field"><span>每周可投入（小时）</span><input class="input" type="number" min="0" max="168" v-model.number="cycleDraft.hours_per_week"></label></div>
            <label class="field"><span>更新节奏</span><select class="input" v-model="cycleDraft.rhythm"><option v-for="r in cycle.rhythms" :key="r">{{ r }}</option></select></label>
            <label class="field"><span>可接受制作复杂度</span><input class="input" v-model="cycleDraft.complexity"></label>
            <label class="field"><span>推广与实验</span><input class="input" v-model="cycleDraft.promotion"></label>
          </div>
          <div class="modal-foot"><button class="btn" @click="cycleOpen=false">取消</button><button class="btn btn-pri" :disabled="cycleSaving" @click="saveCycle">{{ cycleSaving ? '保存中…' : '保存周期' }}</button></div>
        </div>
      </div>
    </section>`,
  };
})();
