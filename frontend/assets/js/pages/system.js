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
        tab: 'source', settings: {}, diag: null, errors: [], usage: null,
        ledger: [], traceQ: '', traceResult: null, saving: false,
        tabs: [
          ['source', '数据来源'], ['voice', '语音模型'], ['llm', '大模型'],
          ['cost', '成本控制'], ['local', '本地数据'], ['diag', '系统诊断'],
        ],
      };
    },
    async mounted() { await this.load(); },
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
      async save(section) {
        this.saving = true;
        try {
          const r = await api.put('/api/settings', { section, values: this.settings[section] });
          toast(r.note);
        } catch (e) { handleError(e); }
        this.saving = false;
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
          auto_backup: '自动备份', theme: '主题（light / dark）',
        };
        return Object.keys(v).map((k) => ({ key: k, label: labels[k] || k, value: v[k] }));
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">系统设置</div><div class="page-q">配置数据源、模型、成本、安全和诊断。</div></div>
      </div>

      <div class="tabs">
        <button class="tab" v-for="t in tabs" :key="t[0]" :class="{active:tab===t[0]}" @click="switchTab(t[0])">{{ t[1] }}
          <span class="badge b-err" v-if="t[0]==='diag' && errors.length" style="margin-left:2px">{{ errors.length }}</span></button>
      </div>

      <div class="card section-gap" v-if="tab!=='diag'">
        <div class="card-body">
          <div class="callout c-warn" v-if="tab==='voice'" style="margin-bottom:12px"><span>⚠</span><span>语音模式失败<b>不会</b>静默切换为文字并继续写档案；适配器失败也<b>不会</b>自动调用另一个适配器。</span></div>
          <div class="callout c-info" v-if="tab==='source'" style="margin-bottom:12px"><span>ⓘ</span><span>自动同步仅限公开元数据与快照；字幕收费接口、评论分页、媒体下载均需任务级确认。<template v-if="store.runtime.demo_mode"> 当前为<b>演示模式</b>，数据源为 demo_tikhub。</template></span></div>
          <div class="callout c-info" v-if="tab==='llm'" style="margin-bottom:12px"><span>ⓘ</span><span>所有写库型 LLM 输出必须通过 Schema 校验；校验失败 = 任务失败，禁止自动构造缺失字段。模型不可用时直接报错，<b>不会</b>自动切换。</span></div>
          <div class="grid g2">
            <div class="field" v-for="f in fieldItems(tab)" :key="f.key">
              <label>{{ f.label }}</label>
              <input class="input" :class="{mono: f.key.includes('key')||f.key.includes('base')}" v-model="settings[tab][f.key]">
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn btn-pri" :disabled="saving" @click="save(tab)">保存本分区</button>
            <span class="mini-note" v-if="tab==='cost' && usage">今日已用 ¥{{ usage.today.toFixed(2) }} / ¥{{ usage.daily_budget }} · 本月 ¥{{ usage.month.toFixed(2) }} / ¥{{ usage.monthly_budget }}（已用量只读展示）</span>
          </div>
        </div>
      </div>

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
