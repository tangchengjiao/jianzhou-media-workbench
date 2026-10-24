/* 页面：数据复盘 / 成长记录 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 数据复盘 ================= */
  window.PageRetro = {
    data() { return { overview: null, due: [], report: null, suggestions: [], experiments: [], activePub: null,
      snapshot: { tn: 'T+1', plays: 0, likes: 0, comments: 0, collects: 0, shares: 0, follows: 0, completion_rate: null }, savingSnapshot: false }; },
    async mounted() { await this.load(); bus.on('rules-changed', this.loadRules); },
    methods: {
      go,
      async load() {
        try {
          const [o, d, s, e] = await Promise.all([
            api.get('/api/retro/overview'), api.get('/api/retro/due'),
            api.get('/api/rules/suggestions'), api.get('/api/retro/experiments'),
          ]);
          this.overview = o; this.due = d; this.suggestions = s; this.experiments = e;
          const requested = new URLSearchParams(location.search).get('publication');
          const target = d.find((x) => x.publication_id === (requested || this.activePub))
            || d.find((x) => x.publication_id);
          if (target) await this.openReport(target.publication_id);
        } catch (e) { handleError(e); }
      },
      async loadRules() { try { this.suggestions = await api.get('/api/rules/suggestions'); } catch (e) {} },
      async openReport(pubId) {
        try { this.report = await api.get('/api/retro/report/' + pubId); this.activePub = pubId; }
        catch (e) { handleError(e); }
      },
      async saveSnapshot() {
        if (!this.activePub) return toast('请先选择一条发布记录', 'warn');
        this.savingSnapshot = true;
        try {
          const r = await api.post('/api/retro/publications/' + this.activePub + '/snapshots', this.snapshot);
          toast(r.note); await this.openReport(this.activePub); await this.load();
        } catch (e) { handleError(e); }
        this.savingSnapshot = false;
      },
      async generateRetro() {
        if (!this.activePub) return;
        try {
          const r = await api.post('/api/retro/publications/' + this.activePub + '/generate');
          toast(r.deduplicated ? r.note : r.note || '真实快照复盘已生成');
          await this.openReport(this.activePub); await this.loadRules(); await this.load();
        } catch (e) { handleError(e); }
      },
      reviewRule(s) { bus.emit('open-rule', s); },
      async rejectRule(s) { try { const r = await api.post('/api/rules/suggestions/' + s.id + '/reject'); toast(r.note, 'warn'); await this.loadRules(); } catch (e) { handleError(e); } },
      async observeRule(s) { try { const r = await api.post('/api/rules/suggestions/' + s.id + '/observe'); toast(r.note, 'warn'); await this.loadRules(); } catch (e) { handleError(e); } },
      actionBadge(a) { return { create: 'b-info', update: 'b-vio', deprecate: 'b-neu', observe: 'b-warn' }[a] || 'b-neu'; },
      statusLabel(s) { return { pending: '待审核', accepted: '已接受', rejected: '已拒绝', observing: '观察中' }[s] || s; },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">数据复盘</div><div class="page-q">这条内容是否完成了它原本的目标，为什么？</div></div>
        <div class="mini-note">发布前判断只读 · 规则不自动生效</div>
      </div>

      <div class="grid g4 section-gap" v-if="overview">
        <div class="stat"><div class="stat-l">真实发布</div><div class="stat-v">{{ overview.publications }}</div></div>
        <div class="stat"><div class="stat-l">已存快照</div><div class="stat-v">{{ overview.snapshots }}</div></div>
        <div class="stat"><div class="stat-l">待采集快照</div><div class="stat-v">{{ overview.snapshots_pending }}</div></div>
        <div class="stat"><div class="stat-l">判断校准率</div><div class="stat-v">{{ overview.calibration }}</div></div>
      </div>

      <div class="card section-gap" v-if="due.length">
        <div class="card-head"><div class="card-title">发布与快照证据链</div><span class="card-sub">无授权采集 Provider 时等待你录入真实指标</span></div>
        <div class="card-body">
          <div class="task-row" v-for="item in due" :key="item.publication_id">
            <div class="tr-body"><div class="tr-title">{{ item.final_title }}</div><div class="tr-meta">{{ item.publication_platform }} · {{ ({PUBLISHED:'已发布',WAITING_RETRO:'待复盘',RETRO_COMPLETED:'复盘完成',ARCHIVED:'已归档'})[item.status] || item.status }} · {{ item.schedules.map(s=>s.tn+':'+s.status).join(' · ') }}<template v-if="item.latest_report"> · 复盘 v{{ item.latest_report.version_no }} {{ item.latest_report.stage==='FINAL'?'最终版':'阶段版' }}</template></div></div>
            <button class="btn btn-sm" @click="openReport(item.publication_id)">选择</button>
          </div>
          <div class="grid g4" v-if="activePub" style="margin-top:12px">
            <label class="field"><span>快照节点</span><select class="input" v-model="snapshot.tn"><option>T+1</option><option>T+3</option><option>T+7</option></select></label>
            <label class="field"><span>播放</span><input class="input" type="number" min="0" v-model.number="snapshot.plays"></label>
            <label class="field"><span>点赞</span><input class="input" type="number" min="0" v-model.number="snapshot.likes"></label>
            <label class="field"><span>评论</span><input class="input" type="number" min="0" v-model.number="snapshot.comments"></label>
            <label class="field"><span>收藏</span><input class="input" type="number" min="0" v-model.number="snapshot.collects"></label>
            <label class="field"><span>分享</span><input class="input" type="number" min="0" v-model.number="snapshot.shares"></label>
            <label class="field"><span>新增关注</span><input class="input" type="number" min="0" v-model.number="snapshot.follows"></label>
            <label class="field"><span>完播率（可选%）</span><input class="input" type="number" min="0" max="100" v-model.number="snapshot.completion_rate"></label>
          </div>
          <div class="settings-actions" v-if="activePub"><button class="btn btn-pri" :disabled="savingSnapshot" @click="saveSnapshot">保存真实快照</button><button class="btn" @click="generateRetro">生成复盘与规则建议</button></div>
        </div>
      </div>

      <div class="grid g21 section-gap">
        <div class="card" v-if="report && report.report && report.report.actual">
          <div class="card-head"><div class="card-title">单条复盘 · 《{{ report.publication.final_title }}》 <span class="badge" :class="report.report.stage==='FINAL'?'b-ok':'b-warn'">v{{ report.report.version_no || 1 }} · {{ report.report.stage==='FINAL'?'最终复盘':'阶段复盘' }}</span></div></div>
          <div class="card-body">
            <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:12px">
              <div><div class="ana-label">发布前假设</div><div style="font-size:12px">{{ report.report.prediction ? report.report.prediction.hypothesis : '工作台外随手发布：没有发布前盲预测，仅保留实绩' }}</div></div>
              <div><div class="ana-label">播放走势（快照）</div>
                <div class="bars" style="height:64px;width:220px">
                  <div class="bar" v-for="sn in report.snapshots" :key="sn.id">
                    <i :style="{'--h': Math.max(10, Math.round(sn.plays/58000*100))+'%'}"></i><span>{{ sn.tn }} {{ (sn.plays/10000).toFixed(1) }}万</span>
                  </div>
                </div>
              </div>
            </div>
            <table class="tbl"><tr><th>预测档位（锁定）</th><th>实际档位</th><th>最新节点</th><th>播放</th><th>互动率</th></tr><tr><td>{{ report.report.prediction ? report.report.prediction.expected_bucket : '不适用' }}</td><td>{{ report.report.actual.actual_bucket }}</td><td>{{ report.report.actual.latest_tn }}</td><td>{{ report.report.actual.plays }}</td><td>{{ report.report.actual.engagement_rate }}%</td></tr></table>
            <div class="mini-note" style="margin-top:8px">{{ report.report.calibration }}。原预测不可修改；每次新增真实快照后会追加复盘版本，T+7 才形成最终复盘与规则建议。</div>
          </div>
        </div>
        <div class="card" v-else><div class="empty">{{ report && report.report ? '这是一条旧版演示复盘，需重新录入真实快照后生成新版证据链' : '选择发布记录并录入至少一个真实快照后生成复盘' }}</div></div>

        <div class="card">
          <div class="card-head"><div class="card-title">规则建议 <span class="badge b-warn">{{ suggestions.filter(s=>s.status==='pending').length }} 条待审核</span>
            <span class="card-sub">看到依据与影响范围后由你确认</span></div></div>
          <div class="card-body" style="padding-top:6px">
            <div class="pattern-card" v-for="s in suggestions" :key="s.id" style="margin-bottom:10px" :style="s.status!=='pending'?'opacity:.55':''">
              <div style="display:flex;justify-content:space-between;gap:8px">
                <b style="font-size:12px">[{{ {create:'新增',update:'调整',deprecate:'失效',observe:'观察'}[s.action] }}] {{ s.rule_text }}</b>
                <span class="badge" :class="actionBadge(s.action)">{{ s.action }}</span>
              </div>
              <div class="mini-note" style="margin:5px 0">证据：{{ s.evidence }} · 置信度 {{ s.confidence }} · 影响范围：{{ s.impact }} · 状态：{{ statusLabel(s.status) }}</div>
              <div style="display:flex;gap:6px" v-if="s.status==='pending'">
                <button class="btn btn-sm btn-pri" @click="reviewRule(s)">审核</button>
                <button class="btn btn-sm btn-ghost" @click="rejectRule(s)">拒绝</button>
                <button class="btn btn-sm btn-ghost" @click="observeRule(s)">继续观察</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">运营实验</div></div>
        <div class="card-body" style="padding-top:4px"><table class="tbl">
          <tr><th>实验</th><th>调整内容</th><th>周期</th><th>关注指标</th><th>样本</th><th>状态</th></tr>
          <tr v-for="e in experiments" :key="e.id">
            <td>{{ e.name }}</td><td>{{ e.change }}</td><td>{{ e.period }}</td><td>{{ e.metric }}</td><td>{{ e.samples }}</td>
            <td><span class="badge b-pri pulse" v-if="e.status==='running'"><span class="sdot"></span>进行中</span>
              <span class="badge b-ok" v-else>{{ e.result }}</span></td>
          </tr>
        </table></div>
      </div>
    </section>`,
  };

  /* ================= 成长记录 ================= */
  window.PageGrowth = {
    data() { return { events: [], rules: [], filter: 'all', evt: { title: '', desc: '' } }; },
    async mounted() { await this.load(); },
    methods: {
      async load() {
        try {
          [this.events, this.rules] = await Promise.all([
            api.get('/api/growth/timeline'), api.get('/api/rules'),
          ]);
        } catch (e) { handleError(e); }
      },
      async addEvent() {
        if (!this.evt.title.trim()) { toast('请填写事件标题', 'warn'); return; }
        try {
          await api.post('/api/growth/events', this.evt);
          this.evt = { title: '', desc: '' };
          toast('关键事件已记录');
          await this.load();
        } catch (e) { handleError(e); }
      },
      async exportReport() {
        try {
          const r = await fetch('/api/growth/export');
          const text = await r.text();
          const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = '成长报告.md';
          a.click();
          toast('成长报告已导出（不含密钥）');
        } catch (e) { handleError(e); }
      },
      ruleBadge(r) {
        return {
          verified: ['b-ok', '已验证'], pending: ['b-warn', '待验证'],
          pending_review: ['b-warn', '待审核'], deprecated: ['b-neu', '已失效'],
          not_applicable: ['b-neu', '不适用我'],
        }[r.status] || ['b-info', r.scope];
      },
    },
    computed: {
      filteredRules() {
        if (this.filter === 'all') return this.rules;
        return this.rules.filter((r) => r.status === this.filter);
      },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">成长记录</div><div class="page-q">我的IP和运营方法是如何一步步形成的？</div></div>
        <button class="btn" @click="exportReport">导出成长报告</button>
      </div>
      <div class="grid g21">
        <div class="card">
          <div class="card-head"><div class="card-title">时间线</div></div>
          <div class="card-body">
            <div class="tl">
              <div class="tl-item" v-for="e in events" :key="e.id" :class="e.kind">
                <div class="tl-date">{{ e.date }}</div>
                <div class="tl-title">{{ e.title }}</div>
                <div class="tl-desc">{{ e.desc }}</div>
              </div>
            </div>
            <div style="border-top:1px dashed var(--line2);padding-top:10px">
              <div class="ana-label">记录关键事件</div>
              <div class="field"><input class="input" v-model="evt.title" placeholder="事件标题（如：第一次直播带货尝试）"></div>
              <div class="field"><input class="input" v-model="evt.desc" placeholder="发生了什么、学到什么"></div>
              <button class="btn btn-sm btn-pri" @click="addEvent">记录</button>
            </div>
          </div>
        </div>
        <div class="card">
          <div class="card-head"><div class="card-title">经验库 <span class="card-sub">规则经验包 v2.0 → v2.1</span></div></div>
          <div class="card-body">
            <div class="chips" style="margin-bottom:12px">
              <button class="chip" :class="{active:filter==='all'}" @click="filter='all'">全部 {{ rules.length }}</button>
              <button class="chip" :class="{active:filter==='verified'}" @click="filter='verified'">已验证</button>
              <button class="chip" :class="{active:filter==='pending'}" @click="filter='pending'">待验证</button>
              <button class="chip" :class="{active:filter==='deprecated'}" @click="filter='deprecated'">已失效</button>
              <button class="chip" :class="{active:filter==='not_applicable'}" @click="filter='not_applicable'">不适用我</button>
            </div>
            <div class="kv-row" v-for="r in filteredRules" :key="r.id">
              <span><s v-if="r.status==='deprecated'">{{ r.text }}</s><template v-else>{{ r.text }}</template></span>
              <span class="badge" :class="ruleBadge(r)[0]">{{ r.note || ruleBadge(r)[1] }}</span>
            </div>
            <div class="mini-note" style="margin-top:8px">规则从建议到生效全程留痕：依据、影响范围、版本差异、确认人均可查。</div>
          </div>
        </div>
      </div>
    </section>`,
  };
})();
