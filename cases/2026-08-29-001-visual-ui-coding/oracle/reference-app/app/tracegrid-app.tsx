"use client";

import {useState} from "react";

type View = "overview" | "runs" | "detail";

type TracegridAppProps = {
  initialView: View;
  filterOpen: boolean;
  menuOpen: boolean;
};

const runs = [
  {id: "run-4821", model: "GLM-5.3 Flash", task: "Invoice anomaly review", latency: "742 ms", tokens: "18.4k", cost: "$0.12", status: "Completed"},
  {id: "run-4820", model: "Claude Opus 4.8", task: "Migrate checkout tests", latency: "1.28 s", tokens: "42.9k", cost: "$1.84", status: "Completed"},
  {id: "run-4819", model: "DeepSeek V4", task: "Vendor risk synthesis", latency: "968 ms", tokens: "31.2k", cost: "$0.22", status: "Failed"},
  {id: "run-4818", model: "Kimi K3", task: "Release notes draft", latency: "811 ms", tokens: "12.7k", cost: "$0.09", status: "Running"},
  {id: "run-4817", model: "GLM-5.3 Flash", task: "Support queue triage", latency: "694 ms", tokens: "9.8k", cost: "$0.06", status: "Completed"},
];

function Icon({name}: {name: "grid" | "runs" | "models" | "reports" | "settings"}) {
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></>,
    runs: <><path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18" cy="18" r="2"/></>,
    models: <><circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M19.1 4.9l-2.8 2.8M7.7 16.3l-2.8 2.8"/></>,
    reports: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></>,
    settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">{paths[name]}</svg>;
}

function Sidebar({view}: {view: View}) {
  return (
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Tracegrid overview"><span className="brand-mark">T</span><span>TRACEGRID</span></a>
      <nav aria-label="Primary navigation">
        <a className={view === "overview" ? "active" : ""} href="/"><Icon name="grid"/><span>Overview</span></a>
        <a className={view !== "overview" ? "active" : ""} href="/runs"><Icon name="runs"/><span>Runs</span><b>1,284</b></a>
        <a href="#models"><Icon name="models"/><span>Models</span></a>
        <a href="#reports"><Icon name="reports"/><span>Reports</span></a>
      </nav>
      <div className="workspace">
        <p>WORKSPACE</p>
        <div className="workspace-row"><span className="avatar square">NW</span><span><strong>Northwind AI</strong><small>Production</small></span></div>
      </div>
      <div className="sidebar-foot"><a href="#settings"><Icon name="settings"/><span>Settings</span></a><div className="health"><i></i><span>All systems nominal</span></div></div>
    </aside>
  );
}

function Header({title, onMenu}: {title: string; onMenu: () => void}) {
  return (
    <header className="topbar">
      <button className="menu-button" aria-label="Open navigation" onClick={onMenu}><span></span><span></span></button>
      <div><p>MODEL OPERATIONS / <span>LIVE</span></p><h1>{title}</h1></div>
      <div className="top-actions"><button className="range">Last 24 hours <span>⌄</span></button><button className="icon-button" aria-label="Notifications">◌<i></i></button><span className="avatar">AD</span></div>
    </header>
  );
}

function Overview() {
  return (
    <div className="page-body">
      <section className="intro"><div><h2>System pulse</h2><p>Live performance across every production model route.</p></div><div className="live"><i></i>Updated 12 sec ago</div></section>
      <section className="metrics" aria-label="Key metrics">
        <article><p>Total runs</p><strong>1,284</strong><span className="up">↗ 12.4%</span><small>vs previous period</small></article>
        <article><p>Success rate</p><strong>96.8%</strong><span className="up">↗ 1.7%</span><small>1,243 completed</small></article>
        <article><p>P50 latency</p><strong>842 <em>ms</em></strong><span className="down">↘ 8.2%</span><small>p95 · 2.41s</small></article>
        <article><p>Total spend</p><strong>$18.42</strong><span className="neutral">$0.014 / run</span><small>Budget · 36.8%</small></article>
      </section>
      <section className="dashboard-grid">
        <article className="panel throughput"><div className="panel-head"><div><p>RUN THROUGHPUT</p><h3>Requests over time</h3></div><div className="legend"><span><i className="mint"></i>Successful</span><span><i></i>Failed</span></div></div><div className="chart-wrap"><div className="axis"><span>120</span><span>80</span><span>40</span><span>0</span></div><svg viewBox="0 0 760 210" preserveAspectRatio="none" role="img" aria-label="Run throughput chart"><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#6cf4b9" stopOpacity=".23"/><stop offset="1" stopColor="#6cf4b9" stopOpacity="0"/></linearGradient></defs><path className="area" d="M0 176 C45 158 54 164 93 139 S153 116 190 131 249 90 290 103 343 132 387 86 455 58 497 78 557 104 603 62 671 39 760 44 L760 210 L0 210Z"/><path className="line" d="M0 176 C45 158 54 164 93 139 S153 116 190 131 249 90 290 103 343 132 387 86 455 58 497 78 557 104 603 62 671 39 760 44"/><path className="failed-line" d="M0 193 C80 190 108 178 172 190 S290 184 344 192 458 173 514 190 650 180 760 189"/></svg><div className="x-axis"><span>00:00</span><span>04:00</span><span>08:00</span><span>12:00</span><span>16:00</span><span>20:00</span><span>NOW</span></div></div></article>
        <article className="panel distribution"><div className="panel-head"><div><p>MODEL SHARE</p><h3>Traffic distribution</h3></div><button aria-label="More options">•••</button></div><div className="donut-row"><div className="donut"><div><strong>1.28k</strong><span>RUNS</span></div></div><div className="model-list"><div><i className="m1"></i><span>GLM-5.3 Flash</span><strong>42%</strong></div><div><i className="m2"></i><span>Claude Opus 4.8</span><strong>24%</strong></div><div><i className="m3"></i><span>DeepSeek V4</span><strong>18%</strong></div><div><i className="m4"></i><span>Kimi K3</span><strong>16%</strong></div></div></div></article>
      </section>
      <RecentRuns />
    </div>
  );
}

function StatusBadge({status}: {status: string}) {
  return <span className={`status ${status.toLowerCase()}`}><i></i>{status}</span>;
}

function RecentRuns() {
  return (
    <section className="panel runs-panel"><div className="panel-head"><div><p>RECENT ACTIVITY</p><h3>Latest runs</h3></div><a href="/runs">View all runs <span>→</span></a></div><div className="run-table"><div className="run-row table-head"><span>RUN ID</span><span>MODEL / TASK</span><span>LATENCY</span><span>TOKENS</span><span>COST</span><span>STATUS</span></div>{runs.slice(0,4).map((run) => <button className="run-row" key={run.id} aria-label={`Run ${run.id}`} onClick={() => {window.location.href = `/runs/${run.id}`;}}><code>{run.id}</code><span className="run-main"><strong>{run.model}</strong><small>{run.task}</small></span><span>{run.latency}</span><span>{run.tokens}</span><span>{run.cost}</span><StatusBadge status={run.status}/></button>)}</div></section>
  );
}

function RunsPage({initialFilterOpen}: {initialFilterOpen: boolean}) {
  const [filterOpen, setFilterOpen] = useState(initialFilterOpen);
  return (
    <div className="page-body runs-page">
      <section className="intro"><div><h2>Run ledger</h2><p>Inspect every production request, trace, and outcome.</p></div><button className="export">Export CSV <span>↗</span></button></section>
      <section className="filterbar"><div className="search"><span>⌕</span><input aria-label="Search runs" placeholder="Search run ID or task"/></div><div className="filter-wrap"><button className={filterOpen ? "filter active-filter" : "filter"} aria-label="Status filter" aria-expanded={filterOpen} onClick={() => setFilterOpen((value) => !value)}><i></i>Status <span>⌄</span></button>{filterOpen && <div className="filter-popover" role="menu"><p>FILTER BY STATUS</p>{["All runs", "Completed", "Running", "Failed"].map((item, index) => <button role="menuitem" key={item}><span className={index === 0 ? "radio selected" : "radio"}></span>{item}{item !== "All runs" && <i className={item.toLowerCase()}></i>}</button>)}</div>}</div><button className="filter"><i className="model-icon"></i>Model <span>⌄</span></button><button className="filter"><i className="calendar-icon"></i>24 hours <span>⌄</span></button><div className="results">1,284 RESULTS</div></section>
      <section className="panel ledger"><div className="run-table"><div className="run-row table-head"><span>RUN ID / TIME</span><span>MODEL / TASK</span><span>LATENCY</span><span>TOKENS</span><span>COST</span><span>STATUS</span></div>{runs.map((run, index) => <button className="run-row tall" key={run.id} aria-label={`Run ${run.id}`} onClick={() => {window.location.href = `/runs/${run.id}`;}}><span><code>{run.id}</code><small>{12 - index}:4{8 - index}:2{1 + index}</small></span><span className="run-main"><strong>{run.model}</strong><small>{run.task}</small></span><span>{run.latency}</span><span>{run.tokens}</span><span>{run.cost}</span><StatusBadge status={run.status}/></button>)}</div><div className="pagination"><span>Showing 1–5 of 1,284 runs</span><div><button disabled>←</button><button className="current">1</button><button>2</button><button>3</button><button>…</button><button>257</button><button>→</button></div></div></section>
    </div>
  );
}

function DetailPage() {
  return (
    <div className="page-body detail-page">
      <a className="back" href="/runs">← Back to all runs</a>
      <section className="detail-title"><div><div className="detail-id"><code>run-4821</code><StatusBadge status="Completed"/></div><h2>Invoice anomaly review</h2><p>Production · GLM-5.3 Flash · Aug 29, 2026 at 12:48:21</p></div><div><button className="secondary">Replay run</button><button className="icon-button">•••</button></div></section>
      <section className="detail-stats"><article><p>END-TO-END LATENCY</p><strong>742 <em>ms</em></strong><span>↘ 14% vs route median</span></article><article><p>TOTAL TOKENS</p><strong>18,442</strong><span>14,080 in · 4,362 out</span></article><article><p>ESTIMATED COST</p><strong>$0.12</strong><span>$0.0065 / 1k tokens</span></article><article><p>TOOL CALLS</p><strong>7</strong><span>7 successful · 0 failed</span></article></section>
      <section className="detail-grid"><article className="panel trace"><div className="panel-head"><div><p>EXECUTION TRACE</p><h3>Run timeline</h3></div><span className="duration">TOTAL · 742 MS</span></div><div className="timeline"><div className="timeline-row"><span>REQUEST RECEIVED</span><div className="track"><i style={{left:"0%", width:"6%"}}></i></div><strong>0 ms</strong></div><div className="timeline-row"><span>CONTEXT PREFILL</span><div className="track"><i className="blue" style={{left:"7%", width:"19%"}}></i></div><strong>142 ms</strong></div><div className="timeline-row"><span>MODEL INFERENCE</span><div className="track"><i style={{left:"27%", width:"48%"}}></i></div><strong>358 ms</strong></div><div className="timeline-row"><span>TOOL EXECUTION</span><div className="track"><i className="amber" style={{left:"52%", width:"29%"}}></i></div><strong>216 ms</strong></div><div className="timeline-row"><span>RESPONSE STREAM</span><div className="track"><i className="violet" style={{left:"82%", width:"17%"}}></i></div><strong>126 ms</strong></div></div><div className="time-axis"><span>0</span><span>200</span><span>400</span><span>600</span><span>742 ms</span></div></article><article className="panel request"><div className="panel-head"><div><p>REQUEST</p><h3>Runtime configuration</h3></div></div><dl><div><dt>MODEL</dt><dd>glm-5.3-flash</dd></div><div><dt>REASONING</dt><dd>max</dd></div><div><dt>TEMPERATURE</dt><dd>1.0</dd></div><div><dt>CONTEXT</dt><dd>14,080 tokens</dd></div><div><dt>CACHE HIT</dt><dd><span className="yes">YES</span> 63.4%</dd></div><div><dt>REGION</dt><dd>us-east-1</dd></div></dl></article></section>
      <section className="panel events"><div className="panel-head"><div><p>EVENT LOG</p><h3>Seven tool calls</h3></div><button>Collapse all</button></div><div className="event"><span className="event-num">01</span><span className="tool-symbol">⌁</span><div><strong>read_file</strong><small>invoices/august-2026.csv</small></div><span className="event-time">48 ms</span><span className="success-check">✓</span></div><div className="event"><span className="event-num">02</span><span className="tool-symbol">⌕</span><div><strong>query_database</strong><small>vendor payment history · 24 rows</small></div><span className="event-time">83 ms</span><span className="success-check">✓</span></div><div className="event"><span className="event-num">03</span><span className="tool-symbol">ƒ</span><div><strong>calculate_variance</strong><small>invoice totals · rolling 90 days</small></div><span className="event-time">21 ms</span><span className="success-check">✓</span></div></section>
    </div>
  );
}

export function TracegridApp({initialView, filterOpen, menuOpen}: TracegridAppProps) {
  const [drawerOpen, setDrawerOpen] = useState(menuOpen);
  const title = initialView === "overview" ? "Overview" : initialView === "runs" ? "Runs" : "Run detail";
  return (
    <main className="app-shell">
      <Sidebar view={initialView}/>
      <div className={drawerOpen ? "mobile-drawer open" : "mobile-drawer"} aria-hidden={!drawerOpen}><div className="drawer-top"><a className="brand" href="/"><span className="brand-mark">T</span><span>TRACEGRID</span></a><button aria-label="Close navigation" onClick={() => setDrawerOpen(false)}>×</button></div><p>NAVIGATION</p><a className="active" href="/"><Icon name="grid"/>Overview</a><a href="/runs"><Icon name="runs"/>Runs <b>1,284</b></a><a href="#models"><Icon name="models"/>Models</a><a href="#reports"><Icon name="reports"/>Reports</a><div className="drawer-workspace"><p>WORKSPACE</p><div className="workspace-row"><span className="avatar square">NW</span><span><strong>Northwind AI</strong><small>Production</small></span></div></div></div>
      {drawerOpen && <button className="backdrop" aria-label="Close navigation backdrop" onClick={() => setDrawerOpen(false)}></button>}
      <div className="main-column"><Header title={title} onMenu={() => setDrawerOpen(true)}/>{initialView === "overview" && <Overview/>}{initialView === "runs" && <RunsPage initialFilterOpen={filterOpen}/>}{initialView === "detail" && <DetailPage/>}</div>
    </main>
  );
}
