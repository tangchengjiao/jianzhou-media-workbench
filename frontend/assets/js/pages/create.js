/* 页面：创作工坊 / 内容计划 */
(function () {
  const go = (p) => { window.__app && window.__app.go(p); };

  /* ================= 创作工坊 ================= */
  window.PageCreation = {
    data() {
      return {
        projects: [], activeId: null, p: null,
        directions: [], selDir: -1, draft: '', generating: false,
        predForm: { goal: '', expected_bucket: '', confidence: 0.6, strengths: '', risks: '', hypothesis: '', user_judgement: '' },
      };
    },
    async mounted() { await this.loadProjects(); },
    methods: {
      go,
      async loadProjects() {
        try {
          this.projects = await api.get('/api/creation/projects');
          if (this.projects.length && !this.activeId) await this.openProject(this.projects[0].id);
        } catch (e) { handleError(e); }
      },
      async openProject(pid) {
        try {
          this.activeId = pid;
          this.p = await api.get('/api/creation/projects/' + pid);
          const cur = this.p.drafts[this.p.drafts.length - 1];
          this.draft = cur ? cur.content : '';
          this.directions = await api.get('/api/creation/projects/' + pid + '/directions');
          if (this.p.diff_strategy) this.selDir = this.directions.findIndex((d) => this.p.diff_strategy.includes(d.type));
          if (this.p.prediction) {
            this.predForm = { goal: this.p.prediction.goal, expected_bucket: this.p.prediction.expected_bucket,
              confidence: this.p.prediction.confidence, strengths: this.p.prediction.strengths,
              risks: this.p.prediction.risks, hypothesis: this.p.prediction.hypothesis,
              user_judgement: this.p.prediction.user_judgement || '' };
          }
        } catch (e) { handleError(e); }
      },
      async chooseDir(i) {
        this.selDir = i;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/direction', { direction: this.directions[i].type + '：' + this.directions[i].title });
          toast('已切换差异化方向 · 提纲已按新方向生成');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async genDraft() {
        this.generating = true;
        try {
          const r = await api.post('/api/creation/projects/' + this.activeId + '/draft');
          this.draft = r.content;
          toast('初稿已生成（演示模型）· 已自动执行风格与原创性检查');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
        this.generating = false;
      },
      async saveDraft() {
        try {
          const r = await api.put('/api/creation/projects/' + this.activeId + '/draft', { content: this.draft, note: '手动修改' });
          toast('已保存为 v' + r.version + '（稿件版本只增不改）');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async savePrediction() {
        try {
          await api.post('/api/creation/projects/' + this.activeId + '/prediction', this.predForm);
          toast('发布前判断已保存（未锁定）');
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      async lockPrediction() {
        try {
          await this.savePrediction();
          const r = await api.post('/api/creation/projects/' + this.activeId + '/prediction/lock');
          toast(r.note);
          await this.openProject(this.activeId);
        } catch (e) { handleError(e); }
      },
      checkBadge(c) { return c.status === 'ok' ? 'b-ok' : c.status === 'warn' ? 'b-warn' : 'b-err'; },
      async addToPlan() {
        try {
          const r = await api.post('/api/planning/tasks', { title: this.p.title, platform: '抖音口播', project_id: this.activeId, estimate_h: 2 });
          await api.patch('/api/planning/tasks/' + r.id, { status: 'SELECTED' });
          toast('已加入本周计划（候选 → 已选题）');
          go('plan');
        } catch (e) { handleError(e); }
      },
      stepState() {
        if (!this.p) return 0;
        if (this.p.prediction && this.p.prediction.locked) return 10;
        if (this.p.drafts.length) return 7;
        if (this.p.outline.length) return 5;
        if (this.p.diff_strategy) return 4;
        return 2;
      },
    },
    computed: {
      locked() { return this.p && this.p.prediction && this.p.prediction.locked === 1; },
      steps() {
        return ['选择来源', '明确目标', 'IP 匹配', '差异化方向', '提纲', '补充本人事实', '生成初稿', '风格适配', '原创检查', '锁定版本'];
      },
      curStep() { return this.stepState(); },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">创作工坊</div><div class="page-q">这件事如何用我的身份、观点和语言讲出来？</div></div>
        <div>
          <select class="input" style="width:220px;display:inline-block;margin-right:8px" :value="activeId" @change="openProject($event.target.value)">
            <option v-for="pr in projects" :key="pr.id" :value="pr.id">{{ pr.title }}（{{ pr.id }}）</option>
          </select>
          <button class="btn btn-pri" v-if="p" @click="addToPlan">加入本周计划</button>
        </div>
      </div>

      <div class="card section-gap"><div class="card-body" style="padding:12px 16px 8px">
        <div class="steps">
          <div class="step" v-for="(s,i) in steps" :key="s" :class="{done: i<curStep, cur: i===curStep}"><i>{{ i<curStep?'✓':i+1 }}</i>{{ s }}</div>
        </div>
      </div></div>

      <template v-if="p">
        <div class="ctx-bar">◈ <b>Context Resolver</b> 本次任务显式加载：{{ p.context.packages && p.context.packages.join(' · ') }} · {{ p.context.sources && p.context.sources.join(' · ') }} · 约 {{ p.context.tokens }} tokens · {{ p.context.isolation }}</div>

        <div class="card section-gap">
          <div class="card-head"><div class="card-title">差异化方向 <span class="card-sub">至少三类明显不同的迁移，拒绝同义词替换</span></div></div>
          <div class="card-body"><div class="grid g3">
            <div class="diff-card" v-for="(d,i) in directions" :key="i" :class="{sel: selDir===i}" @click="chooseDir(i)">
              <span class="badge" :class="d.cls">{{ d.type }}<template v-if="selDir===i"> ✓ 已选</template></span>
              <div style="font-weight:600;margin:7px 0 4px">{{ d.title }}</div>
              <div class="mini-note">{{ d.note }}</div>
            </div>
          </div></div>
        </div>

        <div class="grid g31 section-gap">
          <div class="card">
            <div class="card-head"><div class="card-title">稿件工作区 · 《{{ p.title }}》
              <span class="badge b-neu" v-if="p.drafts.length">草稿 v{{ p.drafts[p.drafts.length-1].version_no }}</span></div>
              <div class="src-chain"><span class="node" v-for="n in p.source_chain" :key="n" @click="go('analysis')">{{ n }}</span></div></div>
            <div class="card-body">
              <div class="grid g2" style="margin-bottom:12px">
                <div><div class="ana-label">本条目标</div><div style="font-size:12.5px">{{ p.goal }}</div></div>
                <div><div class="ana-label">IP 匹配</div><div style="font-size:12.5px">{{ p.audience }}</div></div>
              </div>
              <div class="ana-label">提纲</div>
              <div class="iv-flow" style="margin-bottom:10px"><template v-for="(o,i) in p.outline" :key="i"><span>{{ o }}</span><em v-if="i<p.outline.length-1">→</em></template></div>
              <div class="ana-label">正文（口播稿 · 平台版本：抖音）</div>
              <textarea class="draft-area" v-model="draft"></textarea>
              <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
                <button class="btn btn-pri" :disabled="generating" @click="genDraft">{{ generating ? '生成中…' : (p.drafts.length ? '重新生成初稿' : '生成初稿') }}</button>
                <button class="btn" @click="saveDraft">保存为新版本</button>
              </div>
              <div class="mini-note" style="margin-top:6px">引用与事实来源：案例来自咨询记录 #C-31（已获授权 · 已匿名化） · 结构参考拆解 #A-1024（已标注借鉴边界）</div>
            </div>
          </div>

          <div>
            <div class="card section-gap" v-if="p.checks && p.checks.style_match">
              <div class="card-head"><div class="card-title">检查</div></div>
              <div class="card-body" style="padding-top:4px">
                <template v-for="(c,k) in p.checks" :key="k">
                  <div class="kv-row" v-if="c && c.label">
                    <span>{{ c.label }}</span><span class="badge" :class="checkBadge(c)">{{ c.value }}</span>
                  </div>
                </template>
                <div class="mini-note" style="margin-top:8px">{{ p.checks.note }}</div>
              </div>
            </div>
            <div class="card section-gap">
              <div class="card-head"><div class="card-title">修改记录</div></div>
              <div class="card-body" style="padding-top:4px">
                <div class="kv-row" v-for="d in [...p.drafts].reverse()" :key="d.id">
                  <span class="mono">v{{ d.version_no }}</span><span>{{ d.note }}</span>
                </div>
                <div class="empty" v-if="!p.drafts.length">尚无稿件版本</div>
              </div>
            </div>
            <div class="lock-panel" :class="{locked}">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:9px">
                <b style="font-size:13px">发布前判断</b>
                <span class="badge" :class="locked?'b-neu':'b-warn'">{{ locked ? '🔒 已锁定 · ' + (p.prediction.locked_at||'').slice(0,16).replace('T',' ') : '未锁定' }}</span>
              </div>
              <template v-if="!locked">
                <div class="field"><label>本条内容目标</label><input class="input" v-model="predForm.goal"></div>
                <div class="field"><label>预计相对表现</label><input class="input" v-model="predForm.expected_bucket" placeholder="如：高于均值 1.2–1.8×"></div>
                <div class="field"><label>主要优势</label><input class="input" v-model="predForm.strengths"></div>
                <div class="field"><label>主要风险</label><input class="input" v-model="predForm.risks"></div>
                <div class="field"><label>本次准备验证的假设</label><input class="input" v-model="predForm.hypothesis"></div>
                <div class="field"><label>系统置信度</label><input class="input mono" type="number" step="0.01" min="0" max="1" v-model="predForm.confidence"></div>
                <div class="field"><label>我的判断（发布后不可修改）</label><input class="input" v-model="predForm.user_judgement"></div>
                <button class="btn btn-pri" style="width:100%" @click="lockPrediction">🔒 锁定判断 · 锁定后仅可追加复盘</button>
              </template>
              <template v-else>
                <div class="kv-row"><span>预计相对表现</span><b>{{ p.prediction.expected_bucket }}</b></div>
                <div class="kv-row"><span>主要优势</span><span>{{ p.prediction.strengths }}</span></div>
                <div class="kv-row"><span>主要风险</span><span>{{ p.prediction.risks }}</span></div>
                <div class="kv-row"><span>验证假设</span><span>{{ p.prediction.hypothesis }}</span></div>
                <div class="kv-row"><span>系统置信度</span><b class="mono">{{ p.prediction.confidence }}</b></div>
                <div class="kv-row"><span>我的判断</span><span>{{ p.prediction.user_judgement }}</span></div>
                <div class="mini-note" style="text-align:center;margin-top:8px">预测记录只读 · 不可修改 · 不可删除 · 发布后仅可追加复盘</div>
              </template>
            </div>
          </div>
        </div>
      </template>
      <div class="card" v-else><div class="loading">加载中</div></div>
    </section>`,
  };

  /* ================= 内容计划 ================= */
  window.PagePlan = {
    data() { return { columns: [], done: [], week: null, dragId: null, showDone: false }; },
    async mounted() { await this.load(); bus.on('board-changed', this.load); },
    methods: {
      go,
      async load() {
        try {
          const [b, w] = await Promise.all([api.get('/api/planning/board'), api.get('/api/planning/week')]);
          this.columns = b.columns; this.done = b.done; this.week = w;
        } catch (e) { handleError(e); }
      },
      onDragStart(card) { this.dragId = card.id; },
      onDrop(col) {
        if (!this.dragId) return;
        const card = this.columns.flatMap((c) => c.cards).find((c) => c.id === this.dragId);
        this.dragId = null;
        if (!card || card.status === col.state) return;
        this.moveTask(card, col.state);
      },
      async moveTask(card, status) {
        try {
          await api.patch('/api/planning/tasks/' + card.id, { status });
          toast('状态已更新（明确事件驱动）');
          await this.load();
        } catch (e) {
          handleError(e);
          await this.load();
        }
      },
      async publish(card) { bus.emit('open-publish', card); },
    },
    template: `
    <section>
      <div class="page-head">
        <div><div class="page-title">内容计划</div><div class="page-q">这周具体做哪些内容，什么时候做？</div></div>
        <button class="btn" @click="load();toast('建议由规则引擎计算（非 LLM）：产能 - 已排时长 = 建议数量')">重新计算建议</button>
      </div>

      <div class="card section-gap" v-if="week"><div class="card-body">
        <div style="display:flex;gap:26px;flex-wrap:wrap;align-items:center">
          <div style="flex:1;min-width:240px">
            <div style="font-size:11px;color:var(--ink3);margin-bottom:4px">本周产能 · {{ week.hours_total }}h 可投入</div>
            <div style="display:flex;align-items:center;gap:10px">
              <div class="progress" style="flex:1"><i :style="{width:(week.hours_scheduled/week.hours_total*100)+'%'}"></i></div>
              <b>已排 {{ week.hours_scheduled }}h · 剩余 {{ week.hours_left }}h</b></div>
          </div>
          <div style="font-size:12px;color:var(--ink2)">建议发布 <b style="color:var(--ink)">{{ week.suggested_count }} 条</b>（近 8 周完成率 87%） · 已安排 <b style="color:var(--ink)">{{ week.scheduled_count }} 条</b> · 比例：{{ week.ratio }}</div>
        </div>
      </div></div>

      <div class="callout c-warn" v-if="week && week.backlog > 3"><span>⚠</span><span><b>库存积压 {{ week.backlog }} 条：</b>建议优先完成已有内容再开新选题。若热点插队，将提示会挤占什么任务——确认后才会调整，系统不会静默改排期。</span></div>

      <div class="card section-gap">
        <div class="card-head"><div class="card-title">内容库存看板 <span class="card-sub">拖动卡片调整状态 · 非法转换会被状态机拒绝</span></div>
          <button class="btn btn-sm btn-ghost" @click="showDone=!showDone">{{ showDone?'隐藏':'已完成/归档' }}（{{ done.length }}）</button></div>
        <div class="card-body"><div class="board">
          <div class="bcol" v-for="col in columns" :key="col.state"
               @dragover.prevent="$event.currentTarget.classList.add('dragover')"
               @dragleave="$event.currentTarget.classList.remove('dragover')"
               @drop.prevent="onDrop(col);$event.currentTarget.classList.remove('dragover')">
            <div class="bcol-h">{{ col.label }} <span>{{ col.count }}</span></div>
            <div class="kcard" v-for="card in col.cards" :key="card.id" draggable="true" @dragstart="onDragStart(card)">
              <div class="kt">{{ card.title }}</div>
              <div class="km">
                <span class="badge" v-for="bd in card.badges" :key="bd.t" :class="bd.c">{{ bd.t }}</span>
                <span v-if="card.platform">{{ card.platform }}</span>
                <span v-if="card.estimate_h">预计 {{ card.estimate_h }}h</span>
                <button class="btn btn-sm btn-pri" v-if="card.status==='READY_TO_PUBLISH'" @click="publish(card)">发布登记</button>
                <button class="btn btn-sm" v-if="card.status==='WAITING_RETRO'" @click="go('retro')">去复盘</button>
              </div>
            </div>
            <div class="empty" v-if="!col.count" style="padding:12px">—</div>
          </div>
        </div>
        <div v-if="showDone" style="margin-top:10px">
          <div class="ana-label">已完成 / 已归档</div>
          <div class="chips"><span class="chip" v-for="d in done" :key="d.id">{{ d.title }}</span>
            <span class="mini-note" v-if="!done.length">（暂无）</span></div>
        </div>
        </div>
      </div>

      <div class="card" v-if="week">
        <div class="card-head"><div class="card-title">本周日历 <span class="card-sub">发布登记为手动：填写链接后创建 T+N 快照计划</span></div></div>
        <div class="card-body"><div class="cal">
          <div class="cal-day" v-for="d in week.days" :key="d.date" :class="{today: d.today}">
            <h6>{{ d.name }} {{ d.date.slice(5) }}{{ d.today ? ' · 今天' : '' }}</h6>
            <div class="cal-ev" v-for="(ev,i) in d.events" :key="i" :class="'c-'+ev.c">{{ ev.t }}</div>
          </div>
        </div></div>
      </div>
    </section>`,
  };
})();
