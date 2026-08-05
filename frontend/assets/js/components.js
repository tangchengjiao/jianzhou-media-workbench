/* 全局共享组件：执行计划弹层 / 错误详情 / 来源追溯 / 规则二次确认 / 切换创作者 / 发布登记。 */
(function () {
  const app = () => window.__app;

  /* ---------- 执行计划确认（单次确认 · ASR 授权默认不勾选） ---------- */
  window.PlanModal = {
    data() {
      return { open: false, plan: null, asrAuth: false, submitting: false };
    },
    created() { bus.on('open-plan', (p) => { this.plan = p; this.asrAuth = false; this.open = true; }); },
    methods: {
      close() { this.open = false; },
      async confirm() {
        this.submitting = true;
        try {
          const r = await api.post('/api/extraction/jobs', {
            plan: this.plan, authorizations: { asr_when_no_caption: this.asrAuth },
          });
          toast(r.note || ('任务已创建 · ' + r.job_id));
          this.close();
          bus.emit('jobs-changed');
          refreshTopbar();
        } catch (e) { handleError(e); }
        this.submitting = false;
      },
    },
    template: `
    <div class="modal" :class="{open}" @click.self="close" @keydown.esc="close">
      <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="planModalTitle">
        <div class="modal-head"><div class="modal-title" id="planModalTitle">执行计划确认 <span class="badge b-warn">单次确认</span></div>
          <button class="modal-x" aria-label="关闭执行计划" @click="close">✕</button></div>
        <div class="modal-body" v-if="plan">
          <div class="callout c-info" style="margin-bottom:12px"><span>▶</span><span>
            来源：{{ plan.source.platform }}《{{ plan.source.title }}》
            <template v-if="plan.source.signal_id"> · 雷达 #{{ plan.source.signal_id }}</template>
            <template v-if="store.runtime.demo_mode"> · <b>演示数据</b></template>
          </span></div>
          <div class="grid g2">
            <div>
              <div class="ana-label">执行步骤</div>
              <div class="plan-step" v-for="(s,i) in plan.steps" :key="i"><i>{{ i+1 }}</i>{{ s }}</div>
              <div class="field" style="margin-top:8px">
                <label style="display:flex;gap:6px;align-items:center;cursor:pointer">
                  <input type="checkbox" v-model="asrAuth">
                  {{ plan.source.asset_id ? '使用已上传音频执行百炼 ASR' : '无原生字幕时继续下载媒体并 ASR' }}（本次任务内明确授权 · 默认不勾选）
                </label>
              </div>
            </div>
            <div>
              <div class="ana-label">成本与产物</div>
              <div class="kv-row" v-for="(v,k) in plan.cost" :key="k"><span>{{ k }}</span><b style="text-align:right">{{ v }}</b></div>
            </div>
          </div>
          <div class="mini-note" style="margin-top:10px">确认后创建 Job（QUEUED）。原始字幕与原始 ASR 不可覆盖；平台描述永远不会冒充完整转写。</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="close">取消</button>
          <button class="btn btn-pri" :disabled="submitting" @click="confirm">确认执行 · {{ plan && plan.cost['预计费用'] }}</button>
        </div>
      </div>
    </div>`,
  };

  /* ---------- Typed Error 详情 ---------- */
  window.ErrorModal = {
    data() { return { open: false, err: null }; },
    created() { bus.on('open-error', (e) => { this.err = e; this.open = true; }); },
    methods: {
      close() { this.open = false; },
      copyTrace() {
        navigator.clipboard && navigator.clipboard.writeText(this.err.trace_id || '');
        toast('Trace ID 已复制');
      },
      goSettings() { this.close(); app() && app().go('settings'); },
    },
    template: `
    <div class="modal" :class="{open}" @click.self="close">
      <div class="modal-card">
        <div class="modal-head"><div class="modal-title">任务失败详情 <span class="err-code">{{ err && err.code }}</span></div>
          <button class="modal-x" @click="close">✕</button></div>
        <div class="modal-body" v-if="err">
          <div class="callout c-err"><span>✕</span><span>{{ err.message }}<br>
            <b>系统没有伪造成功、没有返回假数据、没有自动切换提供商。</b></span></div>
          <div class="kv-row"><span>失败阶段</span><b class="mono">{{ err.stage || '—' }}</b></div>
          <div class="kv-row"><span>Provider</span><b class="mono">{{ err.provider || '—' }}</b></div>
          <div class="kv-row"><span>可重试</span><b>{{ err.retryable ? '是' : '否' }}</b></div>
          <div class="kv-row" v-if="err.details && err.details.charged !== undefined">
            <span>是否产生费用</span><b>{{ err.details.charged ? '是' : '否' }}</b></div>
          <div class="kv-row" v-if="err.details && err.details.fix"><span>修复动作</span><span>{{ err.details.fix }}</span></div>
          <div class="mini-note" style="margin-top:10px">Trace ID <b class="mono">{{ err.trace_id }}</b>
            <span class="trace" @click="copyTrace">复制</span> · 可在系统设置 → 系统诊断中查询完整链路</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="close">关闭</button>
          <button class="btn btn-pri" @click="goSettings">去修复配置</button>
        </div>
      </div>
    </div>`,
  };

  /* ---------- 来源追溯 ---------- */
  window.TraceModal = {
    data() { return { open: false, title: '', src: '', quote: '' }; },
    created() {
      bus.on('open-trace', (p) => { this.title = p.title; this.src = p.src; this.quote = p.quote; this.open = true; });
    },
    methods: { close() { this.open = false; } },
    template: `
    <div class="modal" :class="{open}" @click.self="close">
      <div class="modal-card" style="width:520px">
        <div class="modal-head"><div class="modal-title">来源追溯 · {{ title }}</div>
          <button class="modal-x" @click="close">✕</button></div>
        <div class="modal-body">
          <div class="kv-row"><span>直接来源</span><b style="font-size:12px">{{ src }}</b></div>
          <div class="ana-label" style="margin-top:10px">证据原文 / 说明</div>
          <div style="background:var(--line2);border-radius:10px;padding:12px;font-size:12.5px;line-height:1.8">{{ quote }}</div>
          <div class="mini-note" style="margin-top:10px">所有结构化字段必须能回溯到访谈原文或导入资料；模型推断（inferred）未经确认不进入正式档案。</div>
        </div>
      </div>
    </div>`,
  };

  /* ---------- 规则生效二次确认 ---------- */
  window.RuleModal = {
    data() { return { open: false, suggestion: null, busy: false }; },
    created() { bus.on('open-rule', (s) => { this.suggestion = s; this.open = true; }); },
    methods: {
      close() { this.open = false; },
      async approve() {
        this.busy = true;
        try {
          const r = await api.post('/api/rules/suggestions/' + this.suggestion.id + '/approve');
          toast(r.note); this.close(); bus.emit('rules-changed');
        } catch (e) { handleError(e); }
        this.busy = false;
      },
    },
    template: `
    <div class="modal" :class="{open}" @click.self="close">
      <div class="modal-card">
        <div class="modal-head"><div class="modal-title">规则生效确认 <span class="badge b-err">二次确认</span></div>
          <button class="modal-x" @click="close">✕</button></div>
        <div class="modal-body" v-if="suggestion">
          <div class="callout c-warn"><span>⚠</span><span>确认后规则将写入<b>规则经验包新版本 v2.1</b> 并立即影响 {{ suggestion.scope }}。此操作可回滚，但会留下版本记录。</span></div>
          <div class="kv-row"><span>规则内容</span><b style="text-align:right">{{ suggestion.rule_text }}</b></div>
          <div class="kv-row"><span>动作</span><span class="badge b-info">{{ suggestion.action }}</span></div>
          <div class="kv-row"><span>证据</span><span style="text-align:right">{{ suggestion.evidence }}</span></div>
          <div class="kv-row"><span>置信度</span><b class="mono">{{ suggestion.confidence }}</b></div>
          <div class="kv-row"><span>影响范围</span><span style="text-align:right">{{ suggestion.impact }}</span></div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="close">再想想</button>
          <button class="btn btn-pri" :disabled="busy" @click="approve">我确认依据充分，同意生效</button>
        </div>
      </div>
    </div>`,
  };

  /* ---------- 切换创作者（二次确认） ---------- */
  window.SwitchModal = {
    data() { return { open: false, preview: null, busy: false }; },
    created() {
      bus.on('open-switch', async (uid) => {
        try { this.preview = await api.get('/api/users/' + uid + '/switch-preview'); this.open = true; }
        catch (e) { handleError(e); }
      });
    },
    methods: {
      close() { this.open = false; },
      async confirm() {
        this.busy = true;
        try {
          const r = await api.post('/api/users/' + this.preview.target.id + '/activate');
          toast(r.note, 'warn'); this.close();
          await refreshTopbar();
          app() && app().go('workbench');
        } catch (e) { handleError(e); }
        this.busy = false;
      },
    },
    template: `
    <div class="modal" :class="{open}" @click.self="close">
      <div class="modal-card" style="width:560px">
        <div class="modal-head"><div class="modal-title">切换创作者 <span class="badge b-err">二次确认</span></div>
          <button class="modal-x" @click="close">✕</button></div>
        <div class="modal-body" v-if="preview">
          <div class="callout c-warn"><span>⚠</span><span>{{ preview.note }}</span></div>
          <div class="ana-label">将关闭的数据包（当前创作者）</div>
          <div class="chips" style="margin-bottom:10px"><span class="chip" v-for="p in preview.will_close" :key="p.type">{{ p.name }}</span>
            <span class="mini-note" v-if="!preview.will_close.length">（无）</span></div>
          <div class="ana-label">将激活的数据包（{{ preview.target.name }}）</div>
          <div class="chips"><span class="chip" v-for="p in preview.will_activate" :key="p.type">{{ p.name }}</span>
            <span class="mini-note" v-if="!preview.will_activate.length">（该创作者暂无数据包，将从头建立）</span></div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="close">取消</button>
          <button class="btn btn-pri" :disabled="busy" @click="confirm">确认切换</button>
        </div>
      </div>
    </div>`,
  };

  /* ---------- 发布登记 ---------- */
  window.PublishModal = {
    data() { return { open: false, task: null, form: { platform: '抖音', url: '', final_title: '', note: '' }, busy: false }; },
    created() {
      bus.on('open-publish', (task) => {
        this.task = task;
        this.form = { platform: task.platform || '抖音', url: '', final_title: task.title, note: '' };
        this.open = true;
      });
    },
    methods: {
      close() { this.open = false; },
      async submit() {
        if (!this.form.url.trim()) { toast('请填写发布链接', 'warn'); return; }
        this.busy = true;
        try {
          const r = await api.post('/api/publications', { task_id: this.task.id, ...this.form });
          toast(r.note); this.close(); bus.emit('board-changed');
        } catch (e) { handleError(e); }
        this.busy = false;
      },
    },
    template: `
    <div class="modal" :class="{open}" @click.self="close">
      <div class="modal-card" style="width:520px">
        <div class="modal-head"><div class="modal-title">发布登记 <span class="badge b-info">手动登记 · MVP 不自动发布</span></div>
          <button class="modal-x" @click="close">✕</button></div>
        <div class="modal-body" v-if="task">
          <div class="field"><label>内容</label><div style="font-weight:600">{{ task.title }}</div></div>
          <div class="field"><label>发布平台</label><input class="input" v-model="form.platform"></div>
          <div class="field"><label>发布链接（必填）</label><input class="input" v-model="form.url" placeholder="https://..."></div>
          <div class="field"><label>最终标题</label><input class="input" v-model="form.final_title"></div>
          <div class="field"><label>临时修改说明</label><input class="input" v-model="form.note"></div>
          <div class="mini-note">登记后自动创建 T+1 / T+3 / T+7 公开数据快照计划。</div>
        </div>
        <div class="modal-foot">
          <button class="btn" @click="close">取消</button>
          <button class="btn btn-pri" :disabled="busy" @click="submit">登记并创建快照计划</button>
        </div>
      </div>
    </div>`,
  };
})();
