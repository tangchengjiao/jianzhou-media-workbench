/* 页面：资料中心 / 系统设置 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 资料中心 ================= */
  window.PagePackages = {
    data() {
      return {
        packages: [], imports: { flow: [], batches: [] }, tracking: [], users: [],
        uploading: false, lastPlan: null,
      };
    },
    async mounted() { await this.load(); },
    methods: {
      go,
      async load() {
        try {
          const [p, i, u] = await Promise.all([
            api.get('/api/packages'), api.get('/api/imports'), api.get('/api/users'),
          ]);
          this.packages = p; this.imports = i; this.users = u;
          this.tracking = await api.get('/api/packages/sources/tracking');
        } catch (e) { handleError(e); }
      },
      switchUser(u) { if (!u.active) bus.emit('open-switch', u.id); },
      async impact(p) {
        try {
          const r = await api.get('/api/packages/' + p.id + '/impact');
          bus.emit('open-trace', { title: '删除影响 · ' + r.package, src: r.sources + ' 个来源', quote: `${r.note}\n引用：${(r.referenced_by || []).join('、') || '无'}` });
        } catch (e) { handleError(e); }
      },
      async uploadFile(e) {
        const file = e.target.files[0];
        if (!file) return;
        this.uploading = true;
        const fd = new FormData();
        fd.append('file', file);
        try {
          const r = await api.upload('/api/imports/upload?target_package=' + encodeURIComponent('内容语料包'), fd);
          this.lastPlan = r;
          toast(r.note, r.failed ? 'warn' : 'ok');
          await this.load();
        } catch (e) { handleError(e); }
        this.uploading = false;
        e.target.value = '';
      },
      async commit(bid) {
        try { const r = await api.post('/api/imports/' + bid + '/commit'); toast(r.note, 'warn'); this.lastPlan = null; await this.load(); }
        catch (e) { handleError(e); }
      },
      async rollback(bid) {
        if (!confirm('二次确认：将回滚整个批次 ' + bid + '，已写入数据将被移除。确定继续？')) return;
        try { const r = await api.post('/api/imports/' + bid + '/rollback'); toast(r.note, 'warn'); await this.load(); }
        catch (e) { handleError(e); }
      },
      batchBadge(b) {
        return {
          parsed: ['b-info', '待确认'], partial: ['b-warn', '部分成功 · 待确认'],
          committed: ['b-ok', '已写入'], partial_committed: ['b-warn', '部分成功'],
          rolled_back: ['b-neu', '已回滚'],
        }[b.status] || ['b-neu', b.status];
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">资料中心</div><div class="page-q">系统现在正在使用哪些资料，它们从哪里来？</div></div>
        <label class="btn btn-pri" style="cursor:pointer">+ 外部导入<input type="file" style="display:none" @change="uploadFile" accept=".csv,.xlsx,.json,.md,.txt,.zip"></label>
      </div>

      <div class="callout c-info"><span>◈</span><span><b>数据包机制：</b>换一个创作者 = 切换激活的数据包组合，而不是修改提示词或代码。切换时会清空会话上下文，禁止跨用户检索。</span></div>

      <div class="grid g4 section-gap">
        <div class="card" v-for="p in packages" :key="p.id">
          <div class="card-body">
            <div style="display:flex;justify-content:space-between"><b>{{ p.name }}</b>
              <span class="badge" :class="p.status==='active'?'b-ok':'b-neu'">{{ p.status==='active'?'激活中':p.status }}</span></div>
            <div class="mini-note" style="margin:6px 0">{{ p.version }} · 来源 {{ p.source_count }} · {{ p.summary }}<br>更新 {{ (p.updated_at||'').slice(0,10) }} · 可见范围 {{ p.visibility }}</div>
            <button class="btn btn-sm btn-ghost" @click="impact(p)">删除影响…</button>
          </div>
        </div>
        <div class="card" style="border-style:dashed">
          <div class="card-body" style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:86px;gap:6px">
            <div class="chips" style="justify-content:center">
              <button class="chip" v-for="u in users" :key="u.id" :class="{active:u.active}" @click="switchUser(u)">{{ u.name }}{{ u.active ? '（当前）' : '' }}</button>
            </div>
            <div class="mini-note">切换创作者：关闭当前包组合、清空会话、激活新用户包</div>
          </div>
        </div>
      </div>

      <div class="grid g2 section-gap">
        <div class="card">
          <div class="card-head"><div class="card-title">外部导入 <span class="card-sub">默认只保存：不自动 ASR / OCR / LLM / Embedding</span></div></div>
          <div class="card-body">
            <div class="iv-flow" style="margin-bottom:12px"><span v-for="(f,i) in imports.flow" :key="f"><template v-if="i>0"><em>→</em></template><span>{{ f }}</span></span></div>
            <div v-if="lastPlan">
              <div class="callout" :class="lastPlan.failed?'c-warn':'c-info'">
                <span>{{ lastPlan.failed ? '⚠' : 'ⓘ' }}</span>
                <span>导入计划：新增 <b>{{ lastPlan.success }}</b> 条 · 异常 <b>{{ lastPlan.failed }}</b> 条 · 目标包「内容语料包」</span>
              </div>
              <table class="tbl" v-if="lastPlan.mapping.length">
                <tr><th>文件字段</th><th></th><th>目标字段</th><th>策略</th></tr>
                <tr v-for="m in lastPlan.mapping" :key="m.from"><td class="mono">{{ m.from }}</td><td>→</td><td class="mono">{{ m.to }}</td><td>{{ m.policy }}</td></tr>
              </table>
              <table class="tbl" v-if="lastPlan.errors.length" style="margin-top:8px">
                <tr><th>行</th><th>错误</th><th>处理</th></tr>
                <tr v-for="e in lastPlan.errors" :key="e.row"><td class="mono">第 {{ e.row }} 行</td><td>{{ e.error }}</td><td>{{ e.action }}</td></tr>
              </table>
              <div style="display:flex;gap:8px;margin-top:12px">
                <button class="btn btn-pri" @click="commit(lastPlan.batch_id)">{{ lastPlan.failed ? '保留成功部分并写入' : '确认写入' }}</button>
                <button class="btn btn-danger" @click="rollback(lastPlan.batch_id)">全部回滚</button>
              </div>
            </div>
            <div class="empty" v-else>上传文件后先生成导入计划，确认后才写入。支持 CSV / XLSX / JSON / MD / TXT / 音视频 / ZIP / 平台导出文件。</div>
          </div>
        </div>

        <div class="card">
          <div class="card-head"><div class="card-title">导入批次 <span class="card-sub">部分成功不会显示绿色「导入完成」</span></div></div>
          <div class="card-body" style="padding-top:4px">
            <div class="empty" v-if="!imports.batches.length">暂无导入批次</div>
            <div class="kv-row" v-for="b in imports.batches" :key="b.id">
              <span><b class="mono">{{ b.id }}</b> · {{ b.filename }} → {{ b.target_package }}<br>
                <span class="mini-note">成功 {{ b.success }} · 失败 {{ b.failed }} · {{ b.created_at.slice(5,16).replace('T',' ') }}</span></span>
              <span>
                <span class="badge" :class="batchBadge(b)[0]">{{ batchBadge(b)[1] }}</span>
                <button class="btn btn-sm btn-ghost" v-if="b.status==='parsed'||b.status==='partial'" @click="rollback(b.id)">回滚</button>
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">来源与引用追踪</div></div>
        <div class="card-body" style="padding-top:4px"><table class="tbl">
          <tr><th>资料</th><th>来源</th><th>所属包</th><th>已分析</th><th>被引用</th><th>删除影响</th></tr>
          <tr v-for="t in tracking" :key="t.name">
            <td>{{ t.name }}</td><td>{{ t.source }}</td><td>{{ t.package }}</td><td>{{ t.analyzed }}</td><td>{{ t.refs }}</td>
            <td><span class="badge" :class="t.level==='ok'?'b-ok':'b-warn'">{{ t.impact }}</span></td>
          </tr>
        </table></div>
      </div>
    </section>`,
  };

  /* ================= 系统设置 ================= */
  window.PageSettings = {
    data() {
      return {
        tab: 'execution', settings: {}, diag: null, errors: [], usage: null,
        ledger: [], traceQ: '', traceResult: null, saving: false,
        testingSource: false, testingLlm: false, testingAsr: false, loadingModels: false,
        sourceTest: { platform: 'douyin', link: '' },
        sourceResult: null, llmResult: null, asrResult: null, asrFile: null, availableModels: [],
        secretDrafts: { source: '', llm: '', voice: '' },
        tabs: [
          ['execution', '运行模式'], ['source', '数据来源'], ['llm', '大模型'], ['voice', '语音模型'],
          ['cost', '成本控制'], ['local', '本地数据'], ['diag', '系统诊断'],
        ],
      };
    },
    async mounted() {
      await this.load();
      const requested = new URLSearchParams(location.search).get('settings');
      if (this.tabs.some((item) => item[0] === requested)) await this.switchTab(requested);
    },
    methods: {
      async load() {
        try {
          this.settings = await api.get('/api/settings');
          this.usage = await api.get('/api/usage/summary');
        } catch (e) { handleError(e); }
      },
      async loadDiag() {
        try {
          this.diag = await api.get('/api/diagnostics');
          this.errors = await api.get('/api/diagnostics/errors');
          this.ledger = await api.get('/api/usage/ledger');
        } catch (e) { handleError(e); }
      },
      async switchTab(t) {
        this.tab = t;
        if (t === 'diag') await this.loadDiag();
      },
      async save(section, showToast = true) {
        this.saving = true;
        try {
          const values = { ...this.settings[section] };
          delete values.tikhub_api_key_configured;
          delete values.api_key_configured;
          if (section === 'source' && this.secretDrafts.source) values.tikhub_api_key = this.secretDrafts.source;
          if (section === 'llm' && this.secretDrafts.llm) values.api_key = this.secretDrafts.llm;
          if (section === 'voice' && this.secretDrafts.voice) values.api_key = this.secretDrafts.voice;
          const r = await api.put('/api/settings', { section, values });
          if (section === 'source' && this.settings.datasource) {
            await api.put('/api/settings', { section: 'datasource', values: this.settings.datasource });
          }
          this.secretDrafts[section] = '';
          await this.load();
          if (section === 'execution') store.runtime.demo_mode = r.demo_mode;
          if (showToast) toast(r.note);
          this.saving = false;
          return true;
        } catch (e) { handleError(e); }
        this.saving = false;
        return false;
      },
      async setMode(mode) {
        this.settings.execution.mode = mode;
        if (await this.save('execution', false)) {
          toast(mode === 'real' ? '已切换到真实模式；新的外部任务将使用已配置 Provider。' : '已切换到演示模式。');
        }
      },
      async testSource() {
        if (!this.sourceTest.link.trim()) return toast('请先填写一个公开账号主页或账号 ID', 'warn');
        if (!(await this.save('source', false))) return;
        this.testingSource = true; this.sourceResult = null;
        try {
          this.sourceResult = await api.post('/api/datasource/check', {
            platform: this.sourceTest.platform, link: this.sourceTest.link.trim(),
          });
          toast(this.sourceResult.note);
        } catch (e) { handleError(e); }
        this.testingSource = false;
      },
      async loadModels() {
        if (!(await this.save('llm', false))) return;
        this.loadingModels = true; this.llmResult = null;
        try {
          const result = await api.get('/api/llm/models');
          this.availableModels = result.models || [];
          this.llmResult = { ok: true, note: result.note };
          toast(result.note);
        } catch (e) { handleError(e); }
        this.loadingModels = false;
      },
      async testLlm() {
        if (!(await this.save('llm', false))) return;
        this.testingLlm = true; this.llmResult = null;
        try {
          const result = await api.post('/api/llm/check', { model: this.settings.llm.fast_model });
          this.llmResult = { ok: true, note: result.note, model: result.model };
          toast(result.note);
        } catch (e) { handleError(e); }
        this.testingLlm = false;
      },
      pickAsrFile(event) { this.asrFile = event.target.files && event.target.files[0]; },
      async testAsr() {
        if (!this.asrFile) return toast('请先选择一个 10 MB 以内的音频文件', 'warn');
        if (!(await this.save('voice', false))) return;
        this.testingAsr = true; this.asrResult = null;
        try {
          const form = new FormData(); form.append('file', this.asrFile);
          this.asrResult = await api.upload('/api/stt/check', form);
          toast(this.asrResult.note);
        } catch (e) { handleError(e); }
        this.testingAsr = false;
      },
      async queryTrace() {
        if (!this.traceQ.trim()) return;
        try {
          this.traceResult = await api.get('/api/diagnostics/trace/' + this.traceQ.trim());
          if (!this.traceResult.errors.length && !this.traceResult.jobs.length) toast('未找到该 Trace ID 记录', 'warn');
        } catch (e) { handleError(e); }
      },
      checkBadge(s) { return s === 'ok' ? 'b-ok' : s === 'warn' ? 'b-warn' : 'b-err'; },
      fieldItems(section) {
        const v = this.settings[section];
        if (!v) return [];
        const labels = {
          tikhub_api_key: 'TikHub API Key（仅存本机 · 脱敏显示）', sync_frequency: '同步频率',
          daily_request_limit: '每日请求限额', cache_policy: '缓存策略',
          stt_provider: 'STT 提供商', tts_provider: 'TTS 提供商', interview_mode: '默认访谈模式',
          language: '转写语言', hot_words: '热词词典',
          fast_model: '快速分类模型', analysis_model: '深度分析模型', writing_model: '文案生成模型',
          structured_model: '结构化抽取模型', embedding_model: 'Embedding 模型',
          api_base: 'API 地址', api_key: 'API Key（仅存本机）',
          daily_budget: '每日预算（¥）', monthly_budget: '每月预算（¥）',
          confirm_threshold: '单次任务确认阈值（¥）', max_media_minutes: '媒体最长（分钟）',
          max_comments: '评论最多（条）', cache_days: '缓存有效期（天）',
          weekly_capacity_hours: '每周内容产能（小时）',
          auto_backup: '自动备份', theme: '主题（light / dark）',
        };
        return Object.keys(v).filter((k) => !k.endsWith('_configured'))
          .map((k) => ({ key: k, label: labels[k] || k, value: v[k] }));
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">系统设置</div><div class="page-q">配置数据源、模型、成本、安全和诊断。</div></div>
      </div>

      <div class="tabs">
        <button class="tab" v-for="t in tabs" :key="t[0]" :class="{active:tab===t[0]}" :aria-pressed="tab===t[0]" @click="switchTab(t[0])">{{ t[1] }}
          <span class="badge b-err" v-if="t[0]==='diag' && errors.length" style="margin-left:2px">{{ errors.length }}</span></button>
      </div>

      <div class="settings-mode-grid section-gap" v-if="tab==='execution' && settings.execution">
        <button type="button" class="settings-mode-card" :class="{active:settings.execution.mode==='demo'}" :aria-pressed="settings.execution.mode==='demo'" @click="setMode('demo')">
          <span class="settings-mode-check">{{ settings.execution.mode==='demo' ? '✓' : '' }}</span><b>演示模式</b><small>使用明确标记的内置数据，不产生外部调用费用。</small>
        </button>
        <button type="button" class="settings-mode-card" :class="{active:settings.execution.mode==='real'}" :aria-pressed="settings.execution.mode==='real'" @click="setMode('real')">
          <span class="settings-mode-check">{{ settings.execution.mode==='real' ? '✓' : '' }}</span><b>真实模式</b><small>立即使用你保存的 TikHub 与模型 Provider；配置缺失时显式失败。</small>
        </button>
        <div class="card settings-mode-summary"><div class="card-body">
          <div class="settings-section-title">当前生效状态 <span class="badge" :class="store.runtime.demo_mode?'b-vio':'b-ok'">{{ store.runtime.demo_mode ? '演示' : '真实' }}</span></div>
          <p>切换只影响之后发起的任务，不删除已有数据。真实模式下不会自动回退到演示结果。</p>
          <div class="settings-readiness"><span>数据源 Key <b>{{ settings.source && settings.source.tikhub_api_key_configured ? '已保存' : '未保存' }}</b></span><span>模型 Key <b>{{ settings.llm && (settings.llm.auth_mode==='none'||settings.llm.api_key_configured) ? '已就绪' : '未保存' }}</b></span><span>模型地址 <b>{{ settings.llm && settings.llm.api_base ? '已填写' : '未填写' }}</b></span></div>
        </div></div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='source' && settings.source && settings.datasource">
        <div class="card-body">
          <div class="settings-section-head"><div><div class="settings-section-title">TikHub 数据源</div><p>保存后使用公开账号做一次真实连通测试；测试可能产生接口费用。</p></div><span class="badge" :class="settings.source.tikhub_api_key_configured?'b-ok':'b-warn'">{{ settings.source.tikhub_api_key_configured ? 'Key 已保存' : '待配置' }}</span></div>
          <div class="grid g2">
            <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.datasource.providers.tikhub.baseUrl" placeholder="https://api.tikhub.io"><small>大陆网络可使用 https://api.tikhub.dev</small></label>
            <label class="field"><span>TikHub API Key（DPAPI 加密）</span><input class="input mono" type="password" v-model="secretDrafts.source" :placeholder="settings.source.tikhub_api_key_configured ? '已配置 · 留空表示不修改' : '粘贴 API Key'"><small>密钥不会通过设置接口回传。</small></label>
            <label class="field"><span>测试平台</span><select class="input" v-model="sourceTest.platform"><option value="douyin">抖音</option><option value="xhs">小红书</option><option value="bilibili">B站</option><option value="kuaishou">快手</option><option value="weibo">微博</option><option value="channels">视频号</option><option value="tiktok">TikTok</option></select></label>
            <label class="field"><span>公开账号主页 / 账号 ID</span><input class="input" v-model="sourceTest.link" placeholder="用于真实解析和账号资料测试"><small>不会再使用无效的 check_probe。</small></label>
          </div>
          <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('source')">保存数据源</button><button class="btn" :disabled="testingSource||saving" @click="testSource">{{ testingSource ? '正在测试…' : '保存并真实测试' }}</button></div>
          <div class="callout c-ok" role="status" aria-live="polite" v-if="sourceResult"><span>✓</span><span>{{ sourceResult.note }}<template v-if="sourceResult.account_name"> · 账号：{{ sourceResult.account_name }}</template></span></div>
        </div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='llm' && settings.llm">
        <div class="card-body">
          <div class="settings-section-head"><div><div class="settings-section-title">OpenAI Compatible 模型服务</div><p>支持 OpenAI、兼容中转服务和显式授权的本地服务。模型列表不可用时仍可手填名称。</p></div><span class="badge" :class="settings.llm.api_key_configured||settings.llm.auth_mode==='none'?'b-ok':'b-warn'">{{ settings.llm.api_key_configured||settings.llm.auth_mode==='none' ? '鉴权已就绪' : '待配置 Key' }}</span></div>
          <div class="callout c-info"><span>ⓘ</span><span>保存配置本身不调用模型；“读取模型列表”和“最小生成测试”会发起真实请求，后者可能计费。</span></div>
          <div class="grid g2 settings-config-grid">
            <label class="field"><span>协议</span><select class="input" v-model="settings.llm.protocol"><option value="openai_compatible">OpenAI Compatible · Chat Completions</option></select></label>
            <label class="field"><span>鉴权方式</span><select class="input" v-model="settings.llm.auth_mode"><option value="bearer">Bearer API Key</option><option value="none">无需鉴权（本地服务）</option></select></label>
            <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.llm.api_base" placeholder="https://api.openai.com/v1"><small>填写到 /v1；只填域名时系统会自动补齐。</small></label>
            <label class="field"><span>API Key（DPAPI 加密）</span><input class="input mono" type="password" :disabled="settings.llm.auth_mode==='none'" v-model="secretDrafts.llm" :placeholder="settings.llm.api_key_configured ? '已配置 · 留空表示不修改' : '粘贴模型 API Key'"><small>{{ settings.llm.auth_mode==='none' ? '当前服务不发送 Authorization 请求头。' : '仅由当前 Windows 用户解密。' }}</small></label>
          </div>
          <label class="settings-private-toggle"><input type="checkbox" v-model="settings.llm.allow_private_network"><span><b>允许访问本机/私有网络模型服务</b><small>仅在使用 Ollama、LM Studio 等可信本地服务时开启；开启后允许 HTTP。</small></span></label>
          <div class="settings-model-heading"><div><b>任务模型绑定</b><span>每个任务可以使用不同的模型名称</span></div><span v-if="availableModels.length">已读取 {{ availableModels.length }} 个模型</span></div>
          <datalist id="llmModelOptions"><option v-for="model in availableModels" :value="model"></option></datalist>
          <div class="settings-model-grid">
            <label class="field"><span>快速分类 / 指令路由</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.fast_model"><small>工作总台指令解析</small></label>
            <label class="field"><span>深度分析模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.analysis_model"><small>内容拆解、对标作者分析</small></label>
            <label class="field"><span>写作模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.writing_model"><small>方向、提纲、初稿</small></label>
            <label class="field"><span>结构化抽取模型</span><input class="input mono" list="llmModelOptions" v-model="settings.llm.structured_model"><small>访谈字段与 JSON 输出</small></label>
            <label class="field settings-field-pending"><span>Embedding 模型 <em>尚未接入</em></span><input class="input mono" v-model="settings.llm.embedding_model"><small>当前仅保存名称，不会伪装成已调用。</small></label>
          </div>
          <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('llm')">保存模型配置</button><button class="btn" :disabled="loadingModels||saving" @click="loadModels">{{ loadingModels ? '正在读取…' : '保存并读取模型列表' }}</button><button class="btn" :disabled="testingLlm||saving" @click="testLlm">{{ testingLlm ? '正在生成…' : '测试快速模型' }}</button></div>
          <div class="callout c-ok" role="status" aria-live="polite" v-if="llmResult && llmResult.ok"><span>✓</span><span>{{ llmResult.note }}</span></div>
        </div>
      </div>

      <div class="card section-gap settings-provider-card" v-if="tab==='voice' && settings.voice"><div class="card-body">
        <div class="settings-section-head"><div><div class="settings-section-title">阿里云百炼语音识别</div><p>已接通 qwen3-asr-flash 的真实音频转写；测试会发起一次可能计费的请求。TTS 仍保持明确未实现。</p></div><span class="badge" :class="settings.voice.api_key_configured?'b-ok':'b-warn'">{{ settings.voice.api_key_configured ? 'ASR Key 已保存' : '待配置 Key' }}</span></div>
        <div class="grid g2 settings-config-grid">
          <label class="field"><span>STT Provider</span><select class="input" v-model="settings.voice.stt_provider"><option value="aliyun_bailian">阿里云百炼</option></select></label>
          <label class="field"><span>协议</span><select class="input" v-model="settings.voice.protocol"><option value="openai_compatible_audio">OpenAI Compatible Audio</option></select></label>
          <label class="field"><span>API Base URL</span><input class="input mono" v-model="settings.voice.api_base"></label>
          <label class="field"><span>API Key（DPAPI 加密）</span><input class="input mono" type="password" v-model="secretDrafts.voice" :placeholder="settings.voice.api_key_configured ? '已配置 · 留空表示不修改' : '粘贴百炼 API Key'"></label>
          <label class="field"><span>ASR 模型</span><input class="input mono" v-model="settings.voice.asr_model"></label>
          <label class="field"><span>转写语言</span><select class="input" v-model="settings.voice.language"><option value="zh">中文</option><option value="en">英语</option><option value="yue">粤语</option></select></label>
          <label class="field"><span>热词词典</span><input class="input" v-model="settings.voice.hot_words"></label>
          <label class="field"><span>TTS Provider · 尚未接入</span><input class="input" disabled v-model="settings.voice.tts_provider"><small>不会显示成已生效。</small></label>
          <label class="field"><span>真实测试音频（≤10 MB）</span><input class="input" type="file" accept="audio/*,video/mp4" @change="pickAsrFile"><small>音频仅用于本次真实转写测试。</small></label>
        </div>
        <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save('voice')">保存 ASR 配置</button><button class="btn" :disabled="saving||testingAsr" @click="testAsr">{{ testingAsr ? '正在转写…' : '保存并测试音频' }}</button></div>
        <div class="callout c-ok" role="status" aria-live="polite" v-if="asrResult"><span>✓</span><span>{{ asrResult.note }} · {{ asrResult.model }}<br>{{ asrResult.text }}</span></div>
      </div></div>

      <div class="card section-gap" v-if="['cost','local'].includes(tab) && settings[tab]"><div class="card-body">
        <div class="grid g2"><div class="field" v-for="f in fieldItems(tab)" :key="f.key"><label>{{ f.label }}</label><input class="input" v-model="settings[tab][f.key]"></div></div>
        <div class="settings-actions"><button class="btn btn-pri" :disabled="saving" @click="save(tab)">保存本分区</button><span class="mini-note" v-if="tab==='cost' && usage">今日已用 ¥{{ usage.today.toFixed(2) }} / ¥{{ usage.daily_budget }} · 本月 ¥{{ usage.month.toFixed(2) }} / ¥{{ usage.monthly_budget }}</span></div>
      </div></div>

      <template v-if="tab==='diag'">
        <div class="card section-gap"><div class="card-body" style="padding-top:14px">
          <div class="kv-row" v-for="c in (diag ? diag.checks : [])" :key="c.name">
            <span>{{ c.name }}</span>
            <span><span class="badge" :class="checkBadge(c.status)">{{ c.detail }}</span></span>
          </div>
          <div class="loading" v-if="!diag">探测中</div>
        </div></div>
        <div class="card section-gap">
          <div class="card-head"><div class="card-title">最近错误与任务日志</div>
            <div style="display:flex;gap:6px">
              <input class="input mono" style="width:220px;height:28px" placeholder="输入 Trace ID 查询" v-model="traceQ" @keydown.enter="queryTrace">
              <button class="btn btn-sm" @click="queryTrace">查询</button>
            </div></div>
          <div class="card-body" style="padding-top:4px">
            <div v-if="traceResult" class="callout c-info">
              <span>→</span><span>Trace <b class="mono">{{ traceQ }}</b>：错误 {{ traceResult.errors.length }} 条 · 关联任务 {{ traceResult.jobs.length }} 个
                <template v-if="traceResult.jobs.length">（{{ traceResult.jobs.map(j=>j.id+'/'+j.status).join('、') }}）</template></span>
            </div>
            <table class="tbl">
              <tr><th>时间</th><th>错误码</th><th>阶段</th><th>Provider</th><th>Trace ID</th></tr>
              <tr v-for="e in errors" :key="e.id">
                <td class="mono">{{ e.ts.slice(5,16).replace('T',' ') }}</td>
                <td class="mono" style="color:var(--err)">{{ e.code }}</td>
                <td>{{ e.stage }}</td><td>{{ e.provider }}</td>
                <td><span class="trace mono" @click="bus.emit('open-error',{code:e.code,message:e.message,stage:e.stage,provider:e.provider,retryable:false,trace_id:e.trace_id,details:{}})">{{ e.trace_id.slice(0,14) }}…</span></td>
              </tr>
              <tr v-if="!errors.length"><td colspan="5" class="empty">暂无错误记录</td></tr>
            </table>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">Usage Ledger <span class="card-sub">每次外部调用均记录（含失败是否收费）</span></div></div>
          <div class="card-body" style="padding-top:4px"><table class="tbl">
            <tr><th>时间</th><th>任务</th><th>Provider</th><th>接口</th><th>费用</th></tr>
            <tr v-for="u in ledger" :key="u.id">
              <td class="mono">{{ u.ts.slice(5,16).replace('T',' ') }}</td><td>{{ u.task }}</td>
              <td class="mono">{{ u.provider }}</td><td>{{ u.endpoint }}</td>
              <td class="mono">¥{{ u.cost.toFixed(2) }}</td>
            </tr>
          </table></div>
        </div>
      </template>
    </section>`,
  };
})();
