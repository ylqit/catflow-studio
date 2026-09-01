<template>
  <main class="page settings-page">
    <section class="page-heading"><div><p class="eyebrow">Local runtime</p><h1>模型与运行设置</h1><p class="subtitle">密钥、数据库和媒体路径只存在于后端环境，浏览器无法读取。</p></div></section>
    <div class="settings-grid">
      <section class="card settings-card"><p class="eyebrow">Connection</p><h2>本机 Web 服务</h2><dl><div><dt>正式地址</dt><dd>http://127.0.0.1:8765</dd></div><div><dt>访问边界</dt><dd>仅 loopback，同源 + CSRF</dd></div><div><dt>业务状态源</dt><dd>PostgreSQL · catflow_studio</dd></div><div><dt>任务事件</dt><dd>Durable SSE</dd></div></dl></section>
      <section class="card settings-card"><p class="eyebrow">Provider</p><h2>当前安全模式</h2><dl><div><dt>Provider</dt><dd><span class="pill good">fake</span></dd></div><div><dt>规划模型</dt><dd>catflow-fake-planner-v1</dd></div><div><dt>视频模型</dt><dd>catflow-fake-video-v1</dd></div><div><dt>真实付费调用</dt><dd><span class="pill">关闭</span></dd></div></dl><p class="notice">真实 Ark 冒烟必须在 fake 全链路通过后由用户明确开启。</p></section>
      <section class="card settings-card wide"><p class="eyebrow">Persistence</p><h2>持久化与恢复</h2><div class="persistence-flow"><span>浏览器 UI</span><i>只读/命令</i><span>FastAPI</span><i>事务</i><span>PostgreSQL</span><i>领取</i><span>Worker</span><i>媒体</i><span>var/media</span></div><p>关闭浏览器不会停止任务；API 与 Worker 重启后通过 Provider task ID 恢复，不会隐式二次提交。</p></section>
    </div>
  </main>
</template>

<style scoped>
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; }
.settings-card { padding: 26px; }
.settings-card.wide { grid-column: span 2; }
.settings-card dl { display: grid; gap: 0; margin: 20px 0; }
.settings-card dl > div { display: grid; grid-template-columns: 150px 1fr; padding: 12px 0; border-bottom: 1px solid var(--line); font-size: 12px; }
.settings-card dt { color: var(--muted); }
.settings-card dd { margin: 0; font-weight: 650; }
.persistence-flow { display: flex; align-items: center; gap: 9px; margin: 25px 0 16px; }
.persistence-flow span { padding: 13px 15px; border-radius: 11px; background: #eee6dc; font-weight: 700; font-size: 11px; }
.persistence-flow i { color: var(--muted); font-size: 9px; font-style: normal; }
.wide > p:last-child { color: var(--muted); font-size: 12px; }
</style>
