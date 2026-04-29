import { useState, useEffect, useCallback, useRef, Fragment } from "react";
import { auth } from "./firebase";
import { signInWithEmailAndPassword, signOut, onAuthStateChanged } from "firebase/auth";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from "recharts";

// ── API ───────────────────────────────────────────────────────────────────────
const API_BASE = window.location.hostname === "localhost" ? "http://localhost:8000" : "";
async function apiFetch(path, options = {}) {
  const token = auth.currentUser ? await auth.currentUser.getIdToken() : null;
  const res = await fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) }, ...options });
  if (!res.ok) { const e = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(e.detail || `API error ${res.status}`); }
  return res.json();
}

// ── Tokens ────────────────────────────────────────────────────────────────────
const DARK  = { bg: "#0a0e1a", surface: "rgba(255,255,255,0.03)", border: "rgba(255,255,255,0.08)", text: "#e2e8f0", muted: "#64748b", blue: "#60a5fa", green: "#22c55e", amber: "#f59e0b", red: "#ef4444", purple: "#a855f7", teal: "#2dd4bf" };
const LIGHT = { bg: "#f8f9fc", surface: "#ffffff", border: "rgba(0,0,0,0.1)", text: "#1a1f2e", muted: "#64748b", blue: "#2563eb", green: "#16a34a", amber: "#d97706", red: "#dc2626", purple: "#7c3aed", teal: "#0d9488" };
const C = { ...LIGHT };
applyTheme("light");
function applyTheme(t) { const src = t === "light" ? LIGHT : DARK; Object.keys(src).forEach(k => C[k] = src[k]); }
const COLORS = ["#60a5fa","#22c55e","#f59e0b","#ef4444","#a855f7","#2dd4bf","#fb923c","#f472b6"];

// ── Utils ─────────────────────────────────────────────────────────────────────
const fmt = {
  pct: n => `${(+n||0).toFixed(1)}%`,
  cur: n => `$${(+n||0).toLocaleString("en-US",{maximumFractionDigits:0})}`,
  num: n => (+n||0).toLocaleString(),
  month: s => s ? new Date(s+"-01").toLocaleDateString("en-GB",{month:"short",year:"2-digit"}) : s,
  label: t => ({weekly_supplier_overview:"Weekly Overview",monthly_supplier_overview:"Monthly Overview",monthly_supplier_account:"Supplier Account",adhoc_business:"Ad-hoc Business",adhoc_supplier:"Ad-hoc Supplier",nl_query:"NL Query"}[t]||t),
};
function confColor(c) { return c>=0.85?C.green:c>=0.75?C.amber:C.red; }

// ── Shared UI ─────────────────────────────────────────────────────────────────
function Card({children,style={}}) { return <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"16px 20px",...style}}>{children}</div>; }
function SLabel({children}) { return <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:10,marginTop:20}}>{children}</div>; }
function Chip({label,active,onClick}) { return <button onClick={onClick} style={{background:active?"rgba(96,165,250,0.2)":C.surface,border:`1px solid ${active?C.blue:C.border}`,color:active?C.blue:C.muted,borderRadius:20,padding:"3px 12px",fontSize:11,cursor:"pointer",transition:"all 0.15s"}}>{label}</button>; }

function Badge({children,variant="default"}) {
  const s={default:{bg:C.surface,fg:C.muted},business:{bg:"rgba(59,130,246,0.15)",fg:C.blue},supplier:{bg:"rgba(20,184,166,0.15)",fg:C.teal},approved:{bg:"rgba(34,197,94,0.15)",fg:C.green},rejected:{bg:"rgba(239,68,68,0.15)",fg:C.red},pending:{bg:"rgba(245,158,11,0.15)",fg:C.amber},pass:{bg:"rgba(34,197,94,0.12)",fg:C.green},fail:{bg:"rgba(239,68,68,0.12)",fg:C.red}};
  const {bg,fg}=s[variant]||s.default;
  return <span style={{background:bg,color:fg,padding:"2px 8px",borderRadius:4,fontSize:11,fontWeight:600,letterSpacing:"0.04em",textTransform:"uppercase",whiteSpace:"nowrap"}}>{children}</span>;
}

function Scorecard({label,value,sub,color,warn}) {
  return (
    <Card style={{minWidth:0}}>
      <div style={{fontSize:10,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:6}}>{label}</div>
      <div style={{fontSize:22,fontWeight:700,color:color||C.text,fontFamily:"monospace",lineHeight:1}}>{value}</div>
      {sub&&<div style={{fontSize:11,color:warn?C.amber:C.muted,marginTop:5}}>{sub}</div>}
    </Card>
  );
}

function ConfMeter({value}) {
  const c=confColor(value);
  return <div style={{display:"flex",alignItems:"center",gap:8}}><div style={{width:80,height:4,background:C.border,borderRadius:2,overflow:"hidden"}}><div style={{width:`${(value||0)*100}%`,height:"100%",background:c,borderRadius:2}}/></div><span style={{fontSize:12,color:c,fontWeight:700,fontFamily:"monospace"}}>{((value||0)*100).toFixed(0)}%</span></div>;
}

function Spinner() { return <div style={{display:"flex",justifyContent:"center",padding:40}}><div style={{width:24,height:24,border:`2px solid ${C.border}`,borderTop:`2px solid ${C.blue}`,borderRadius:"50%",animation:"spin 0.8s linear infinite"}}/><style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style></div>; }
function ErrMsg({message,onRetry}) { return <div style={{padding:"12px 16px",background:"rgba(239,68,68,0.1)",border:"1px solid rgba(239,68,68,0.2)",borderRadius:8,display:"flex",gap:12,alignItems:"center",marginBottom:16}}><span style={{color:C.red}}>✗</span><span style={{fontSize:13,color:C.red,flex:1}}>{message}</span>{onRetry&&<button onClick={onRetry} style={{background:"none",border:"1px solid rgba(239,68,68,0.3)",color:C.red,borderRadius:6,padding:"4px 12px",cursor:"pointer",fontSize:12}}>Retry</button>}</div>; }

function getTT() { return {contentStyle:{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,fontSize:12,color:C.text}}; }

// ── Date range control ────────────────────────────────────────────────────────
function DateRangeControl({dateFrom,dateTo,onChange}) {
  const presets = [
    {label:"Last 30d", from:null, to:null, key:"30d"},
    {label:"Oct–Mar (default)", from:null, to:null, key:"default"},
    {label:"Last 3m",  from:null, to:null, key:"3m"},
    {label:"Last 12m", from:null, to:null, key:"12m"},
    {label:"Custom",   from:null, to:null, key:"custom"},
  ];

  const getActive = () => {
    if (!dateFrom && !dateTo) return "default";
    return "custom";
  };

  const applyPreset = (key) => {
    const now = new Date();
    if (key === "default" || key === "30d") { onChange(null, null); return; }
    if (key === "3m")  { const f = new Date(now); f.setMonth(f.getMonth()-3); onChange(f.toISOString().slice(0,10), now.toISOString().slice(0,10)); return; }
    if (key === "12m") { const f = new Date(now); f.setFullYear(f.getFullYear()-1); onChange(f.toISOString().slice(0,10), now.toISOString().slice(0,10)); return; }
  };

  return (
    <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
      {presets.filter(p=>p.key!=="custom").map(p=>(
        <Chip key={p.key} label={p.label} active={getActive()===p.key} onClick={()=>applyPreset(p.key)} />
      ))}
      <input type="date" value={dateFrom||""} onChange={e=>onChange(e.target.value||null,dateTo)} style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"3px 8px",fontSize:11}} />
      <span style={{color:C.muted,fontSize:11}}>to</span>
      <input type="date" value={dateTo||""} onChange={e=>onChange(dateFrom,e.target.value||null)} style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"3px 8px",fontSize:11}} />
    </div>
  );
}

// ── Insights Banner ───────────────────────────────────────────────────────────
function InsightsBanner() {
  const [data,setData]         = useState(null);
  const [expanded,setExpanded] = useState(false);
  const [history,setHistory]   = useState(null);
  const [loadingHistory,setLoadingHistory] = useState(false);

  useEffect(()=>{
    apiFetch("/api/insights/current").then(d=>setData(d)).catch(()=>{});
  },[]);

  const loadHistory = async () => {
    if (history) { setExpanded(e=>!e); return; }
    setLoadingHistory(true);
    try {
      const d = await apiFetch("/api/insights/history");
      setHistory(d.weeks || []);
      setExpanded(true);
    } catch(e) {}
    finally { setLoadingHistory(false); }
  };

  if (!data?.has_insights) return null;

  const { digest, alerts } = data;
  const critical = alerts.filter(a=>a.severity==="critical").length;
  const warning  = alerts.filter(a=>a.severity==="warning").length;
  const watch    = alerts.filter(a=>a.severity==="watch").length;

  const severityColor  = critical > 0 ? C.red  : warning > 0 ? C.amber : C.blue;
  const severityBg     = critical > 0 ? "rgba(239,68,68,0.08)"  : warning > 0 ? "rgba(245,158,11,0.08)"  : "rgba(96,165,250,0.08)";
  const severityBorder = critical > 0 ? "rgba(239,68,68,0.2)"   : warning > 0 ? "rgba(245,158,11,0.2)"   : "rgba(96,165,250,0.2)";

  const weekLabel = digest.weekOf ? `Week of ${new Date(digest.weekOf+"T00:00:00").toLocaleDateString("en-GB",{day:"2-digit",month:"short"})}` : "";

  return (
    <div style={{marginBottom:20}}>
      <div style={{background:severityBg,border:`1px solid ${severityBorder}`,borderRadius:10,padding:"14px 18px"}}>
        <div style={{display:"flex",alignItems:"flex-start",gap:14}}>
          <div style={{flexShrink:0,marginTop:4}}>
            <div style={{width:8,height:8,borderRadius:"50%",background:severityColor,boxShadow:`0 0 6px ${severityColor}`}}/>
          </div>
          <div style={{flex:1,minWidth:0}}>
            <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:8,flexWrap:"wrap"}}>
              <span style={{fontSize:12,fontWeight:700,color:severityColor,textTransform:"uppercase",letterSpacing:"0.06em"}}>Weekly Insights</span>
              <span style={{fontSize:11,color:C.muted}}>{weekLabel}</span>
              <div style={{display:"flex",gap:6,marginLeft:"auto"}}>
                {critical>0 && <span style={{background:"rgba(239,68,68,0.15)",color:C.red,padding:"1px 8px",borderRadius:4,fontSize:11,fontWeight:700}}>{critical} critical</span>}
                {warning>0  && <span style={{background:"rgba(245,158,11,0.15)",color:C.amber,padding:"1px 8px",borderRadius:4,fontSize:11,fontWeight:700}}>{warning} warning</span>}
                {watch>0    && <span style={{background:"rgba(96,165,250,0.15)",color:C.blue,padding:"1px 8px",borderRadius:4,fontSize:11,fontWeight:700}}>{watch} watch</span>}
              </div>
            </div>
            <p style={{fontSize:13,color:C.text,lineHeight:1.7,margin:"0 0 10px",fontStyle:"italic"}}>
              {digest.narrative}
            </p>
            <button onClick={loadHistory}
              style={{background:"none",border:`1px solid ${severityBorder}`,color:severityColor,borderRadius:6,padding:"4px 12px",fontSize:11,cursor:"pointer",fontWeight:600}}>
              {loadingHistory ? "Loading..." : expanded ? "Hide detail ↑" : `View all ${alerts.length} alerts & history ↓`}
            </button>
          </div>
        </div>

        {expanded && history && (
          <div style={{marginTop:16,borderTop:`1px solid ${severityBorder}`,paddingTop:16}}>
            {history.map((week,wi)=>(
              <div key={wi} style={{marginBottom:wi<history.length-1?20:0}}>
                <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:8,display:"flex",alignItems:"center",gap:10}}>
                  <span>Week of {new Date(week.weekOf+"T00:00:00").toLocaleDateString("en-GB",{day:"2-digit",month:"short",year:"numeric"})}</span>
                  <span style={{color:C.border}}>·</span>
                  <span>{week.alerts.length} alerts</span>
                  {week.critical>0 && <span style={{color:C.red}}>{week.critical} critical</span>}
                  {week.warning>0  && <span style={{color:C.amber}}>{week.warning} warning</span>}
                </div>
                <div style={{display:"flex",flexDirection:"column",gap:4}}>
                  {week.alerts.map((a,ai)=>{
                    const sc = a.severity==="critical"?C.red:a.severity==="warning"?C.amber:C.blue;
                    return (
                      <div key={ai} style={{display:"flex",alignItems:"center",gap:10,padding:"6px 10px",background:C.surface,borderRadius:6,border:"1px solid rgba(255,255,255,0.04)"}}>
                        <span style={{fontSize:9,color:sc,flexShrink:0}}>●</span>
                        <span style={{fontSize:12,color:C.muted,flex:1}}>{a.description}</span>
                        <span style={{fontSize:11,color:sc,fontFamily:"monospace",whiteSpace:"nowrap",fontWeight:600}}>
                          {a.changePercent!=null?`${a.changePercent>0?"+":""}${a.changePercent.toFixed(1)}%`:""}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Business Overview Dashboard ───────────────────────────────────────────────
function BusinessDashboard() {
  const [data,setData]       = useState(null);
  const [loading,setLoading] = useState(true);
  const [error,setError]     = useState(null);
  const [dateFrom,setDateFrom] = useState(null);
  const [dateTo,setDateTo]   = useState(null);
  const [filterSupplier,setFilterSupplier] = useState(null);
  const [filterCategory,setFilterCategory] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const q = new URLSearchParams();
      if (dateFrom) q.set("date_from", dateFrom);
      if (dateTo)   q.set("date_to",   dateTo);
      setData(await apiFetch(`/api/dashboard/business?${q}`));
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  }, [dateFrom, dateTo]);

  useEffect(()=>{ load(); },[load]);

  const clearFilters = () => { setFilterSupplier(null); setFilterCategory(null); };

  const filteredTrend = data?.trend || [];
  const filteredSuppliers = (data?.by_supplier||[]).filter(r => (!filterCategory));
  const filteredCategories = (data?.by_category||[]).filter(r =>
    !filterSupplier || (data?.by_supplier||[]).find(s=>s.supplierID===filterSupplier)
  );
  const filteredResMix = data?.resolution_mix || [];
  const filteredResTrend = data?.res_trend || [];

  const hasFilter = filterSupplier || filterCategory;
  const s = data?.scorecards || {};

  if (loading) return <Spinner />;

  return (
    <div>
      <InsightsBanner />

      <div style={{display:"flex",alignItems:"flex-start",justifyContent:"space-between",marginBottom:20,gap:16,flexWrap:"wrap"}}>
        <div>
          <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Business Overview</h2>
          <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>Portfolio-level supplier performance</p>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:8,alignItems:"flex-end"}}>
          <DateRangeControl dateFrom={dateFrom} dateTo={dateTo} onChange={(f,t)=>{setDateFrom(f);setDateTo(t);}} />
          {hasFilter && (
            <div style={{display:"flex",alignItems:"center",gap:8}}>
              <span style={{fontSize:12,color:C.amber}}>
                Filtered by: {[filterSupplier,filterCategory].filter(Boolean).join(" · ")}
              </span>
              <button onClick={clearFilters} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:4,padding:"2px 8px",fontSize:11,cursor:"pointer"}}>Clear ✕</button>
            </div>
          )}
        </div>
      </div>

      {error && <ErrMsg message={error} onRetry={load} />}

      <div style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:10,marginBottom:20}}>
        <Scorecard label="Total Orders"     value={fmt.num(s.total_orders)} />
        <Scorecard label="Gross Revenue"    value={fmt.cur(s.total_gross_revenue)} />
        <Scorecard label="Net Revenue"      value={fmt.cur(s.total_net_revenue)} />
        <Scorecard label="Incident Rate"    value={fmt.pct(s.incident_rate_pct)}  color={s.incident_rate_pct>15?C.red:s.incident_rate_pct>12?C.amber:C.green} />
        <Scorecard label="Return Rate"      value={fmt.pct(s.return_rate_pct)}    color={s.return_rate_pct>7?C.red:s.return_rate_pct>5?C.amber:C.green} />
        <Scorecard label="Resolution Cost"  value={fmt.cur(s.total_resolution_cost)} sub={`${((s.total_resolution_cost/(s.total_gross_revenue||1))*100).toFixed(1)}% of revenue`} />
        <Scorecard label="Returned Revenue" value={fmt.cur(s.returned_revenue)}   sub="Gross rev of returned orders" warn />
      </div>

      <SLabel>Incident &amp; Return Rate Trend</SLabel>
      <Card style={{marginBottom:16}}>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={filteredTrend} margin={{top:5,right:20,bottom:5,left:0}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.surface} />
            <XAxis dataKey="month" stroke={C.muted} tick={{fontSize:11}} tickFormatter={fmt.month} />
            <YAxis stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
            <Tooltip {...getTT()} formatter={(v,n)=>[`${(+v).toFixed(1)}%`,n]} labelFormatter={fmt.month} />
            <Legend wrapperStyle={{fontSize:12}} />
            <Line type="monotone" dataKey="incident_rate_pct" name="Incident Rate" stroke={C.red}  strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="return_rate_pct"   name="Return Rate"   stroke={C.amber} strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <SLabel>Resolution Cost as % of Gross Revenue</SLabel>
      <Card style={{marginBottom:20}}>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={filteredResTrend} margin={{top:5,right:20,bottom:5,left:0}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.surface} />
            <XAxis dataKey="month" stroke={C.muted} tick={{fontSize:11}} tickFormatter={fmt.month} />
            <YAxis stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
            <Tooltip {...getTT()} formatter={(v,n)=>[`${(+v).toFixed(2)}%`,n]} labelFormatter={fmt.month} />
            <Line type="monotone" dataKey="resolution_cost_pct" name="Res. Cost %" stroke={C.purple} strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
        <div>
          <SLabel>Top 10 Suppliers by Incident Rate {filterSupplier && <span style={{color:C.blue,fontSize:10,marginLeft:6}}>● filtered</span>}</SLabel>
          <Card>
            <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter all charts</div>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={filteredSuppliers} layout="vertical" margin={{left:55,right:30}} onClick={e=>e?.activePayload&&setFilterSupplier(prev=>prev===e.activePayload[0]?.payload?.supplierID?null:e.activePayload[0]?.payload?.supplierID)}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.surface} horizontal={false} />
                <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                <YAxis type="category" dataKey="supplierID" stroke={C.muted} tick={{fontSize:11}} width={50} />
                <Tooltip {...getTT()} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
                <Bar dataKey="incident_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                  {filteredSuppliers.map((r,i)=><Cell key={i} fill={r.supplierID===filterSupplier?C.amber:C.blue} opacity={filterSupplier&&r.supplierID!==filterSupplier?0.35:1} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>

        <div>
          <SLabel>Incident Rate by Category {filterCategory && <span style={{color:C.blue,fontSize:10,marginLeft:6}}>● filtered</span>}</SLabel>
          <Card>
            <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter all charts</div>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={filteredCategories} layout="vertical" margin={{left:100,right:30}} onClick={e=>e?.activePayload&&setFilterCategory(prev=>prev===e.activePayload[0]?.payload?.productCategory?null:e.activePayload[0]?.payload?.productCategory)}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.surface} horizontal={false} />
                <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                <Tooltip {...getTT()} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
                <Bar dataKey="incident_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                  {filteredCategories.map((r,i)=><Cell key={i} fill={C.blue} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </div>

      <SLabel>Resolution Method Mix</SLabel>
      <Card>
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie data={filteredResMix} dataKey="total_incidents" nameKey="incidentResolution" cx="50%" cy="50%" outerRadius={75} label={({name,percent})=>`${(name||"").replace(/_/g," ")} ${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={11}>
              {filteredResMix.map((_,i)=><Cell key={i} fill={C.blue} fillOpacity={[1,0.7,0.5,0.35,0.22,0.14][i%6]} />)}
            </Pie>
            <Tooltip {...getTT()} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
          </PieChart>
        </ResponsiveContainer>
      </Card>
    </div>
  );
}

// ── Supplier Account Dashboard ────────────────────────────────────────────────

function SupplierReports({supplierID}) {
  const [reports,setReports] = useState([]);
  const [loading,setLoading] = useState(true);
  const [expanded,setExpanded] = useState(null);

  useEffect(()=>{
    if(!supplierID) return;
    setLoading(true);
    apiFetch(`/api/reports/supplier/${supplierID}`)
      .then(d=>setReports(d.reports||[]))
      .catch(()=>setReports([]))
      .finally(()=>setLoading(false));
  },[supplierID]);

  const downloadPDF = (report) => {
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8"/>
<title>${report.reportType} - ${report.reportDate}</title>
<style>
  body{font-family:'Georgia',serif;max-width:900px;margin:40px auto;padding:0 40px;color:#111;line-height:1.7}
  h1,h2,h3{font-family:'Arial',sans-serif;color:#1e293b}
  h2{font-size:16px;margin-top:28px}
  table{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}
  th{background:#f1f5f9;padding:8px 12px;text-align:left;font-size:11px;text-transform:uppercase}
  td{padding:8px 12px;border-bottom:1px solid #e2e8f0}
  .meta{font-size:12px;color:#64748b;margin-bottom:24px;font-family:Arial,sans-serif}
  pre{white-space:pre-wrap;font-family:inherit}
  @media print{body{margin:20px}}
</style></head><body>
<div class="meta">Supplier: ${supplierID} &nbsp;·&nbsp; Date: ${report.reportDate} &nbsp;·&nbsp; Confidence: ${Math.round((report.confidence||0)*100)}% &nbsp;·&nbsp; Approved: ${report.approvedAt?report.approvedAt.slice(0,10):'N/A'}</div>
<pre>${report.reportNarrative||'No narrative available.'}</pre>
</body></html>`;
    const w = window.open('','_blank');
    if(!w) return;
    w.document.write(html);
    w.document.close();
    w.focus();
    setTimeout(()=>{ w.print(); },500);
  };

  if(loading) return <div style={{padding:40,textAlign:"center",color:C.muted}}>Loading reports...</div>;
  if(!reports.length) return (
    <div style={{padding:40,textAlign:"center",color:C.muted}}>
      <div style={{fontSize:32,marginBottom:12}}>{"📋"}</div>
      <div style={{fontSize:13}}>No approved reports yet.</div>
      <div style={{fontSize:12,marginTop:6}}>Reports shared by your account manager will appear here.</div>
    </div>
  );

  return (
    <div style={{display:"flex",flexDirection:"column",gap:12,padding:"20px 0"}}>
      {reports.map(r=>{
        const isOpen = expanded===r.reportID;
        const conf = Math.round((r.confidence||0)*100);
        const confCol = conf>=85?C.green:conf>=70?C.amber:C.red;
        const title = r.reportType==="monthly_supplier_account"?"Monthly Account Report":
                      r.reportType==="adhoc_supplier"?"Ad-Hoc Report":
                      r.reportType.replace(/_/g," ").replace(/\w/g,c=>c.toUpperCase());
        return (
          <div key={r.reportID} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,overflow:"hidden"}}>
            <div onClick={()=>setExpanded(isOpen?null:r.reportID)}
              style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"14px 20px",cursor:"pointer"}}>
              <div>
                <div style={{fontSize:13,fontWeight:600,color:C.text}}>{title}</div>
                <div style={{fontSize:12,color:C.muted,marginTop:3}}>
                  {r.reportDate} &nbsp;·&nbsp; Approved {r.approvedAt?r.approvedAt.slice(0,10):"—"}
                  {r.approvedBy?` · by ${r.approvedBy}`:""}
                </div>
              </div>
              <div style={{display:"flex",alignItems:"center",gap:10}}>
                <span style={{fontSize:12,color:confCol,fontWeight:600}}>{conf}%</span>
                <button onClick={e=>{e.stopPropagation();downloadPDF(r);}}
                  style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,
                    padding:"4px 12px",color:C.muted,fontSize:11,cursor:"pointer"}}>
                  Download PDF
                </button>
                <span style={{color:C.muted}}>{isOpen?"▲":"▼"}</span>
              </div>
            </div>
            {isOpen&&(
              <div style={{borderTop:`1px solid ${C.border}`,padding:"20px 24px",
                fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",
                fontFamily:"Georgia,serif",maxHeight:600,overflowY:"auto"}}>
                {r.reportNarrative||"No narrative."}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SupplierDashboard({initialSupplier, supplierFacing=false}) {
  const [suppliers,setSuppliers]     = useState([]);
  const [selectedID,setSelectedID]   = useState(initialSupplier||"");
  const [data,setData]               = useState(null);
  const [loading,setLoading]         = useState(false);
  const [error,setError]             = useState(null);
  const [dateFrom,setDateFrom]       = useState(null);
  const [dateTo,setDateTo]           = useState(null);
  const [filterCategory,setFilterCategory] = useState(null);
  const [filterIncType,setFilterIncType]   = useState(null);
  const [reportExpanded,setReportExpanded] = useState(null);
  const [supView,setSupView]           = useState("dashboard");

  useEffect(()=>{
    if (!supplierFacing) apiFetch("/api/suppliers").then(d=>{ setSuppliers(d.suppliers||[]); if(!selectedID&&d.suppliers?.length) setSelectedID(d.suppliers[0].supplierID); }).catch(()=>{});
  },[supplierFacing]);

  const load = useCallback(async()=>{
    if (!selectedID) return;
    setLoading(true); setError(null);
    try {
      const q=new URLSearchParams();
      if(dateFrom) q.set("date_from",dateFrom);
      if(dateTo)   q.set("date_to",dateTo);
      setData(await apiFetch(`/api/dashboard/supplier/${selectedID}?${q}`));
      setFilterCategory(null); setFilterIncType(null);
    } catch(e){ setError(e.message); } finally { setLoading(false); }
  },[selectedID,dateFrom,dateTo]);

  useEffect(()=>{ load(); },[load]);

  const s = data?.scorecards||{};
  const incVsBench = (s.incident_rate_pct||0)-(s.portfolio_incident_rate||0);
  const retVsBench = (s.return_rate_pct||0)-(s.portfolio_return_rate||0);
  const resVsBench = (s.total_resolution_cost||0)-(s.portfolio_avg_res_cost||0);

  const skuInc = (data?.sku_incidents||[]).filter(r=> (!filterCategory||r.productCategory===filterCategory)&&(!filterIncType||r.incidentType===filterIncType));
  const skuRet = (data?.sku_returns||[]).filter(r=> (!filterCategory||r.productCategory===filterCategory));

  const skuIncAgg = Object.values(skuInc.reduce((acc,r)=>{
    if(!acc[r.productSKU]) acc[r.productSKU]={productSKU:r.productSKU,productCategory:r.productCategory,total_incidents:0,total_resolution_cost:0,avg_product_rating:[]};
    acc[r.productSKU].total_incidents += (+r.total_incidents||0);
    acc[r.productSKU].total_resolution_cost += (+r.total_resolution_cost||0);
    if(r.avg_product_rating) acc[r.productSKU].avg_product_rating.push(+r.avg_product_rating);
    return acc;
  },{})).map(r=>({...r,avg_product_rating:r.avg_product_rating.length?r.avg_product_rating.reduce((a,b)=>a+b,0)/r.avg_product_rating.length:0})).sort((a,b)=>b.total_incidents-a.total_incidents);

  const skuRetAgg = Object.values(skuRet.reduce((acc,r)=>{
    if(!acc[r.productSKU]) acc[r.productSKU]={productSKU:r.productSKU,productCategory:r.productCategory,total_returns:0,avg_product_rating:[]};
    acc[r.productSKU].total_returns += (+r.total_returns||0);
    if(r.avg_product_rating) acc[r.productSKU].avg_product_rating.push(+r.avg_product_rating);
    return acc;
  },{})).map(r=>({...r,avg_product_rating:r.avg_product_rating.length?r.avg_product_rating.reduce((a,b)=>a+b,0)/r.avg_product_rating.length:0})).sort((a,b)=>b.total_returns-a.total_returns);

  const hasFilter = filterCategory||filterIncType;

  return (
    <div>
      <div style={{display:"flex",gap:2,borderBottom:`1px solid ${C.border}`,marginBottom:24}}>
        {[{id:"dashboard",label:"Dashboard"},{id:"reports",label:"Reports"},{id:"customer_voice",label:"🗣 Customer Voice"}].map(t=>(
          <button key={t.id} onClick={()=>setSupView(t.id)}
            style={{background:"none",border:"none",borderBottom:supView===t.id?`2px solid ${C.blue}`:"2px solid transparent",color:supView===t.id?C.blue:C.muted,padding:"10px 20px",cursor:"pointer",fontSize:13,fontWeight:supView===t.id?600:400}}>
            {t.label}
          </button>
        ))}
      </div>
      {supView==="customer_voice" && <CustomerVoice supplierID={selectedID||initialSupplier}/>}
      {supView==="reports" && <SupplierReports supplierID={selectedID||initialSupplier}/>}
      {supView==="dashboard" && (
      <div>
      <div style={{display:"flex",alignItems:"flex-start",justifyContent:"space-between",marginBottom:20,gap:16,flexWrap:"wrap"}}>
        <div>
          <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>
            {supplierFacing?(data?.supplier?.supplierName||selectedID):"Supplier Account"}
          </h2>
          <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>{supplierFacing?"Performance report":"Per-supplier drill-down"}</p>
        </div>
        <div style={{display:"flex",flexDirection:"column",gap:8,alignItems:"flex-end"}}>
          <div style={{display:"flex",gap:10,alignItems:"center"}}>
            {!supplierFacing&&(
              <select value={selectedID} onChange={e=>setSelectedID(e.target.value)} style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"6px 12px",fontSize:13,minWidth:200}}>
                {suppliers.map(s=><option key={s.supplierID} value={s.supplierID}>{s.supplierName} ({s.supplierID})</option>)}
              </select>
            )}
          </div>
          <DateRangeControl dateFrom={dateFrom} dateTo={dateTo} onChange={(f,t)=>{setDateFrom(f);setDateTo(t);}} />
          {hasFilter&&(
            <div style={{display:"flex",alignItems:"center",gap:8}}>
              <span style={{fontSize:12,color:C.amber}}>Filtered: {[filterCategory,filterIncType].filter(Boolean).join(" · ")}</span>
              <button onClick={()=>{setFilterCategory(null);setFilterIncType(null);}} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:4,padding:"2px 8px",fontSize:11,cursor:"pointer"}}>Clear ✕</button>
            </div>
          )}
        </div>
      </div>

      {loading&&<Spinner/>}
      {error&&<ErrMsg message={error} onRetry={load}/>}

      {data&&!loading&&(
        <>
          <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:10,marginBottom:10}}>
            <Scorecard label="Total Orders"     value={fmt.num(s.total_orders)} />
            <Scorecard label="Product Cost"     value={fmt.cur(s.total_product_cost)} sub="Supplier's revenue" />
            <Scorecard label="Incident Rate"    value={fmt.pct(s.incident_rate_pct)}  color={incVsBench>0?C.red:C.green} />
            <Scorecard label="Return Rate"      value={fmt.pct(s.return_rate_pct)}    color={retVsBench>0?C.red:C.green} />
            <Scorecard label="Resolution Cost"  value={fmt.cur(s.total_resolution_cost)} />
            <Scorecard label="Returned Revenue" value={fmt.cur(s.returned_revenue)} warn />
          </div>

          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:10,marginBottom:20}}>
            <Scorecard label="Incident Rate vs Benchmark"
              value={`${incVsBench>0?"+":""}${incVsBench.toFixed(1)}pp`}
              color={incVsBench>0?C.red:C.green}
              sub={`Portfolio: ${fmt.pct(s.portfolio_incident_rate)}`} />
            <Scorecard label="Return Rate vs Benchmark"
              value={`${retVsBench>0?"+":""}${retVsBench.toFixed(1)}pp`}
              color={retVsBench>0?C.red:C.green}
              sub={`Portfolio avg: ${fmt.pct(s.portfolio_return_rate)}`} />
            <Scorecard label="Resolution Cost vs Benchmark"
              value={`${resVsBench>0?"+":""}${fmt.cur(Math.abs(resVsBench))}`}
              color={resVsBench>0?C.red:C.green}
              sub={`Portfolio avg/supplier: ${fmt.cur(s.portfolio_avg_res_cost)}`} />
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>Incident Rate by Category {filterCategory&&<span style={{color:C.blue,fontSize:10,marginLeft:6}}>● {filterCategory}</span>}</SLabel>
              <Card>
                <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter SKU tables &amp; all charts</div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.cat_incident_rate} layout="vertical" margin={{left:100,right:20}} onClick={e=>e?.activePayload&&setFilterCategory(prev=>prev===e.activePayload[0]?.payload?.productCategory?null:e.activePayload[0]?.payload?.productCategory)}>
                    <CartesianGrid strokeDasharray="3 3" stroke={C.surface} horizontal={false} />
                    <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                    <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                    <Tooltip {...getTT()} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
                    <Bar dataKey="incident_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                      {data.cat_incident_rate.map((r,i)=><Cell key={i} fill={r.productCategory===filterCategory?C.amber:C.blue} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </div>

            <div>
              <SLabel>Return Rate by Category</SLabel>
              <Card>
                <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter SKU tables &amp; all charts</div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.cat_return_rate} layout="vertical" margin={{left:100,right:20}} onClick={e=>e?.activePayload&&setFilterCategory(prev=>prev===e.activePayload[0]?.payload?.productCategory?null:e.activePayload[0]?.payload?.productCategory)}>
                    <CartesianGrid strokeDasharray="3 3" stroke={C.surface} horizontal={false} />
                    <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                    <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                    <Tooltip {...getTT()} formatter={v=>[`${(+v).toFixed(1)}%`,"Return Rate"]} />
                    <Bar dataKey="return_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                      {data.cat_return_rate.map((r,i)=><Cell key={i} fill={C.blue} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </div>
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>SKU Incidents {filterCategory&&`— ${filterCategory}`}</SLabel>
              <Card style={{padding:0}}>
                <div style={{overflowX:"auto",maxHeight:280,overflowY:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead style={{position:"sticky",top:0,background:C.bg}}>
                      <tr style={{borderBottom:`1px solid ${C.border}`}}>
                        {["SKU","Category","Incidents","Cost","Avg Cost","Rating"].map(h=>(
                          <th key={h} style={{padding:"8px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.06em",whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {skuIncAgg.slice(0,20).map((r,i)=>(
                        <tr key={i} style={{borderBottom:`1px solid rgba(255,255,255,0.04)`}}>
                          <td style={{padding:"7px 12px",color:C.blue,fontFamily:"monospace",fontWeight:600}}>{r.productSKU}</td>
                          <td style={{padding:"7px 12px",color:C.muted,fontSize:11}}>{r.productCategory}</td>
                          <td style={{padding:"7px 12px",color:C.text,fontFamily:"monospace"}}>{fmt.num(r.total_incidents)}</td>
                          <td style={{padding:"7px 12px",color:C.amber,fontFamily:"monospace"}}>{fmt.cur(r.total_resolution_cost)}</td>
                          <td style={{padding:"7px 12px",color:C.muted,fontFamily:"monospace"}}>{fmt.cur(r.total_resolution_cost/(r.total_incidents||1))}</td>
                          <td style={{padding:"7px 12px",color:(+r.avg_product_rating||0)<3?C.red:C.green,fontFamily:"monospace"}}>{(+r.avg_product_rating||0).toFixed(1)}/5</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>

            <div>
              <SLabel>SKU Returns {filterCategory&&`— ${filterCategory}`}</SLabel>
              <Card style={{padding:0}}>
                <div style={{overflowX:"auto",maxHeight:280,overflowY:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead style={{position:"sticky",top:0,background:C.bg}}>
                      <tr style={{borderBottom:`1px solid ${C.border}`}}>
                        {["SKU","Category","Returns","Rating"].map(h=>(
                          <th key={h} style={{padding:"8px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.06em",whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {skuRetAgg.slice(0,20).map((r,i)=>(
                        <tr key={i} style={{borderBottom:`1px solid rgba(255,255,255,0.04)`}}>
                          <td style={{padding:"7px 12px",color:C.blue,fontFamily:"monospace",fontWeight:600}}>{r.productSKU}</td>
                          <td style={{padding:"7px 12px",color:C.muted,fontSize:11}}>{r.productCategory}</td>
                          <td style={{padding:"7px 12px",color:C.text,fontFamily:"monospace"}}>{fmt.num(r.total_returns)}</td>
                          <td style={{padding:"7px 12px",color:(+r.avg_product_rating||0)<3?C.red:C.green,fontFamily:"monospace"}}>{(+r.avg_product_rating||0).toFixed(1)}/5</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            </div>
          </div>

          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>Return Reasons</SLabel>
              <Card>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={(data.return_reasons||[]).filter(r=>!filterCategory||(skuRet.some(s=>s.buyersRemorseReason===r.buyersRemorseReason)))} margin={{left:10,right:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke={C.surface} />
                    <XAxis dataKey="buyersRemorseReason" stroke={C.muted} tick={{fontSize:9}} tickFormatter={v=>(v||"").replace(/_/g," ")} />
                    <YAxis stroke={C.muted} tick={{fontSize:11}} />
                    <Tooltip {...getTT()} formatter={(v,n,p)=>[fmt.num(v),(p.payload.buyersRemorseReason||"").replace(/_/g," ")]} />
                    <Bar dataKey="total_returns" fill={C.teal} radius={[4,4,0,0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </div>

            <div>
              <SLabel>Incident Types {filterIncType&&<span style={{color:C.blue,fontSize:10,marginLeft:6}}>● {filterIncType}</span>}</SLabel>
              <Card>
                <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter SKU table</div>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart onClick={e=>e?.activePayload&&setFilterIncType(prev=>prev===e.activePayload[0]?.payload?.incidentType?null:e.activePayload[0]?.payload?.incidentType)}>
                    <Pie data={data.incident_types||[]} dataKey="total_incidents" nameKey="incidentType" cx="50%" cy="50%" outerRadius={70} cursor="pointer" label={({name,percent})=>`${(name||"").replace(/_/g," ")} ${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={9}>
                      {(data.incident_types||[]).map((r,i)=><Cell key={i} fill={C.blue} fillOpacity={[1,0.7,0.5,0.35,0.22,0.14][i%6]} opacity={filterIncType&&r.incidentType!==filterIncType?0.35:1} />)}
                    </Pie>
                    <Tooltip {...getTT()} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </div>

            <div>
              <SLabel>Resolution Mix</SLabel>
              <Card>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie data={data.resolution_mix||[]} dataKey="total_incidents" nameKey="incidentResolution" cx="50%" cy="50%" outerRadius={70} label={({name,percent})=>`${(name||"").replace(/_/g," ")} ${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={9}>
                      {(data.resolution_mix||[]).map((_,i)=><Cell key={i} fill={C.blue} fillOpacity={[1,0.7,0.5,0.35,0.22,0.14][i%6]} />)}
                    </Pie>
                    <Tooltip {...getTT()} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </div>
          </div>

          {(data.reports||[]).length>0&&(
            <>
              <SLabel>Approved Reports</SLabel>
              {data.reports.map((r,i)=>(
                <Card key={i} style={{marginBottom:10}}>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                    <div style={{display:"flex",gap:12,alignItems:"center"}}>
                      <span style={{fontSize:13,fontWeight:600,color:C.text}}>{r.reportDate}</span>
                      <Badge variant={r.reportType?.includes("adhoc")?"pending":"approved"}>{r.reportType?.includes("adhoc")?"Ad-hoc":"Monthly"}</Badge>
                      <span style={{fontSize:12,color:C.muted}}>Approved by {r.approvedBy}</span>
                    </div>
                    <button onClick={()=>setReportExpanded(reportExpanded===i?null:i)} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",cursor:"pointer",fontSize:12}}>
                      {reportExpanded===i?"Collapse":"Read report"}
                    </button>
                  </div>
                  {reportExpanded===i&&(
                    <div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:500,overflowY:"auto",paddingTop:12,marginTop:12,borderTop:`1px solid ${C.border}`}}>
                      {r.reportNarrative}
                    </div>
                  )}
                </Card>
              ))}
            </>
          )}
        </>
      )}
    </div>
      )}
    </div>
  );
}

// ── New Report ────────────────────────────────────────────────────────────────
const PIPELINE_STEPS = [
  { id: "discover",  label: "Discover",  desc: "Selecting data tables",          duration: 8  },
  { id: "pull",      label: "Pull",      desc: "Executing BigQuery queries",      duration: 35 },
  { id: "analyse",   label: "Analyse",   desc: "Processing & scoring data",       duration: 40 },
  { id: "generate",  label: "Generate",  desc: "Writing report narrative",        duration: 50 },
  { id: "validate",  label: "Validate",  desc: "Checking against ground truth",   duration: 15 },
  { id: "review",    label: "Review",    desc: "Applying policy rules",           duration: 5  },
  { id: "publish",   label: "Publish",   desc: "Saving approved report",          duration: 5  },
];

function PipelineProgress({ startTime, status }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!startTime) return;
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 1000)), 500);
    return () => clearInterval(iv);
  }, [startTime]);

  const TERMINAL = ["pending_review","pending_publish","approved","rejected","escalated","failed","completed"];
  const done = TERMINAL.includes(status);

  let cumulative = 0;
  let activeIdx = 0;
  for (let i = 0; i < PIPELINE_STEPS.length; i++) {
    if (elapsed >= cumulative) activeIdx = i;
    cumulative += PIPELINE_STEPS[i].duration;
  }
  if (done) activeIdx = PIPELINE_STEPS.length - 1;

  const totalEstimated = PIPELINE_STEPS.reduce((s, n) => s + n.duration, 0);
  const progress = done ? 100 : Math.min((elapsed / totalEstimated) * 100, 95);
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;

  return (
    <div style={{ padding: "20px 0" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontSize: 12, color: C.muted }}>Pipeline progress</span>
        <span style={{ fontSize: 12, color: C.muted, fontFamily: "monospace" }}>{timeStr} elapsed</span>
      </div>
      <div style={{ width: "100%", height: 3, background: C.surface, borderRadius: 2, marginBottom: 24, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${progress}%`, background: done ? C.green : C.blue, borderRadius: 2, transition: "width 0.8s ease" }} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {PIPELINE_STEPS.map((step, i) => {
          const isActive   = !done && i === activeIdx;
          const isComplete = done ? true : i < activeIdx;
          return (
            <div key={step.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 10px", borderRadius: 6, background: isActive ? "rgba(96,165,250,0.08)" : "transparent", border: isActive ? `1px solid rgba(96,165,250,0.2)` : "1px solid transparent", transition: "all 0.3s" }}>
              <div style={{ width: 20, height: 20, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: isComplete ? "rgba(34,197,94,0.15)" : isActive ? "rgba(96,165,250,0.15)" : C.surface, border: `1px solid ${isComplete ? C.green : isActive ? C.blue : C.border}` }}>
                {isComplete ? <span style={{ fontSize: 11, color: C.green }}>✓</span> : isActive ? <div style={{ width: 8, height: 8, borderRadius: "50%", border: `1.5px solid ${C.blue}`, borderTopColor: "transparent", animation: "spin 0.7s linear infinite" }} /> : <span style={{ fontSize: 9, color: C.muted }}>○</span>}
              </div>
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 13, fontWeight: isActive ? 600 : 400, color: isComplete ? C.muted : isActive ? C.text : C.muted }}>{step.label}</span>
                {isActive && <span style={{ fontSize: 11, color: C.muted, marginLeft: 8 }}>{step.desc}</span>}
              </div>
              <span style={{ fontSize: 10, color: C.border, fontFamily: "monospace" }}>{i + 1}/{PIPELINE_STEPS.length}</span>
            </div>
          );
        })}
      </div>
      {done && (
        <div style={{ marginTop: 16, padding: "10px 14px", background: "rgba(34,197,94,0.08)", border: "1px solid rgba(34,197,94,0.2)", borderRadius: 8, display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ color: C.green }}>✓</span>
          <span style={{ fontSize: 13, color: C.green, fontWeight: 600 }}>Pipeline complete in {timeStr}</span>
        </div>
      )}
    </div>
  );
}

function NewReport({onCreated}) {
  const [suppliers,setSuppliers] = useState([]);
  const [reportType,setReportType] = useState("adhoc_business");
  const [supplierID,setSupplierID] = useState("");
  const [goal,setGoal]         = useState("");
  const [reportTitle,setReportTitle] = useState("");
  const [running,setRunning] = useState(false);
  const [runID,setRunID] = useState(null);
  const [runData,setRunData] = useState(null);
  const [status,setStatus]   = useState(null);
  const [startTime,setStartTime] = useState(null);
  const [error,setError]     = useState(null);
  const [sharing,setSharing] = useState(false);
  const [refreshKey,setRefreshKey] = useState(0);
  const pollRef = useRef(null);

  useEffect(()=>{ apiFetch("/api/suppliers").then(d=>setSuppliers(d.suppliers||[])).catch(()=>{}); return ()=>{ if(pollRef.current) clearInterval(pollRef.current); }; },[]);

  const isSupplier = reportType.includes("supplier");
  const isReady    = status&&!["running","starting"].includes(status);
  const TERMINAL   = ["pending_review","pending_publish","approved","rejected","escalated","failed","completed"];
  const IN_QUEUE   = ["pending_review","escalated"];

  const handleSubmit = async () => {
    if (!goal.trim()||(isSupplier&&!supplierID)) return;
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setRunning(true); setError(null); setStatus("starting"); setRunData(null); setRunID(null); setStartTime(Date.now());
    try {
      const res = await apiFetch("/api/runs",{method:"POST",body:JSON.stringify({reportType,supplierID:isSupplier?supplierID:null,goal,reportTitle:reportTitle.trim()||null})});
      const newRunID = res.runID;
      setRunID(newRunID); setStatus("running");
      const pollStart = Date.now();
      pollRef.current = setInterval(async()=>{
        try {
          const s = await apiFetch(`/api/runs/${newRunID}/status`);
          if (s.status) setStatus(s.status);
          const elapsed = Date.now() - pollStart;
          const shouldStop = (s.status && TERMINAL.includes(s.status)) || elapsed > 180000;
          if (shouldStop) {
            clearInterval(pollRef.current); pollRef.current = null; setRunning(false);
            if (elapsed > 180000 && (!s.status || !TERMINAL.includes(s.status))) setStatus("pending_review");
            try { const full = await apiFetch(`/api/runs/${newRunID}`); setRunData(full); } catch(e) {}
            setRefreshKey(k=>k+1);
          }
        } catch(e){ clearInterval(pollRef.current); pollRef.current = null; setRunning(false); }
      },5000);
    } catch(e){ setError(e.message); setRunning(false); }
  };

  const handleShare = async(share) => {
    if(!runID) return;
    setSharing(true);
    try {
      await apiFetch("/api/decisions",{method:"POST",body:JSON.stringify({runID,decision:"approved",reviewer:"account_manager",reason:share?"Approved for supplier sharing":"Approved internal only",shareWithSupplier:share})});
      if(onCreated) onCreated();
      alert(share?"Report approved and shared with supplier.":"Report saved.");
    } catch(e){ setError(e.message); } finally { setSharing(false); }
  };

  return (
    <div style={{maxWidth:700}}>
      <div style={{marginBottom:24}}>
        <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>New Report</h2>
        <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>Trigger an ad-hoc agent run · review results · choose to share or keep internal</p>
      </div>
      {error&&<ErrMsg message={error}/>}
      <Card style={{display:"flex",flexDirection:"column",gap:16,marginBottom:24}}>
        <div>
          <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Report type</label>
          <select value={reportType} onChange={e=>setReportType(e.target.value)} disabled={running} style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,opacity:running?0.5:1}}>
            <option value="adhoc_business">Ad-hoc Business Overview</option>
            <option value="adhoc_supplier">Ad-hoc Supplier Account</option>
          </select>
        </div>
        {isSupplier&&(
          <div>
            <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Supplier</label>
            <select value={supplierID} onChange={e=>setSupplierID(e.target.value)} disabled={running} style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,opacity:running?0.5:1}}>
              <option value="">Select a supplier...</option>
              {suppliers.map(s=><option key={s.supplierID} value={s.supplierID}>{s.supplierName} ({s.supplierID})</option>)}
            </select>
          </div>
        )}
        <div>
          <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Title <span style={{fontWeight:400,color:C.muted}}>(optional)</span></label>
          <input value={reportTitle} onChange={e=>setReportTitle(e.target.value)} disabled={running}
            placeholder="e.g. SUP004 Q1 2026 review"
            style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,fontFamily:"inherit",boxSizing:"border-box",opacity:running?0.5:1}}/>
        </div>
        <div>
          <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Report goal</label>
          <textarea value={goal} onChange={e=>setGoal(e.target.value)} disabled={running} placeholder="Describe what you need, e.g. Analyse SUP002 incident trends last 6 months vs previous 6 months, broken down by category and SKU..." rows={4} style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"10px 12px",fontSize:13,resize:"vertical",fontFamily:"inherit",boxSizing:"border-box",opacity:running?0.5:1}} />
        </div>
        <button onClick={handleSubmit} disabled={running||!goal.trim()||(isSupplier&&!supplierID)} style={{background:running?C.surface:"rgba(96,165,250,0.2)",border:`1px solid ${running?C.border:C.blue}`,color:running?C.muted:C.blue,borderRadius:7,padding:"10px 18px",fontSize:13,fontWeight:600,cursor:running?"not-allowed":"pointer",opacity:(running||!goal.trim()||(isSupplier&&!supplierID))?0.5:1}}>
          {running?"Running pipeline...":"Run Report"}
        </button>
        {(running || isReady) && startTime && (
          <div style={{borderTop:`1px solid ${C.border}`,paddingTop:16,marginTop:4}}>
            <PipelineProgress startTime={startTime} status={status} />
            {runID && <div style={{fontSize:11,color:C.border,fontFamily:"monospace",marginTop:8}}>Run ID: {runID}</div>}
          </div>
        )}
      </Card>
      {isReady&&status&&IN_QUEUE.includes(status)&&!runData&&(
        <div style={{padding:"14px 18px",background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:10,marginBottom:16}}>
          <div style={{fontSize:13,fontWeight:600,color:C.amber,marginBottom:4}}>Report sent to review queue</div>
          <div style={{fontSize:12,color:C.muted}}>Confidence below auto-approve threshold. Find it in Recent Reports below.</div>
        </div>
      )}
      {isReady&&runData&&(
        <div>
          {IN_QUEUE.includes(status)
            ? <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:10}}>
                <div style={{fontSize:13,fontWeight:600,color:C.amber,marginBottom:4}}>⚠ Low confidence — pending admin review</div>
                <div style={{fontSize:12,color:C.muted}}>Confidence: {((runData.confidence||0)*100).toFixed(0)}% · You can read the report below. Sharing with supplier requires admin approval in the Queue tab.</div>
              </div>
            : <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.2)",borderRadius:10}}>
                <div style={{fontSize:13,fontWeight:600,color:C.green,marginBottom:4}}>Report ready — review below</div>
                <div style={{fontSize:12,color:C.green}}>Confidence: {((runData.confidence||0)*100).toFixed(0)}% · {runData.policyDecision?.replace(/_/g," ")}</div>
              </div>
          }
          <Card style={{marginBottom:16}}>
            <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:10}}>Report Narrative</div>
            <div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:400,overflowY:"auto"}}>
              {runData.reportNarrative||"No narrative generated."}
            </div>
          </Card>
          <Card>
            <div style={{fontSize:13,fontWeight:600,color:C.text,marginBottom:12}}>What would you like to do with this report?</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
              <button onClick={()=>handleShare(false)} disabled={sharing} style={{padding:"16px",border:`1px solid ${C.border}`,borderRadius:8,background:C.surface,cursor:"pointer",textAlign:"left",transition:"all 0.15s"}}
                onMouseEnter={e=>e.currentTarget.style.borderColor=C.blue}
                onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}>
                <div style={{fontSize:13,fontWeight:600,color:C.text,marginBottom:4}}>🔒 Internal only</div>
                <div style={{fontSize:12,color:C.muted}}>Save to control plane. Not visible to supplier.</div>
              </button>
              {isSupplier&&(
                <div style={{padding:"16px",border:`1px solid ${IN_QUEUE.includes(status)?C.border:C.border}`,borderRadius:8,background:C.surface,opacity:IN_QUEUE.includes(status)?0.5:1,cursor:IN_QUEUE.includes(status)?"not-allowed":"pointer",textAlign:"left",transition:"all 0.15s"}}
                  onClick={!IN_QUEUE.includes(status)?()=>handleShare(true):undefined}
                  onMouseEnter={e=>{ if(!IN_QUEUE.includes(status)) e.currentTarget.style.borderColor=C.teal; }}
                  onMouseLeave={e=>{ e.currentTarget.style.borderColor=C.border; }}>
                  <div style={{fontSize:13,fontWeight:600,color:IN_QUEUE.includes(status)?C.muted:C.teal,marginBottom:4}}>🔗 Share with supplier</div>
                  <div style={{fontSize:12,color:C.muted}}>{IN_QUEUE.includes(status)?"Requires admin approval in Queue first.":"Appears in supplier's view alongside their standard dashboard."}</div>
                </div>
              )}
            </div>
          </Card>
        </div>
      )}
      <RecentReports refreshKey={refreshKey}/>
    </div>
  );
}


// ── Recent Reports ────────────────────────────────────────────────────────────
function RecentReports({refreshKey}) {
  const [reports,setReports] = useState([]);
  const [loading,setLoading] = useState(true);
  const [expanded,setExpanded] = useState(null);

  const load = useCallback(()=>{
    setLoading(true);
    apiFetch("/api/recent-reports?limit=10")
      .then(d=>{ setReports(d.reports||[]); setLoading(false); })
      .catch(()=>setLoading(false));
  },[]);

  useEffect(()=>{ load(); },[load, refreshKey]);

  if (loading) return <Spinner/>;
  if (!reports.length) return null;

  const statusColor = s => ["approved","edited_and_approved","auto_approved"].includes(s)?C.green:s==="rejected"?C.red:["pending_review","pending","pending_publish","escalated"].includes(s)?C.amber:C.muted;
  const statusLabel = s => s==="approved"||s==="edited_and_approved"?"Approved":s==="auto_approved"?"Auto-approved":["pending_review","pending","pending_publish","escalated"].includes(s)?"Awaiting review":s==="rejected"?"Rejected":s==="running"?"Processing":s||"Processing";

  return (
    <div style={{marginTop:32}}>
      <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:14,display:"flex",alignItems:"center",gap:10}}>
        Recent Reports
        <button onClick={load} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:4,padding:"1px 8px",fontSize:10,cursor:"pointer"}}>↻ Refresh</button>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {reports.map((run,i)=>{
          const ds = run.displayStatus||run.decision||run.status;
          return (
            <div key={run.runID} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,overflow:"hidden"}}>
              <div style={{padding:"14px 18px",display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:4,flexWrap:"wrap"}}>
                    <span style={{fontSize:13,fontWeight:600,color:C.text}}>
                      {run.goal&&run.goal.startsWith("[")
                        ? run.goal.slice(1,run.goal.indexOf("]"))
                        : fmt.label(run.reportType)}
                    </span>
                    {(!run.goal||!run.goal.startsWith("["))&&run.goal&&(
                      <span style={{fontSize:11,color:C.muted,fontStyle:"italic",maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                        {run.goal.length>50?run.goal.slice(0,50)+"...":run.goal}
                      </span>
                    )}
                    {run.supplierID&&<Badge>{run.supplierID}</Badge>}
                    <span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:`${statusColor(ds)}18`,color:statusColor(ds),fontWeight:600,textTransform:"uppercase",letterSpacing:"0.04em"}}>{statusLabel(ds)}</span>
                  </div>
                  <div style={{fontSize:11,color:C.muted}}>
                    {run.startedAt?new Date(run.startedAt).toLocaleString("en-GB"):""}
                    {run.confidence?` · Confidence: ${((run.confidence||0)*100).toFixed(0)}%`:""}
                    {run.approvedBy?` · ${run.approvedBy}`:run.reviewer?` · ${run.reviewer}`:""}
                  </div>
                </div>
                <button onClick={()=>setExpanded(expanded===i?null:i)}
                  style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer",flexShrink:0}}>
                  {expanded===i?"Hide ↑":"View ↓"}
                </button>
              </div>
              {expanded===i&&(
                <div style={{borderTop:`1px solid ${C.border}`,padding:"16px 18px"}}>
                  {run.reason&&<div style={{padding:"10px 14px",background:"rgba(239,68,68,0.06)",border:"1px solid rgba(239,68,68,0.15)",borderRadius:7,marginBottom:14,fontSize:12,color:C.red}}>Rejection reason: {run.reason}</div>}
                  {run.approvedBy&&<div style={{fontSize:11,color:C.muted,marginBottom:12}}>Approved by {run.approvedBy}{run.approvedAt?` · ${new Date(run.approvedAt).toLocaleDateString("en-GB")}`:""}</div>}
                  {run.reportNarrative
                    ? <div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:500,overflowY:"auto"}}>{run.reportNarrative}</div>
                    : <div style={{padding:"12px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,fontSize:13,color:C.muted,fontStyle:"italic"}}>
                        {["pending_review","pending","escalated"].includes(ds)?"Awaiting admin review in Queue. Narrative will appear here once approved.":ds==="rejected"?"This report was rejected and not published.":"Report is still processing."}
                      </div>
                  }
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Ask a Question — Conversational ──────────────────────────────────────────
function AskQuestion() {
  const [suppliers,setSuppliers]     = useState([]);
  const [supplierID,setSupplierID]   = useState("");
  const [question,setQuestion]       = useState("");
  const [loading,setLoading]         = useState(false);
  const [error,setError]             = useState(null);
  const [sessionID,setSessionID]     = useState(null);
  const [exchanges,setExchanges]     = useState([]);
  const [expandedSQL,setExpandedSQL] = useState({});
  const [expandedData,setExpandedData] = useState({});
  const inputRef = useRef(null);
  const bottomRef = useRef(null);

  useEffect(()=>{
    apiFetch("/api/suppliers").then(d=>setSuppliers(d.suppliers||[])).catch(()=>{});
  },[]);

  useEffect(()=>{
    if (bottomRef.current) bottomRef.current.scrollIntoView({behavior:"smooth"});
  },[exchanges]);

  const startSession = async (sid) => {
    const res = await apiFetch(`/api/ask/session${sid ? `?supplierID=${sid}` : ""}`, {method:"POST"});
    setSessionID(res.sessionID);
    setExchanges([]);
    setError(null);
    return res.sessionID;
  };

  const handleNewConversation = async () => {
    if (sessionID) await apiFetch(`/api/ask/session/${sessionID}`, {method:"DELETE"}).catch(()=>{});
    await startSession(supplierID);
    setQuestion("");
    inputRef.current?.focus();
  };

  const handleSupplierChange = async (val) => {
    setSupplierID(val);
    if (sessionID) {
      await apiFetch(`/api/ask/session/${sessionID}`, {method:"DELETE"}).catch(()=>{});
      await startSession(val);
      setExchanges([]);
    }
  };

  const handleAsk = async () => {
    if (!question.trim() || loading) return;
    setError(null);
    let sid = sessionID;
    if (!sid) sid = await startSession(supplierID);
    const q = question.trim();
    setQuestion("");
    setLoading(true);
    setExchanges(prev => [...prev, {question: q, sql: null, data: null, rows: null, loading: true}]);
    try {
      const res = await apiFetch("/api/ask", {
        method: "POST",
        body: JSON.stringify({question: q, supplierID: supplierID||null, sessionID: sid}),
      });
      setExchanges(prev => prev.map((ex, i) =>
        i === prev.length - 1 ? {question: q, sql: res.sql, data: res.data, rows: res.rows, loading: false} : ex
      ));
    } catch(e) {
      setExchanges(prev => prev.map((ex, i) =>
        i === prev.length - 1 ? {question: q, sql: null, data: null, rows: null, loading: false, error: e.message} : ex
      ));
      setError(e.message);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const toggleSQL  = (i) => setExpandedSQL(p => ({...p, [i]: !p[i]}));
  const toggleData = (i) => setExpandedData(p => ({...p, [i]: !p[i]}));

  const examples = [
    "Which supplier had the highest incident rate last month?",
    "Top 10 SKUs by resolution cost in the last 30 days",
    "Most common return reasons for Electronics",
    "Compare incident rates across fulfilment channels",
    "Show me damage_defect incidents by category this quarter",
  ];

  const hasConversation = exchanges.length > 0;

  return (
    <div style={{display:"flex",flexDirection:"column",height:"calc(100vh - 120px)",maxWidth:900}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:16,flexShrink:0}}>
        <div>
          <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Ask a Question</h2>
          <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>
            Conversational analysis · {hasConversation ? `${exchanges.length} question${exchanges.length!==1?"s":""} this session` : "Start a new conversation"}
          </p>
        </div>
        <div style={{display:"flex",gap:10,alignItems:"center"}}>
          <select value={supplierID} onChange={e=>handleSupplierChange(e.target.value)}
            style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"6px 12px",fontSize:13,minWidth:180}}>
            <option value="">All suppliers</option>
            {suppliers.map(s=><option key={s.supplierID} value={s.supplierID}>{s.supplierName}</option>)}
          </select>
          {hasConversation && (
            <button onClick={handleNewConversation}
              style={{background:C.surface,border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"6px 14px",fontSize:12,cursor:"pointer",whiteSpace:"nowrap"}}>
              New conversation
            </button>
          )}
        </div>
      </div>

      <div style={{flex:1,overflowY:"auto",display:"flex",flexDirection:"column",gap:16,paddingBottom:8}}>
        {!hasConversation && (
          <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",flex:1,gap:24}}>
            <div style={{fontSize:32,opacity:0.3}}>💬</div>
            <div style={{textAlign:"center"}}>
              <div style={{fontSize:13,color:C.muted,marginBottom:16}}>Ask anything about your supplier data</div>
              <div style={{display:"flex",flexWrap:"wrap",gap:8,justifyContent:"center",maxWidth:600}}>
                {examples.map((ex,i)=>(
                  <button key={i} onClick={()=>{ setQuestion(ex); inputRef.current?.focus(); }}
                    style={{background:C.surface,border:`1px solid ${C.border}`,color:C.muted,borderRadius:20,padding:"6px 14px",fontSize:12,cursor:"pointer",transition:"all 0.15s"}}
                    onMouseEnter={e=>{ e.currentTarget.style.borderColor=C.blue; e.currentTarget.style.color=C.blue; }}
                    onMouseLeave={e=>{ e.currentTarget.style.borderColor=C.border; e.currentTarget.style.color=C.muted; }}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
        {exchanges.map((ex, i) => (
          <div key={i} style={{display:"flex",flexDirection:"column",gap:8}}>
            <div style={{display:"flex",justifyContent:"flex-end"}}>
              <div style={{background:"rgba(96,165,250,0.15)",border:`1px solid rgba(96,165,250,0.25)`,borderRadius:"12px 12px 4px 12px",padding:"10px 16px",maxWidth:"75%",fontSize:13,color:C.text,lineHeight:1.5}}>
                {ex.question}
              </div>
            </div>
            <div style={{display:"flex",justifyContent:"flex-start"}}>
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:"4px 12px 12px 12px",padding:"12px 16px",maxWidth:"90%",minWidth:200}}>
                {ex.loading ? (
                  <div style={{display:"flex",alignItems:"center",gap:10,color:C.muted,fontSize:13}}>
                    <div style={{width:14,height:14,border:`2px solid ${C.blue}`,borderTopColor:"transparent",borderRadius:"50%",animation:"spin 0.7s linear infinite",flexShrink:0}}/>
                    Querying data...
                  </div>
                ) : ex.error ? (
                  <div style={{color:C.red,fontSize:13}}>{ex.error}</div>
                ) : (
                  <>
                    <div style={{fontSize:13,color:C.muted,marginBottom:10}}>
                      {ex.rows === 0 ? "No results found." : `${ex.rows} row${ex.rows!==1?"s":""} returned`}
                    </div>
                    {ex.data && ex.data.length > 0 && (
                      <div style={{marginBottom:8}}>
                        <div style={{overflowX:"auto",maxHeight:expandedData[i]?400:160,overflowY:"auto",transition:"max-height 0.2s"}}>
                          <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                            <thead>
                              <tr style={{borderBottom:`1px solid ${C.border}`}}>
                                {Object.keys(ex.data[0]).map(col=>(
                                  <th key={col} style={{padding:"6px 10px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:11,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap"}}>{col}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {ex.data.map((row,j)=>(
                                <tr key={j} style={{borderBottom:`1px solid rgba(255,255,255,0.04)`}}>
                                  {Object.values(row).map((val,k)=>(
                                    <td key={k} style={{padding:"6px 10px",color:C.text,fontFamily:typeof val==="number"?"monospace":"inherit",fontSize:12}}>
                                      {typeof val==="number"?(+val).toLocaleString(undefined,{maximumFractionDigits:2}):String(val??"—")}
                                    </td>
                                  ))}
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        {ex.rows > 5 && (
                          <button onClick={()=>toggleData(i)} style={{background:"none",border:"none",color:C.blue,fontSize:11,cursor:"pointer",padding:"4px 0",marginTop:4}}>
                            {expandedData[i] ? "Show less ↑" : `Show all ${ex.rows} rows ↓`}
                          </button>
                        )}
                      </div>
                    )}
                    {ex.sql && (
                      <div>
                        <button onClick={()=>toggleSQL(i)} style={{background:"none",border:"none",color:C.muted,fontSize:11,cursor:"pointer",padding:"2px 0",display:"flex",alignItems:"center",gap:4}}>
                          <span style={{fontFamily:"monospace"}}>SQL</span>
                          <span style={{fontSize:9}}>{expandedSQL[i]?"▲":"▼"}</span>
                        </button>
                        {expandedSQL[i] && (
                          <pre style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:10,fontSize:11,color:C.muted,overflow:"auto",whiteSpace:"pre-wrap",margin:"6px 0 0",fontFamily:"monospace"}}>
                            {ex.sql}
                          </pre>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div style={{flexShrink:0,paddingTop:12,borderTop:`1px solid ${C.border}`}}>
        {error && <ErrMsg message={error} />}
        <div style={{display:"flex",gap:10}}>
          <input
            ref={inputRef}
            value={question}
            onChange={e=>setQuestion(e.target.value)}
            onKeyDown={e=>e.key==="Enter"&&!e.shiftKey&&handleAsk()}
            placeholder={hasConversation ? "Ask a follow-up question..." : "Ask anything about your supplier data..."}
            style={{flex:1,background:C.surface,border:`1px solid ${loading?C.blue:C.border}`,color:C.text,borderRadius:8,padding:"10px 14px",fontSize:13,fontFamily:"inherit",outline:"none",transition:"border-color 0.15s"}}
          />
          <button onClick={handleAsk} disabled={loading||!question.trim()}
            style={{background:"rgba(96,165,250,0.2)",border:`1px solid ${C.blue}`,color:C.blue,borderRadius:8,padding:"10px 20px",fontSize:13,fontWeight:600,cursor:loading||!question.trim()?"not-allowed":"pointer",opacity:loading||!question.trim()?0.5:1,whiteSpace:"nowrap"}}>
            {loading ? "..." : "Ask →"}
          </button>
        </div>
        {hasConversation && (
          <div style={{fontSize:11,color:C.muted,marginTop:6,textAlign:"center"}}>
            Claude remembers this conversation · {exchanges.length}/10 turns used
          </div>
        )}
      </div>
    </div>
  );
}


// ── Customer Voice ────────────────────────────────────────────────────────────
function CustomerVoice({supplierID}) {
  const [data,setData]                   = useState(null);
  const [loading,setLoading]             = useState(true);
  const [error,setError]                 = useState(null);
  const [selectedMonth,setSelectedMonth] = useState(null);
  const [expandedSKU,setExpandedSKU]     = useState(null);
  const [activeTab,setActiveTab]         = useState({});

  const load = useCallback((month=null)=>{
    if (!supplierID) return;
    setLoading(true); setError(null);
    const q = month ? `?month=${month}` : "";
    apiFetch(`/api/customer-voice/${supplierID}${q}`)
      .then(d=>{ setData(d); setSelectedMonth(d.selectedMonth); setLoading(false); })
      .catch(e=>{ setError(e.message); setLoading(false); });
  },[supplierID]);

  useEffect(()=>{ load(); },[load]);

  const effortColor = e => e==="low"?C.green:e==="medium"?C.amber:C.red;
  const sevColor    = s => s==="high"?C.red:s==="medium"?C.amber:C.blue;
  const catIcon     = c => ({product_quality:"🔧",packaging:"📦",fulfilment:"🏭",listing_accuracy:"📋"}[c]||"⚠");
  const catLabel    = c => ({product_quality:"Product Quality",packaging:"Packaging",fulfilment:"Fulfilment",listing_accuracy:"Listing Accuracy"}[c]||c);
  const rcConfColor = c => c==="high"?C.red:c==="medium"?C.amber:C.green;

  if (loading) return <Spinner/>;
  if (error)   return <ErrMsg message={error}/>;
  if (!data?.skus?.length) return (
    <div style={{textAlign:"center",padding:60,color:C.muted,fontSize:13}}>
      No Customer Voice data available yet. Data is generated monthly by the Comment Intelligence agent.
    </div>
  );

  return (
    <div>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:24,flexWrap:"wrap",gap:12}}>
        <div>
          <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Customer Voice</h2>
          <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>
            {data.skus.length} flagged SKU{data.skus.length!==1?"s":""} · AI analysis of customer incident &amp; return comments
          </p>
        </div>
        <select value={selectedMonth||""} onChange={e=>{ setSelectedMonth(e.target.value); load(e.target.value); setExpandedSKU(null); }}
          style={{background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"6px 12px",fontSize:13}}>
          {(data.months||[]).map(m=>(
            <option key={m} value={m}>{new Date(m+"T00:00:00").toLocaleDateString("en-GB",{month:"long",year:"numeric"})}</option>
          ))}
        </select>
      </div>

      <div style={{display:"flex",flexDirection:"column",gap:12}}>
        {data.skus.map((sku,i)=>{
          const isOpen = expandedSKU === i;
          const tab    = activeTab[i] || "rootcauses";
          const incDev = ((+sku.skuIncidentRate) - (+sku.catIncidentRate)).toFixed(1);
          const retDev = ((+sku.skuReturnRate)   - (+sku.catReturnRate)).toFixed(1);

          return (
            <div key={sku.productSKU} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:12,overflow:"hidden"}}>
              <div onClick={()=>setExpandedSKU(isOpen?null:i)} style={{padding:"16px 20px",cursor:"pointer"}}>
                <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:8,flexWrap:"wrap"}}>
                  <span style={{fontSize:13,fontWeight:700,color:C.blue,fontFamily:"monospace"}}>{sku.productSKU}</span>
                  <Badge>{sku.productCategory}</Badge>
                  <span style={{fontSize:11,color:C.muted}}>{sku.totalOrders} orders · {sku.incidentCommentCount} incident comments · {sku.returnCommentCount} return comments</span>
                  <span style={{marginLeft:"auto",color:C.muted,fontSize:13}}>{isOpen?"▲":"▼"}</span>
                </div>
                <div style={{display:"flex",gap:24,flexWrap:"wrap"}}>
                  <div style={{display:"flex",alignItems:"center",gap:8}}>
                    <span style={{fontSize:11,color:C.muted}}>Incident rate</span>
                    <span style={{fontSize:13,fontWeight:700,color:C.red,fontFamily:"monospace"}}>{(+sku.skuIncidentRate).toFixed(1)}%</span>
                    <span style={{fontSize:11,color:C.muted}}>vs {(+sku.catIncidentRate).toFixed(1)}% avg</span>
                    <span style={{fontSize:11,padding:"1px 6px",borderRadius:4,background:"rgba(239,68,68,0.12)",color:C.red,fontWeight:600}}>+{incDev}pp</span>
                  </div>
                  <div style={{display:"flex",alignItems:"center",gap:8}}>
                    <span style={{fontSize:11,color:C.muted}}>Return rate</span>
                    <span style={{fontSize:13,fontWeight:700,color:C.amber,fontFamily:"monospace"}}>{(+sku.skuReturnRate).toFixed(1)}%</span>
                    <span style={{fontSize:11,color:C.muted}}>vs {(+sku.catReturnRate).toFixed(1)}% avg</span>
                    <span style={{fontSize:11,padding:"1px 6px",borderRadius:4,background:+retDev>0?"rgba(245,158,11,0.12)":"rgba(34,197,94,0.12)",color:+retDev>0?C.amber:C.green,fontWeight:600}}>{+retDev>0?"+":""}{retDev}pp</span>
                  </div>
                  <span style={{fontSize:11,color:C.muted,marginLeft:"auto"}}>AI confidence: <span style={{color:rcConfColor(sku.confidence>=0.85?"high":sku.confidence>=0.7?"medium":"low"),fontWeight:700}}>{((sku.confidence||0)*100).toFixed(0)}%</span></span>
                </div>
              </div>

              {isOpen&&(
                <div style={{borderTop:`1px solid ${C.border}`}}>
                  <div style={{display:"flex",gap:0,borderBottom:`1px solid ${C.border}`,padding:"0 20px"}}>
                    {[
                      {id:"rootcauses",  label:`Root Causes (${(sku.rootCauses||[]).length})`},
                      {id:"incidents",   label:`Incident Themes (${(sku.incidentThemes||[]).length})`},
                      {id:"returns",     label:`Return Themes (${(sku.returnThemes||[]).length})`},
                      {id:"improvements",label:`Improvements (${(sku.improvements||[]).length})`},
                    ].map(t=>(
                      <button key={t.id} onClick={()=>setActiveTab(p=>({...p,[i]:t.id}))}
                        style={{background:"none",border:"none",borderBottom:tab===t.id?`2px solid ${C.blue}`:"2px solid transparent",color:tab===t.id?C.blue:C.muted,padding:"10px 14px",cursor:"pointer",fontSize:12,fontWeight:tab===t.id?600:400,whiteSpace:"nowrap"}}>
                        {t.label}
                      </button>
                    ))}
                  </div>
                  <div style={{padding:20}}>
                    {tab==="rootcauses"&&(
                      <div style={{display:"flex",flexDirection:"column",gap:10}}>
                        {(sku.rootCauses||[]).map((rc,j)=>(
                          <div key={j} style={{padding:"14px 16px",background:C.bg,border:`1px solid ${C.border}`,borderRadius:8,display:"flex",gap:14}}>
                            <div style={{flexShrink:0,width:36,height:36,borderRadius:8,background:`${rcConfColor(rc.confidence)}18`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:18}}>{catIcon(rc.category)}</div>
                            <div style={{flex:1,minWidth:0}}>
                              <div style={{display:"flex",gap:8,marginBottom:6,flexWrap:"wrap"}}>
                                <span style={{fontSize:10,padding:"2px 7px",borderRadius:4,background:`${rcConfColor(rc.confidence)}18`,color:rcConfColor(rc.confidence),fontWeight:700,textTransform:"uppercase"}}>{rc.confidence} confidence</span>
                                <span style={{fontSize:10,padding:"2px 7px",borderRadius:4,background:"rgba(96,165,250,0.12)",color:C.blue,fontWeight:700,textTransform:"uppercase"}}>{catLabel(rc.category)}</span>
                              </div>
                              <p style={{fontSize:13,fontWeight:600,color:C.text,margin:"0 0 6px"}}>{rc.cause}</p>
                              <p style={{fontSize:12,color:C.muted,margin:0,lineHeight:1.6,fontStyle:"italic"}}>"{rc.supporting_evidence}"</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {tab==="incidents"&&(
                      <div style={{display:"flex",flexDirection:"column",gap:8}}>
                        {(sku.incidentThemes||[]).map((t,j)=>(
                          <div key={j} style={{padding:"12px 16px",background:C.bg,border:`1px solid ${C.border}`,borderRadius:8}}>
                            <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6,flexWrap:"wrap"}}>
                              <span style={{fontSize:12,fontWeight:600,color:C.text,flex:1}}>{t.theme}</span>
                              <span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:`${sevColor(t.severity)}18`,color:sevColor(t.severity),fontWeight:700,textTransform:"uppercase"}}>{t.severity}</span>
                              <span style={{fontSize:11,color:C.muted,fontFamily:"monospace"}}>{t.frequency}× reported</span>
                            </div>
                            <p style={{fontSize:12,color:C.muted,margin:0,lineHeight:1.6,fontStyle:"italic"}}>"{t.evidence}"</p>
                          </div>
                        ))}
                      </div>
                    )}
                    {tab==="returns"&&(
                      <div style={{display:"flex",flexDirection:"column",gap:8}}>
                        {(sku.returnThemes||[]).length
                          ? (sku.returnThemes||[]).map((t,j)=>(
                            <div key={j} style={{padding:"12px 16px",background:C.bg,border:`1px solid ${C.border}`,borderRadius:8}}>
                              <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6,flexWrap:"wrap"}}>
                                <span style={{fontSize:12,fontWeight:600,color:C.text,flex:1}}>{t.theme}</span>
                                <span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:`${sevColor(t.severity)}18`,color:sevColor(t.severity),fontWeight:700,textTransform:"uppercase"}}>{t.severity}</span>
                                <span style={{fontSize:11,color:C.muted,fontFamily:"monospace"}}>{t.frequency}× reported</span>
                              </div>
                              <p style={{fontSize:12,color:C.muted,margin:0,lineHeight:1.6,fontStyle:"italic"}}>"{t.evidence}"</p>
                            </div>
                          ))
                          : <div style={{fontSize:13,color:C.muted,fontStyle:"italic"}}>No return themes identified for this SKU.</div>
                        }
                      </div>
                    )}
                    {tab==="improvements"&&(
                      <div style={{display:"flex",flexDirection:"column",gap:10}}>
                        {(sku.improvements||[]).map((imp,j)=>(
                          <div key={j} style={{padding:"14px 16px",background:C.bg,border:`1px solid ${C.border}`,borderRadius:8,display:"flex",gap:12}}>
                            <div style={{flexShrink:0,width:28,height:28,borderRadius:"50%",background:"rgba(96,165,250,0.15)",border:`1px solid ${C.blue}`,display:"flex",alignItems:"center",justifyContent:"center",fontSize:12,fontWeight:700,color:C.blue}}>{imp.priority}</div>
                            <div style={{flex:1,minWidth:0}}>
                              <p style={{fontSize:13,fontWeight:600,color:C.text,margin:"0 0 8px",lineHeight:1.5}}>{imp.action}</p>
                              <div style={{display:"flex",gap:8,marginBottom:8}}>
                                <span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:`${effortColor(imp.effort)}18`,color:effortColor(imp.effort),fontWeight:700,textTransform:"uppercase"}}>{imp.effort} effort</span>
                              </div>
                              <p style={{fontSize:12,color:C.muted,margin:0,lineHeight:1.5}}><span style={{color:C.green,fontWeight:600}}>Expected impact: </span>{imp.expected_impact}</p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Run Queue ─────────────────────────────────────────────────────────────────
function RunQueue({onSelect}) {
  const [runs,setRuns]       = useState([]);
  const [loading,setLoading] = useState(true);
  const [error,setError]     = useState(null);
  const load = useCallback(async()=>{ setLoading(true); setError(null); try{ const d=await apiFetch("/api/queue"); setRuns(d.queue||[]); }catch(e){ setError(e.message); }finally{ setLoading(false); } },[]);
  useEffect(()=>{ load(); },[load]);
  if(loading) return <Spinner/>;
  return (
    <div>
      <div style={{marginBottom:24}}><h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Pending Review</h2><p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>{runs.length} report{runs.length!==1?"s":""} awaiting decision</p></div>
      {error&&<ErrMsg message={error} onRetry={load}/>}
      <div style={{display:"grid",gap:12}}>
        {runs.map(run=>(
          <div key={run.runID} onClick={()=>onSelect(run)} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"18px 22px",cursor:"pointer",transition:"all 0.15s",display:"grid",gridTemplateColumns:"1fr auto",gap:16,alignItems:"center"}} onMouseEnter={e=>e.currentTarget.style.background=C.surface} onMouseLeave={e=>e.currentTarget.style.background=C.surface}>
            <div style={{display:"flex",flexDirection:"column",gap:8}}>
              <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
                <span style={{fontSize:13,fontWeight:600,color:C.text}}>{fmt.label(run.reportType)}</span>
                <Badge variant={run.audience}>{run.audience}</Badge>
                {run.supplierID&&<Badge>{run.supplierID}</Badge>}
                <Badge variant="pending">Pending</Badge>
                {run.hallucinationFlags>0&&<Badge variant="rejected">⚠ {run.hallucinationFlags} hallucination</Badge>}
              </div>
              <div style={{display:"flex",alignItems:"center",gap:20,flexWrap:"wrap"}}>
                <ConfMeter value={run.confidence||0}/>
                <span style={{fontSize:12,color:C.muted}}>{run.validationPassed}/{(run.validationPassed||0)+(run.validationFailed||0)} checks</span>
                <span style={{fontSize:12,color:C.muted}}>{run.queuedAt?new Date(run.queuedAt).toLocaleString("en-GB"):""}</span>
              </div>
              {run.softFailures?.length>0&&<div style={{fontSize:12,color:C.muted}}>⚠ {run.softFailures.join(", ")}</div>}
            </div>
            <div style={{color:C.muted,fontSize:13}}>Review →</div>
          </div>
        ))}
        {!error&&runs.length===0&&<div style={{textAlign:"center",padding:60,color:C.muted,fontSize:13}}>No pending reports.</div>}
      </div>
    </div>
  );
}

// ── Audit View ────────────────────────────────────────────────────────────────
function AuditView({runSummary,onDecision,onBack,isDemo=false}) {
  const [run,setRun]           = useState(null);
  const [loading,setLoading]   = useState(true);
  const [tab,setTab]           = useState("report");
  const [decision,setDecision] = useState(null);
  const [reason,setReason]     = useState("");
  const [reviewer,setReviewer] = useState("f.trindade");
  const [editedNarrative,setEditedNarrative] = useState("");
  const [shareWithSupplier,setShareWithSupplier] = useState(false);
  const [submitting,setSubmitting] = useState(false);
  const [submitted,setSubmitted]   = useState(false);
  const [error,setError]       = useState(null);

  useEffect(()=>{ apiFetch(`/api/runs/${runSummary.runID}`).then(d=>{ setRun(d); setEditedNarrative(d.reportNarrative||""); setLoading(false); }).catch(e=>{ setError(e.message); setLoading(false); }); },[runSummary.runID]);

  const [rerunID,setRerunID]         = useState(null);
  const [rerunStatus,setRerunStatus] = useState(null);
  const [rerunStart,setRerunStart]   = useState(null);
  const [rerunError,setRerunError]   = useState(null);
  const rerunPollRef                 = useRef(null);

  const handleRerun = async() => {
    setRerunError(null);
    try {
      const res = await apiFetch(`/api/runs/rerun/${run.runID}`,{method:"POST"});
      setRerunID(res.rerunID);
      setRerunStatus("running");
      setRerunStart(Date.now());
      rerunPollRef.current = setInterval(async()=>{
        try {
          const s = await apiFetch(`/api/runs/${res.rerunID}/status`);
          setRerunStatus(s.status);
          if(s.status&&s.status!=="running"&&s.status!=="starting") {
            clearInterval(rerunPollRef.current);
          }
        } catch(e){}
      }, 3000);
    } catch(e){ setRerunError(e.message); }
  };

  const handleSubmit = async() => {
    if(!decision||(decision==="rejected"&&!reason.trim())) return;
    setSubmitting(true);
    try {
      await apiFetch("/api/decisions",{method:"POST",body:JSON.stringify({runID:run.runID,decision,reviewer,reason:reason.trim()||null,editedNarrative:decision==="edited_and_approved"?editedNarrative:null,shareWithSupplier})});
      setSubmitted(true); onDecision({runID:run.runID,decision});
    } catch(e){ setError(e.message); } finally{ setSubmitting(false); }
  };

  if(loading) return <Spinner/>;
  if(submitted) return (
    <div style={{padding:"40px 32px"}}>
      <div style={{textAlign:"center",marginBottom:24}}>
        <div style={{fontSize:48,marginBottom:12}}>{decision==="rejected"?"✗":"✓"}</div>
      </div>
      {decision==="rejected"&&(
        <div style={{marginBottom:24}}>
          {!rerunID&&(
            <div style={{textAlign:"center"}}>
              <div style={{fontSize:13,color:C.muted,marginBottom:12}}>
                Want the agent to re-run with your correction applied?
              </div>
              {rerunError&&<div style={{color:C.red,fontSize:12,marginBottom:8}}>{rerunError}</div>}
              <button onClick={handleRerun}
                style={{background:"rgba(96,165,250,0.15)",border:"1px solid rgba(96,165,250,0.4)",
                  borderRadius:8,padding:"10px 24px",color:C.blue,fontSize:13,fontWeight:600,cursor:"pointer"}}>
                ↻ Re-run with correction
              </button>
            </div>
          )}
          {rerunID&&(
            <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"16px 20px"}}>
              <div style={{fontSize:12,color:C.muted,marginBottom:8,textTransform:"uppercase",letterSpacing:"0.06em"}}>
                Correction Re-run
              </div>
              {(rerunStatus==="running"||rerunStatus==="starting")&&(
                <PipelineProgress startTime={rerunStart} status={rerunStatus}/>
              )}
              {rerunStatus&&rerunStatus!=="running"&&rerunStatus!=="starting"&&(
                <div style={{display:"flex",alignItems:"center",gap:8,fontSize:13}}>
                  <span style={{color:rerunStatus==="pending_review"?C.amber:rerunStatus==="failed"?C.red:C.green,fontSize:16}}>
                    {rerunStatus==="pending_review"?"⏳":rerunStatus==="failed"?"✗":"✓"}
                  </span>
                  <span style={{color:C.text}}>
                    {rerunStatus==="pending_review"?"Re-run complete — new report is in the queue":
                     rerunStatus==="failed"?"Re-run failed — check the queue for details":
                     "Re-run complete"}
                  </span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      <div style={{textAlign:"center"}}>
        <div style={{fontSize:20,fontWeight:700,color:C.text,marginBottom:8}}>{decision==="approved"?"Approved":decision==="edited_and_approved"?"Edited & Approved":"Rejected"}{shareWithSupplier?" · Shared with supplier":""}</div>
        <button onClick={onBack} style={{background:C.surface,border:"none",color:C.muted,borderRadius:7,padding:"10px 24px",cursor:"pointer",fontSize:13,fontWeight:600,marginTop:24}}>← Back to Queue</button>
      </div>
    </div>
  );

  const tabs=[{id:"report",label:"Report"},{id:"validation",label:`Validation (${run?.validationPassed||0}/${(run?.validationPassed||0)+(run?.validationFailed||0)})`},{id:"policy",label:"Policy"},{id:"data",label:"Data"}];
  const policyRules=run?.policyOutcome?.rule_results||[];

  return (
    <div>
      <div style={{display:"flex",alignItems:"center",gap:16,marginBottom:24,flexWrap:"wrap"}}>
        <button onClick={onBack} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:13,padding:0}}>← Queue</button>
        <div style={{width:1,height:16,background:C.border}}/>
        <span style={{fontSize:16,fontWeight:700,color:C.text}}>{fmt.label(run?.reportType)}</span>
        <Badge variant={run?.audience}>{run?.audience}</Badge>
        {run?.supplierID&&<Badge>{run.supplierID}</Badge>}
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:10}}><ConfMeter value={run?.confidence||0}/></div>
      </div>
      {error&&<ErrMsg message={error}/>}
      <div style={{display:"grid",gridTemplateColumns:"1fr 340px",gap:20}}>
        <div>
          <div style={{display:"flex",gap:2,borderBottom:`1px solid ${C.border}`}}>
            {tabs.map(t=>(<button key={t.id} onClick={()=>setTab(t.id)} style={{background:"none",border:"none",borderBottom:tab===t.id?`2px solid ${C.blue}`:"2px solid transparent",color:tab===t.id?C.blue:C.muted,padding:"10px 16px",cursor:"pointer",fontSize:13,fontWeight:tab===t.id?600:400}}>{t.label}</button>))}
          </div>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderTop:"none",borderRadius:"0 0 10px 10px",padding:20,overflowY:"auto",maxHeight:560}}>
            {tab==="report"&&(
              <div>
                {run?.goal && (
                  <div style={{marginBottom:16,padding:"10px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:7}}>
                    <div style={{fontSize:10,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:4}}>Report Prompt</div>
                    <div style={{fontSize:13,color:C.text,lineHeight:1.5}}>{run.goal}</div>
                  </div>
                )}
                {decision==="edited_and_approved"
                  ?<textarea value={editedNarrative} onChange={e=>setEditedNarrative(e.target.value)} style={{width:"100%",minHeight:480,background:C.surface,border:`1px solid rgba(96,165,250,0.3)`,borderRadius:8,padding:16,color:C.text,fontSize:13,lineHeight:1.7,fontFamily:"monospace",resize:"vertical",boxSizing:"border-box"}}/>
                  :<div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif"}}>{run?.reportNarrative||"No narrative."}</div>
                }
              </div>
            )}
            {tab==="validation"&&(
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:10,marginBottom:16}}>
                  <Scorecard label="Passed" value={run?.validationPassed||0}/><Scorecard label="Failed" value={run?.validationFailed||0}/><Scorecard label="Hallucinations" value={run?.hallucinationFlags||0}/><Scorecard label="Pass Rate" value={fmt.pct((run?.validationPassed||0)/Math.max((run?.validationPassed||0)+(run?.validationFailed||0),1)*100)}/>
                </div>
                {(run?.validationResults||[]).map((r,i)=>(
                  <div key={i} style={{display:"flex",alignItems:"flex-start",gap:10,padding:"10px 14px",background:r.passed?"rgba(34,197,94,0.05)":"rgba(239,68,68,0.05)",border:`1px solid ${r.passed?"rgba(34,197,94,0.15)":"rgba(239,68,68,0.15)"}`,borderRadius:6}}>
                    <span style={{color:r.passed?C.green:C.red,fontSize:13}}>{r.passed?"✓":"✗"}</span>
                    <div style={{flex:1}}>
                      <div style={{fontSize:12,fontWeight:600,color:C.text,fontFamily:"monospace"}}>{r.metricName}</div>
                      {r.expectedValue!=null&&<div style={{fontSize:11,color:C.muted,marginTop:2}}>Expected: <span style={{color:C.muted}}>{(+r.expectedValue).toLocaleString()}</span> · Reported: <span style={{color:C.muted}}>{(+r.reportedValue).toLocaleString()}</span>{r.deviationPct!=null&&<> · Dev: <span style={{color:r.deviationPct>10?C.red:C.green}}>{(+r.deviationPct).toFixed(1)}%</span></>}</div>}
                      {r.details&&<div style={{fontSize:11,color:C.muted,marginTop:2}}>{r.details}</div>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {tab==="policy"&&(
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:16,padding:"12px 16px",background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:8}}>
                  <span style={{color:C.amber}}>⚡</span>
                  <div><div style={{fontSize:13,fontWeight:600,color:C.amber}}>Decision: {(run?.policyOutcome?.decision||"").toUpperCase().replace(/_/g," ")}</div><div style={{fontSize:12,color:C.muted,marginTop:2}}>{run?.policyOutcome?.rules_passed}/{run?.policyOutcome?.rules_evaluated} rules passed</div></div>
                </div>
                {policyRules.map((r,i)=>(
                  <div key={i} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:6}}>
                    <span style={{color:r.passed?C.green:C.amber}}>{r.passed?"✓":"✗"}</span>
                    <div style={{flex:1}}><span style={{fontSize:12,fontWeight:600,color:C.text,fontFamily:"monospace"}}>{r.rule}</span>{!r.passed&&r.message&&<div style={{fontSize:11,color:C.muted,marginTop:2}}>{r.message}</div>}</div>
                    <div style={{fontSize:11,color:C.muted,fontFamily:"monospace"}}>{r.actual} {r.threshold&&`/ ${r.threshold}`}</div>
                    <Badge variant={r.passed?"pass":"fail"}>{r.passed?"pass":"fail"}</Badge>
                  </div>
                ))}
              </div>
            )}
            {tab==="data"&&(
              <div style={{display:"flex",flexDirection:"column",gap:16}}>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:10}}>
                  {Object.entries(run?.rowCounts||{}).map(([t,c])=><Scorecard key={t} label={t} value={(+c).toLocaleString()} sub="rows"/>)}
                </div>
                {Object.entries(run?.queries||{}).map(([t,sql])=>(<div key={t}><div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:6}}>{t}</div><pre style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:12,fontSize:11,color:C.muted,overflow:"auto",whiteSpace:"pre-wrap",margin:0,fontFamily:"monospace"}}>{typeof sql==="string"?sql:JSON.stringify(sql,null,2)}</pre></div>))}
                {(run?.flags||[]).map((f,i)=><div key={i} style={{fontSize:12,color:C.amber,padding:"6px 0",borderBottom:`1px solid rgba(255,255,255,0.04)`}}>⚠ {f}</div>)}
              </div>
            )}
          </div>
        </div>

        <div style={{display:"flex",flexDirection:"column",gap:12}}>
          <div style={{fontSize:13,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:"0.06em"}}>Decision</div>
          {!isDemo && [{id:"approved",label:"Approve",desc:"Publish as generated",color:C.green},{id:"edited_and_approved",label:"Edit & Approve",desc:"Modify then publish",color:C.blue},{id:"rejected",label:"Reject",desc:"Send back — reason required",color:C.red}].map(opt=>(
            <div key={opt.id} onClick={()=>setDecision(opt.id)} style={{padding:"14px 16px",border:`1px solid ${decision===opt.id?opt.color:C.border}`,borderRadius:8,cursor:"pointer",background:decision===opt.id?`${opt.color}18`:C.surface,transition:"all 0.15s"}}>
              <div style={{fontSize:13,fontWeight:600,color:decision===opt.id?opt.color:C.text}}>{opt.label}</div>
              <div style={{fontSize:11,color:C.muted,marginTop:2}}>{opt.desc}</div>
            </div>
          ))}
          {run?.supplierID&&decision&&decision!=="rejected"&&(
            <div style={{padding:"12px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:8}}>
              <label style={{display:"flex",alignItems:"center",gap:10,cursor:"pointer"}}>
                <input type="checkbox" checked={shareWithSupplier} onChange={e=>setShareWithSupplier(e.target.checked)} style={{width:16,height:16,accentColor:C.teal}} />
                <div>
                  <div style={{fontSize:13,fontWeight:600,color:shareWithSupplier?C.teal:C.text}}>Share with supplier</div>
                  <div style={{fontSize:11,color:C.muted,marginTop:2}}>Appears in /supplier/{run.supplierID} view</div>
                </div>
              </label>
            </div>
          )}
          <div><label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Reviewer</label><input value={reviewer} onChange={e=>setReviewer(e.target.value)} style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:"8px 10px",color:C.text,fontSize:12,fontFamily:"inherit",boxSizing:"border-box"}}/></div>
          {!isDemo && (decision==="rejected"||decision==="edited_and_approved")&&(
            <div><label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>{decision==="rejected"?"Reason *":"Notes"}</label><textarea value={reason} onChange={e=>setReason(e.target.value)} rows={3} style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,borderRadius:6,padding:10,color:C.text,fontSize:12,resize:"vertical",fontFamily:"inherit",boxSizing:"border-box"}}/></div>
          )}
          {!isDemo && <button onClick={handleSubmit} disabled={submitting||!decision||(decision==="rejected"&&!reason.trim())} style={{padding:"10px 18px",borderRadius:7,border:`1px solid ${decision==="approved"?C.green:decision==="edited_and_approved"?C.blue:decision==="rejected"?C.red:C.border}`,fontSize:13,fontWeight:600,cursor:"pointer",background:decision==="approved"?"rgba(34,197,94,0.2)":decision==="edited_and_approved"?"rgba(96,165,250,0.2)":decision==="rejected"?"rgba(239,68,68,0.2)":C.surface,color:decision==="approved"?C.green:decision==="edited_and_approved"?C.blue:decision==="rejected"?C.red:C.muted,opacity:(submitting||!decision||(decision==="rejected"&&!reason.trim()))?0.4:1,marginTop:8}}>
            {submitting?"Recording...":"Confirm Decision"}
          </button>}
        </div>
      </div>
    </div>
  );
}

// ── Observability ─────────────────────────────────────────────────────────────
function Observability() {
  const [history,setHistory] = useState([]);
  const [loading,setLoading] = useState(true);
  const [error,setError]     = useState(null);
  const load = useCallback(async()=>{ setLoading(true); setError(null); try{ const d=await apiFetch("/api/history"); setHistory(d.history||[]); }catch(e){ setError(e.message); }finally{ setLoading(false); } },[]);
  useEffect(()=>{ load(); },[load]);
  if(loading) return <Spinner/>;
  const approved=history.filter(r=>["approved","edited_and_approved","auto_approved"].includes(r.decision)).length;
  const rejected=history.filter(r=>r.decision==="rejected").length;
  const autoApp =history.filter(r=>r.decision==="auto_approved").length;
  const avgConf =history.length?history.reduce((s,r)=>s+(r.confidence||0),0)/history.length:0;
  return (
    <div>
      <div style={{marginBottom:24}}><h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Observability</h2><p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>Run history and system health</p></div>
      {error&&<ErrMsg message={error} onRetry={load}/>}
      <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:12,marginBottom:24}}>
        <Scorecard label="Total Runs" value={history.length}/><Scorecard label="Approved" value={approved} sub={`${autoApp} auto`}/><Scorecard label="Rejected" value={rejected}/><Scorecard label="Avg Confidence" value={fmt.pct(avgConf*100)}/>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {history.map(run=>(
          <div key={run.runID} style={{display:"grid",gridTemplateColumns:"1fr auto auto auto",gap:16,alignItems:"center",padding:"12px 16px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:8}}>
            <div><div style={{display:"flex",gap:8,alignItems:"center"}}><span style={{fontSize:13,fontWeight:600,color:C.text}}>{fmt.label(run.reportType)}</span>{run.supplierID&&<Badge>{run.supplierID}</Badge>}</div><div style={{fontSize:11,color:C.muted,marginTop:3}}>{run.decidedAt?new Date(run.decidedAt).toLocaleDateString("en-GB"):new Date(run.startedAt).toLocaleDateString("en-GB")} · {run.reviewer||"system"}</div></div>
            <ConfMeter value={run.confidence||0}/>
            <Badge variant={run.decision==="rejected"?"rejected":"approved"}>{run.decision==="auto_approved"?"Auto":run.decision==="edited_and_approved"?"Edited":run.decision||run.status}</Badge>
            <span style={{fontSize:11,fontFamily:"monospace",color:C.muted}}>{(run.runID||"").slice(0,8)}</span>
          </div>
        ))}
        {!error&&history.length===0&&<div style={{textAlign:"center",padding:60,color:C.muted,fontSize:13}}>No run history yet.</div>}
      </div>
    </div>
  );
}

// ── Login Page ────────────────────────────────────────────────────────────────
// ── Landing Page ──────────────────────────────────────────────────────────────
function LandingPage({ onSignIn, onDemoLogin }) {
  const [demoKey, setDemoKey] = useState(null);
  const [demoReport, setDemoReport] = useState(null);

  const STEPS = [
    { icon:"🎯", title:"You ask a question", color:"#2563eb",
      desc:"Type a plain-English question, no SQL or technical knowledge needed. For example: Show me SUP004 sales in 2026 vs the same period last year." },
    { icon:"🤖", title:"Six AI agents take over", color:"#2563eb",
      desc:"Six specialised agents each handle one job in sequence. Discover picks the right data tables, Pull writes and runs the database query, Analyse scores the findings, Generate writes the report, Validate fact-checks it, and Review applies policy rules." },
    { icon:"⚙️", title:"SQL is written and self-corrected", color:"#2563eb",
      desc:"The Pull agent writes precise SQL for BigQuery. If the query fails, it reads the exact error, corrects the SQL automatically, and retries up to 2 times before escalating." },
    { icon:"📊", title:"Three ways a report reaches you", color:"#2563eb",
      desc:"Scheduled reports auto-publish if confidence is high. Ad-hoc reports with high confidence skip the queue. Low-confidence or complex reports route to a human reviewer first." },
    { icon:"🧠", title:"Customer Voice, beyond the numbers", color:"#2563eb",
      desc:"A separate agent reads customer comments and groups them into themes: what went wrong, why items were returned, what customers asked for. This feeds directly into supplier account reports." },
    { icon:"👤", title:"Human-in-the-loop review", color:"#2563eb",
      desc:"Every report that needs review goes to a queue. The reviewer can approve, edit, or reject with a reason. If rejected, the agent re-runs with the correction applied automatically." },
  ];

  const TECH = [
    { name:"LangGraph",     color:"#2563eb", role:"Agent pipeline orchestration",
      desc:"Manages the six-agent pipeline as a stateful graph. Each node passes context to the next and handles retries, state persistence, and branching logic." },
    { name:"Claude Sonnet 4", color:"#2563eb", role:"AI reasoning engine",
      desc:"Anthropic's Claude powers every agent node that requires reasoning: writing SQL, interpreting data, scoring confidence, generating report narratives, and self-correcting errors." },
    { name:"BigQuery",      color:"#2563eb", role:"Data warehouse",
      desc:"Google's cloud database holds all order, incident, return, and supplier data. The Pull agent generates and executes SQL directly against BigQuery." },
    { name:"FastAPI",       color:"#2563eb", role:"Backend API",
      desc:"Python API connecting the agent pipeline, BigQuery, Firebase, and the React frontend. Handles auth, human decisions, report delivery, and the rejection feedback loop." },
    { name:"Firebase Auth", color:"#2563eb", role:"Authentication and roles",
      desc:"Manages login and role-based access. Admin, business, demo, and supplier roles each see a different view, enforced in both the UI and every API endpoint." },
    { name:"Cloud Run",     color:"#2563eb", role:"Serverless deployment",
      desc:"The entire system runs as a containerised service on Google Cloud. Scales automatically with zero infrastructure management." },
  ];

  const DEMOS = [
    { label:"Weekly dashboard", icon:"📈" },
    { label:"Sales comparison", icon:"📊" },
    { label:"Customer Voice",   icon:"💬" },
    { label:"Spike alert",      icon:"🚨" },
  ];

  const OUTPUTS = {
    "Sales comparison": {
      title:"SUP004: Sales and Quality, Jan to Apr 2026 vs Jan to Apr 2025",
      confidence:91, autoPublish:true,
      summary:"CoreTech Industries (SUP004) delivered 4.47M euros gross revenue across 19,354 orders in Jan to Apr 2026, up 6.2% versus 4.21M euros across 18,102 orders in the same period in 2025. Revenue growth is entirely volume-driven. Average order value held flat at 230 euros. The incident rate has deteriorated from 9.97% to 10.31% annualised.",
      metrics:[
        { label:"2026 Revenue (YTD)", value:"€4.47M",  change:"+6.2% vs 2025",    up:true  },
        { label:"2026 Orders (YTD)", value:"19,354",   change:"+6.9% vs 2025",    up:true  },
        { label:"Incident Rate 2026", value:"10.31%",  change:"+0.34pp vs 2025",  up:false },
        { label:"Avg Order Value",    value:"€230.73", change:"Flat vs 2025",      up:null  },
      ],
      sections:[
        { title:"Revenue trend", body:"Month-by-month comparison shows consistent volume growth in Jan and Feb (+8.1%, +7.4%), slowing in Mar (+4.2%) and Apr (+5.3%). Growth is broad-based with no single month distorting the picture." },
        { title:"Quality deterioration", body:"The incident rate increase represents an additional 65 incidents at current order volumes. At an average resolution cost of 18.40 euros per incident, this is an estimated 1,196 euros additional annual cost if the trend continues." },
        { title:"Recommendation", body:"Trigger a quality review with CoreTech Industries focused on the incident rate increase. Revenue growth is positive but does not offset quality deterioration at this trajectory." },
      ],
      finding:"Revenue growth is real but quality is heading in the wrong direction. Both need to be on the agenda in the next supplier review.",
    },
    "Customer Voice": {
      title:"Customer Voice Intelligence: SUP004 Sports and Outdoors",
      confidence:95, autoPublish:true,
      summary:"Analysis of 847 customer comments for CoreTech Industries (SUP004) reveals three structural product issues driving returns and incidents. Sizing and description accuracy account for 61% of all negative comment themes. Two SKUs, SPT-0041 and SPT-0087, are responsible for 34% of all flagged comments despite representing only 8% of order volume.",
      metrics:[
        { label:"Comments analysed",    value:"847",     change:"Last 90 days",         up:null  },
        { label:"Flagged SKUs",          value:"6 of 43", change:"Require action",       up:false },
        { label:"Top issue",             value:"Sizing",  change:"38% of complaints",    up:false },
        { label:"Avg rating (flagged)",  value:"2.4 / 5", change:"vs 3.9 portfolio avg", up:false },
      ],
      sections:[
        { title:"Root cause 1: Sizing inaccuracy (38%)", body:"SPT-0041 (hiking boots) and SPT-0087 (compression shorts) generate the majority of sizing complaints. Customers consistently report products running 1 to 1.5 sizes small. 73% of returns for these SKUs cite sizing as the reason." },
        { title:"Root cause 2: Description mismatch (23%)", body:"Product descriptions for 3 outdoor furniture SKUs do not reflect actual dimensions. Customers report items arriving significantly smaller than listed. This is a listing accuracy issue, not a manufacturing defect." },
        { title:"Improvement actions", body:"1. Add size guide to SPT-0041 and SPT-0087 listings immediately. 2. Audit outdoor furniture dimension listings against physical specs. 3. Request CoreTech to update product photography for the 3 flagged SKUs." },
      ],
      finding:"Two SKUs are disproportionately damaging customer satisfaction. Fixing their size guidance alone is estimated to reduce the return rate from 14.2% to under 6%.",
    },
    "Spike alert": {
      title:"Incident Rate Spike: Electronics Portfolio",
      confidence:88, autoPublish:false,
      summary:"An automated spike alert: the Electronics category incident rate reached 14.7% in the last 7 days, up from a 30-day average of 11.2%, a 3.5 percentage point increase. Concentrated in the budget tier across the supplier direct channel. Two suppliers account for 81% of the spike volume.",
      metrics:[
        { label:"Current rate (7-day)", value:"14.7%",          change:"+3.5pp vs 30-day avg", up:false },
        { label:"Affected orders",      value:"1,847",           change:"In spike window",      up:null  },
        { label:"Primary channel",      value:"supplier direct", change:"Budget tier",          up:false },
        { label:"Spike severity",       value:"High",            change:"Auto-escalated",       up:false },
      ],
      sections:[
        { title:"Affected suppliers", body:"SUP002 (Horizon Global Goods) accounts for 54% of spike incidents, 998 incidents on 6,790 orders. SUP004 (CoreTech Industries) accounts for 27%, 499 incidents on 3,441 orders. Both are in the supplier direct budget channel." },
        { title:"Incident type breakdown", body:"Damage and defect incidents increased 89% week-on-week. Missing parts increased 34%. Late delivery is flat, pointing to a packaging or handling issue at dispatch rather than a logistics problem." },
        { title:"Recommended actions", body:"1. Contact SUP002 and SUP004 for immediate root cause explanation. 2. Temporarily pause new supplier direct budget Electronics orders. 3. Review last 48 hours of dispatch records for both suppliers." },
      ],
      finding:"This spike has the pattern of a packaging change or a new batch of defective product entering the supply chain. It needs a same-day response.",
    },
  };

  const handleDemo = (label) => {
    if (demoKey === label) return;
    setDemoKey(label);
    setDemoReport(OUTPUTS[label] || null);
  };

  const LI = "https://www.linkedin.com/in/franciscotrindade";
  const S = { // shared text styles
    body:   { fontSize:13, color:"#475569", lineHeight:1.7 },
    label:  { fontSize:11, color:"#94a3b8", textTransform:"uppercase", letterSpacing:"0.08em" },
    title:  { fontSize:13, fontWeight:700, color:"#0f172a" },
    card:   { background:"#f8fafc", borderRadius:12, border:"1px solid #e2e8f0", padding:"20px 24px" },
  };

  return (
    <div style={{minHeight:"100vh", background:"#f8fafc", fontFamily:"'DM Sans','Helvetica Neue',sans-serif", color:"#0f172a"}}>

      <nav style={{position:"sticky",top:0,zIndex:100,background:"rgba(248,250,252,0.95)",backdropFilter:"blur(12px)",borderBottom:"1px solid #e2e8f0",padding:"0 32px",display:"flex",alignItems:"center",height:56}}>
        <div style={{fontSize:13,fontWeight:700,letterSpacing:"-0.02em"}}>Agentic <span style={{color:"#2563eb"}}>Intel</span></div>
        <div style={{marginLeft:"auto",display:"flex",gap:24,alignItems:"center"}}>
          <a href="#how-it-works" style={{fontSize:13,color:"#64748b",textDecoration:"none"}}>How it works</a>
          <a href="#tech" style={{fontSize:13,color:"#64748b",textDecoration:"none"}}>Tech stack</a>
          <a href="#demo" style={{fontSize:13,color:"#64748b",textDecoration:"none"}}>See outputs</a>
          <a href={LI} target="_blank" rel="noreferrer" style={{background:"#0a66c2",color:"#fff",borderRadius:7,padding:"7px 16px",fontSize:13,fontWeight:600,textDecoration:"none",display:"inline-flex",alignItems:"center",gap:6}}>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
            LinkedIn
          </a>
          <button onClick={onSignIn} style={{background:"#fff",color:"#0f172a",border:"1px solid #e2e8f0",borderRadius:7,padding:"7px 18px",fontSize:13,fontWeight:600,cursor:"pointer"}}>Login</button>
        </div>
      </nav>

      <section style={{maxWidth:1100,margin:"0 auto",padding:"80px 32px 60px",display:"grid",gridTemplateColumns:"1fr 1fr",gap:64,alignItems:"center"}}>
        <div>
          <div style={{display:"inline-flex",alignItems:"center",gap:8,background:"#eff6ff",border:"1px solid #bfdbfe",borderRadius:20,padding:"4px 14px",fontSize:12,color:"#2563eb",fontWeight:600,marginBottom:24}}>
            Multi-agent AI system, built end-to-end
          </div>
          <h1 style={{fontSize:44,fontWeight:800,lineHeight:1.1,letterSpacing:"-0.03em",margin:"0 0 24px",color:"#0f172a"}}>
            Six AI agents.<br/><span style={{color:"#2563eb"}}>One intelligence</span><br/>platform.
          </h1>
          <p style={{...S.body, margin:"0 0 12px", maxWidth:480}}>
            A portfolio project demonstrating what is possible with modern AI agent frameworks. A fully working supplier intelligence platform, from data warehouse to agent pipeline to human review queue to supplier portal.
          </p>
          <p style={{...S.body, margin:"0 0 12px", maxWidth:480}}>
            Ask a question in plain English. Six AI agents query the data, analyse the results, write a structured report, and route it for human review, all automatically.
          </p>
          <p style={{...S.body, margin:"0 0 32px", maxWidth:480, color:"#2563eb", fontWeight:500}}>
            If you are looking for someone that brings business, operations and AI knowledge, let's connect.
          </p>
          <div style={{display:"flex",gap:12,flexWrap:"wrap"}}>
            <a href={LI} target="_blank" rel="noreferrer" style={{background:"#0a66c2",color:"#fff",borderRadius:8,padding:"12px 24px",fontSize:13,fontWeight:600,textDecoration:"none",display:"inline-flex",alignItems:"center",gap:8}}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
              Get in touch
            </a>
            <button onClick={onDemoLogin} style={{background:"#fff",color:"#0f172a",border:"1px solid #e2e8f0",borderRadius:8,padding:"12px 24px",fontSize:13,fontWeight:600,cursor:"pointer"}}>
              Explore the live system
            </button>
          </div>
        </div>
        <div style={{background:"#fff",borderRadius:16,border:"1px solid #e2e8f0",boxShadow:"0 4px 24px rgba(0,0,0,0.06)",padding:28}}>
          <div style={{...S.label, marginBottom:16}}>Live agent run, 6 specialised agents</div>
          {[
            {label:"Discover", desc:"Selects data tables",               done:true,  pending:false},
            {label:"Pull",     desc:"Writes and runs SQL query",         done:true,  pending:false},
            {label:"Analyse",  desc:"Scores findings, 91% confidence",  done:true,  pending:false},
            {label:"Generate", desc:"Writes report narrative",           done:true,  pending:false},
            {label:"Validate", desc:"Fact-checks against source data",   done:true,  pending:false},
            {label:"Review",   desc:"Policy check, routing to queue",    done:false, pending:true },
          ].map((s,i)=>(
            <div key={s.label} style={{display:"flex",alignItems:"center",gap:12,padding:"8px 0",borderBottom:i<5?"1px solid #f1f5f9":"none"}}>
              <div style={{width:28,height:28,borderRadius:"50%",background:s.pending?"#fffbeb":s.done?"#f0fdf4":"#f8fafc",border:"1px solid "+(s.pending?"#fef08a":s.done?"#bbf7d0":"#e2e8f0"),display:"flex",alignItems:"center",justifyContent:"center",fontSize:11,fontWeight:700,color:s.pending?"#ca8a04":s.done?"#16a34a":"#94a3b8"}}>
                {s.done&&!s.pending?"✓":i+1}
              </div>
              <div style={{flex:1}}>
                <div style={{fontSize:13,fontWeight:600,color:"#0f172a"}}>{s.label}</div>
                <div style={{fontSize:12,color:"#94a3b8"}}>{s.desc}</div>
              </div>
              <div style={{fontSize:11,fontWeight:600,color:s.pending?"#ca8a04":s.done?"#16a34a":"#94a3b8"}}>{s.pending?"pending":s.done?"done":"."}</div>
            </div>
          ))}
          <div style={{marginTop:16,padding:"10px 14px",background:"#fffbeb",borderRadius:8,border:"1px solid #fef08a",fontSize:13,color:"#92400e"}}>
            Awaiting human review before publishing.
          </div>
        </div>
      </section>

      <section id="how-it-works" style={{background:"#fff",borderTop:"1px solid #e2e8f0",borderBottom:"1px solid #e2e8f0",padding:"80px 32px"}}>
        <div style={{maxWidth:1100,margin:"0 auto"}}>
          <div style={{textAlign:"center",marginBottom:48}}>
            <h2 style={{fontSize:30,fontWeight:800,letterSpacing:"-0.02em",margin:"0 0 12px",color:"#0f172a"}}>How it works</h2>
            <p style={{...S.body, maxWidth:520, margin:"0 auto"}}>Six specialised agents. Three ways a report reaches you. One human decision gate.</p>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16,marginBottom:40,padding:"20px 24px",background:"#f8fafc",borderRadius:12,border:"1px solid #e2e8f0"}}>
            {[
              {icon:"📅",title:"Scheduled reports",        desc:"Weekly and monthly reports run automatically. High-confidence results publish without any human action."},
              {icon:"💬",title:"Ad-hoc questions/reports", desc:"Type any question. High-confidence results go straight through. Lower confidence routes to the human review queue."},
              {icon:"🚨",title:"Spike alerts",             desc:"The system monitors key metrics. When an incident or return rate spikes beyond thresholds, an alert report is generated automatically."},
            ].map(t=>(
              <div key={t.title} style={{display:"flex",gap:12,alignItems:"flex-start"}}>
                <span style={{fontSize:20,marginTop:2}}>{t.icon}</span>
                <div>
                  <div style={{...S.title, marginBottom:4}}>{t.title}</div>
                  <div style={{...S.body}}>{t.desc}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16}}>
            {STEPS.map((s,i)=>(
              <div key={i} style={{background:"#f8fafc",border:"1px solid #e2e8f0",borderRadius:12,padding:"20px"}}>
                <div style={{fontSize:24,marginBottom:10}}>{s.icon}</div>
                <div style={{...S.label, marginBottom:6}}>Step {i+1}</div>
                <div style={{...S.title, marginBottom:8}}>{s.title}</div>
                <div style={{...S.body}}>{s.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="demo" style={{padding:"80px 32px",background:"#f8fafc"}}>
        <div style={{maxWidth:960,margin:"0 auto"}}>
          <div style={{textAlign:"center",marginBottom:48}}>
            <h2 style={{fontSize:30,fontWeight:800,letterSpacing:"-0.02em",margin:"0 0 12px",color:"#0f172a"}}>Sample outputs</h2>
            <p style={{...S.body, maxWidth:480, margin:"0 auto"}}>Four different types of intelligence the system produces. Real structure from the live system.</p>
          </div>
          <div style={{display:"flex",gap:10,justifyContent:"center",marginBottom:28}}>
            {DEMOS.map(p=>(
              <button key={p.label} onClick={()=>handleDemo(p.label)} style={{background:demoKey===p.label?"#eff6ff":"#fff",border:"1px solid "+(demoKey===p.label?"#bfdbfe":"#e2e8f0"),borderRadius:8,padding:"10px 18px",fontSize:13,fontWeight:600,cursor:"pointer",color:demoKey===p.label?"#2563eb":"#475569",display:"flex",alignItems:"center",gap:7,transition:"all 0.15s"}}>
                {p.icon} {p.label}
              </button>
            ))}
          </div>

          {demoKey==="Weekly dashboard" && (
            <div style={{background:"#fff",borderRadius:16,border:"1px solid #e2e8f0",boxShadow:"0 4px 24px rgba(0,0,0,0.06)",overflow:"hidden"}}>
              <div style={{padding:"16px 24px",borderBottom:"1px solid #f1f5f9",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                <div style={{...S.title}}>Business Overview, Weekly Dashboard</div>
                <div style={{...S.label}}>Auto-published, Mon 28 Apr 2026</div>
              </div>
              <div style={{padding:"16px 24px",background:"#fff7ed",borderBottom:"1px solid #fed7aa",display:"flex",gap:12,alignItems:"flex-start"}}>
                <span style={{fontSize:16,marginTop:2}}>🚨</span>
                <div>
                  <div style={{fontSize:13,fontWeight:700,color:"#9a3412",marginBottom:4}}>Weekly digest: 1 critical alert, 2 watch items</div>
                  <div style={{fontSize:13,color:"#92400e",lineHeight:1.6}}>Electronics incident rate spiked 89% week-on-week in the budget supplier direct channel. SUP002 and SUP004 are the primary drivers. Temporary hold on new orders recommended pending root cause confirmation. Home and Garden returns are elevated at 8.3%, return-cause capture has been requested from the supplier.</div>
                </div>
              </div>
              <div style={{padding:"16px 24px",display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:12,borderBottom:"1px solid #f1f5f9"}}>
                {[
                  {label:"Total Orders",    value:"21,839", sub:"+4.2% vs last week",  good:true },
                  {label:"Gross Revenue",   value:"€4.97M", sub:"+3.8% vs last week",  good:true },
                  {label:"Incident Rate",   value:"11.02%", sub:"+0.4pp vs last week", good:false},
                  {label:"Return Rate",     value:"6.43%",  sub:"+0.1pp vs last week", good:false},
                  {label:"Resolution Cost", value:"€38.4K", sub:"2.2% of revenue",     good:null },
                  {label:"Active Alerts",   value:"3",      sub:"1 critical",           good:false},
                  {label:"Suppliers OK",    value:"5 of 7", sub:"2 require action",    good:null },
                  {label:"Published",       value:"Auto",   sub:"93% confidence",       good:true },
                ].map((m,i)=>(
                  <div key={i} style={{background:"#f8fafc",borderRadius:8,padding:"12px 14px"}}>
                    <div style={{...S.label, marginBottom:4}}>{m.label}</div>
                    <div style={{fontSize:17,fontWeight:800,color:m.good===null?"#0f172a":m.good?"#16a34a":"#dc2626",marginBottom:2}}>{m.value}</div>
                    <div style={{fontSize:11,color:"#94a3b8"}}>{m.sub}</div>
                  </div>
                ))}
              </div>
              <div style={{padding:"16px 24px",borderBottom:"1px solid #f1f5f9"}}>
                <div style={{...S.label, marginBottom:12}}>Supplier incident rate this week</div>
                {[
                  {name:"SUP002 Horizon Global",  rate:13.4, bad:true },
                  {name:"SUP004 CoreTech",         rate:11.8, bad:true },
                  {name:"SUP001 Apex Mfg",         rate:10.2, bad:null },
                  {name:"SUP006 Meridian",         rate:9.8,  bad:null },
                  {name:"SUP003 Summit Supply",    rate:8.1,  bad:false},
                ].map((s,i)=>(
                  <div key={i} style={{display:"flex",alignItems:"center",gap:10,marginBottom:8}}>
                    <div style={{fontSize:12,color:"#475569",width:160,flexShrink:0}}>{s.name}</div>
                    <div style={{flex:1,background:"#f1f5f9",borderRadius:4,height:7,overflow:"hidden"}}>
                      <div style={{width:s.rate*5+"%",height:"100%",background:s.bad===true?"#dc2626":s.bad===false?"#16a34a":"#d97706",borderRadius:4}}/>
                    </div>
                    <div style={{fontSize:12,fontWeight:700,color:s.bad===true?"#dc2626":s.bad===false?"#16a34a":"#d97706",width:38,textAlign:"right"}}>{s.rate}%</div>
                  </div>
                ))}
                <div style={{marginTop:10,fontSize:12,color:"#94a3b8"}}>Portfolio average: 11.02%</div>
              </div>
              <div style={{padding:"12px 24px",background:"#f0fdf4",fontSize:13,color:"#15803d",display:"flex",alignItems:"center",gap:8}}>
                <span>✓</span><span>Auto-published. 93% confidence, all policy checks passed, no human review required.</span>
              </div>
            </div>
          )}

          {demoKey!=="Weekly dashboard" && demoReport && (
            <div style={{background:"#fff",borderRadius:16,border:"1px solid #e2e8f0",boxShadow:"0 4px 24px rgba(0,0,0,0.06)",overflow:"hidden"}}>
              <div style={{padding:"20px 28px",borderBottom:"1px solid #f1f5f9",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                <div>
                  <div style={{...S.label, marginBottom:4}}>AI-generated report</div>
                  <div style={{...S.title, fontSize:13}}>{demoReport.title}</div>
                </div>
                <div style={{background:demoReport.confidence>=90?"#f0fdf4":"#fffbeb",border:"1px solid "+(demoReport.confidence>=90?"#bbf7d0":"#fef08a"),borderRadius:20,padding:"4px 12px",fontSize:12,fontWeight:700,color:demoReport.confidence>=90?"#16a34a":"#ca8a04"}}>
                  {demoReport.confidence}% confidence
                </div>
              </div>
              <div style={{padding:"20px 28px",borderBottom:"1px solid #f1f5f9"}}>
                <div style={{...S.label, marginBottom:8}}>Executive summary</div>
                <p style={{...S.body, margin:0}}>{demoReport.summary}</p>
              </div>
              <div style={{padding:"20px 28px",display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,borderBottom:"1px solid #f1f5f9"}}>
                {demoReport.metrics.map((m,i)=>(
                  <div key={i} style={{background:"#f8fafc",borderRadius:10,padding:"14px 16px"}}>
                    <div style={{...S.label, marginBottom:6}}>{m.label}</div>
                    <div style={{fontSize:18,fontWeight:800,color:"#0f172a",marginBottom:4}}>{m.value}</div>
                    <div style={{fontSize:11,fontWeight:600,color:m.up===null?"#94a3b8":m.up?"#16a34a":"#dc2626"}}>{m.change}</div>
                  </div>
                ))}
              </div>
              {demoReport.sections.map((s,i)=>(
                <div key={i} style={{padding:"16px 28px",borderBottom:i<demoReport.sections.length-1?"1px solid #f1f5f9":"none",background:i%2===1?"#f8fafc":"#fff"}}>
                  <div style={{...S.title, fontSize:13, marginBottom:6}}>{s.title}</div>
                  <p style={{...S.body, margin:0}}>{s.body}</p>
                </div>
              ))}
              <div style={{padding:"12px 28px",display:"flex",alignItems:"flex-start",gap:10,fontSize:13,...(demoReport.autoPublish?{background:"#f0fdf4",borderTop:"1px solid #bbf7d0",color:"#15803d"}:{background:"#fffbeb",borderTop:"1px solid #fef08a",color:"#92400e"})}}>
                <span>{demoReport.autoPublish?"✓":"⏳"}</span>
                <span>{demoReport.autoPublish?"This report passed all policy checks and was auto-published to relevant stakeholders.":"Spike alerts always require human review before being shared. This report is waiting in the queue."}</span>
              </div>
            </div>
          )}

          {!demoKey && (
            <div style={{background:"#fff",borderRadius:16,border:"2px dashed #e2e8f0",padding:"48px 32px",textAlign:"center",color:"#94a3b8",fontSize:13}}>
              Select a report type above to see a sample output.
            </div>
          )}
        </div>
      </section>

      <section id="tech" style={{background:"#fff",borderTop:"1px solid #e2e8f0",padding:"80px 32px"}}>
        <div style={{maxWidth:1100,margin:"0 auto"}}>
          <div style={{textAlign:"center",marginBottom:48}}>
            <h2 style={{fontSize:30,fontWeight:800,letterSpacing:"-0.02em",margin:"0 0 12px",color:"#0f172a"}}>Built with</h2>
            <p style={{...S.body, maxWidth:480, margin:"0 auto"}}>Every component chosen to make the agents reliable, accurate, and auditable.</p>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16}}>
            {TECH.map(t=>(
              <div key={t.name} style={{...S.card}}>
                <div style={{display:"inline-block",background:"#eff6ff",border:"1px solid #bfdbfe",borderRadius:6,padding:"3px 10px",fontSize:12,fontWeight:700,color:"#2563eb",marginBottom:10}}>{t.name}</div>
                <div style={{...S.title, marginBottom:6}}>{t.role}</div>
                <div style={{...S.body}}>{t.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section style={{background:"#fff",borderTop:"1px solid #e2e8f0",padding:"80px 32px"}}>
        <div style={{maxWidth:1100,margin:"0 auto"}}>
          <div style={{textAlign:"center",marginBottom:48}}>
            <h2 style={{fontSize:30,fontWeight:800,letterSpacing:"-0.02em",margin:"0 0 12px",color:"#0f172a"}}>Control plane and audit trail</h2>
            <p style={{...S.body, maxWidth:560, margin:"0 auto"}}>Every agent run is logged, monitored, and auditable. The system is built for oversight, not just automation.</p>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:16}}>
            {[
              { icon:"📋", title:"Human review queue",
                desc:"Every report that does not meet the auto-approval threshold goes to a review queue. Reviewers can approve, edit, or reject with a written reason. Rejected reports trigger a corrected re-run automatically." },
              { icon:"🔍", title:"Full observability",
                desc:"Every pipeline run is logged with status, confidence score, SQL queries executed, row counts, validation results, and policy decision. Nothing is a black box." },
              { icon:"📊", title:"Audit trail",
                desc:"All human decisions are stored with reviewer name, timestamp, and reason. Every report can be traced back to the exact query that generated it and the data it was based on." },
              { icon:"🛡️", title:"Policy engine",
                desc:"A deterministic rule engine evaluates every report before it is published. Rules cover confidence thresholds, hallucination flags, metric deviation limits, and required report sections. No LLM involved." },
              { icon:"🔄", title:"Rejection feedback loop",
                desc:"When a reviewer rejects a report with a reason, the agent pipeline re-runs automatically with that reason injected into the SQL generation prompt. The correction is applied without manual intervention." },
              { icon:"⚙️", title:"Role-based access",
                desc:"Admin, business, demo, and supplier roles each have a different view of the system. Enforced at both the UI layer and every API endpoint independently." },
            ].map(t=>(
              <div key={t.title} style={{...S.card}}>
                <div style={{fontSize:22,marginBottom:10}}>{t.icon}</div>
                <div style={{...S.title, marginBottom:8}}>{t.title}</div>
                <div style={{...S.body}}>{t.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section style={{background:"#0f172a",padding:"80px 32px",textAlign:"center"}}>
        <div style={{maxWidth:580,margin:"0 auto"}}>
          <h2 style={{fontSize:30,fontWeight:800,color:"#fff",letterSpacing:"-0.02em",margin:"0 0 16px"}}>Open to new opportunities</h2>
          <p style={{fontSize:13,color:"#94a3b8",margin:"0 0 32px",lineHeight:1.75}}>
            Background in business, data, and operations with a focus on building things that actually get used. This project sits at the intersection of business intelligence, AI engineering and security. If that is a combination you are looking for, let's connect.
          </p>
          <div style={{display:"flex",gap:16,justifyContent:"center",flexWrap:"wrap"}}>
            <a href={LI} target="_blank" rel="noreferrer" style={{background:"#0a66c2",color:"#fff",borderRadius:10,padding:"14px 28px",fontSize:13,fontWeight:600,textDecoration:"none",display:"inline-flex",alignItems:"center",gap:8}}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>
              Message on LinkedIn
            </a>
            <button onClick={onDemoLogin} style={{background:"rgba(255,255,255,0.1)",color:"#fff",border:"1px solid rgba(255,255,255,0.2)",borderRadius:10,padding:"14px 28px",fontSize:13,fontWeight:600,cursor:"pointer"}}>
              Explore with demo account
            </button>
          </div>
        </div>
      </section>

      <footer style={{background:"#0f172a",borderTop:"1px solid rgba(255,255,255,0.08)",padding:"24px 32px",textAlign:"center",fontSize:12,color:"#475569"}}>
        Built by Francisco Trindade, LangGraph, Claude Sonnet 4, BigQuery, FastAPI, Firebase, Cloud Run
      </footer>

      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
        * { box-sizing: border-box; }
        html { scroll-behavior: smooth; }
      `}</style>
    </div>
  );
}
// ── End Landing Page ──────────────────────────────────────────────────────────

function LoginPage({onBack="", autoEmail="", autoPassword=""}) {
  const [email,setEmail]       = useState(autoEmail);
  const [password,setPassword] = useState(autoPassword);
  const [loading,setLoading]   = useState(false);
  const [error,setError]       = useState(null);
  const _af = useRef(false);
  useEffect(() => {
    if (autoEmail && autoPassword && !_af.current) {
      _af.current = true;
      signInWithEmailAndPassword(auth, autoEmail, autoPassword).catch(e => setError(e.message));
    }
  }, [autoEmail, autoPassword]);

  const handleLogin = async (e) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;
    setLoading(true); setError(null);
    try { await signInWithEmailAndPassword(auth, email.trim(), password); }
    catch (err) { setError("Invalid email or password. Please try again."); }
    finally { setLoading(false); }
  };

  return (
    <div style={{minHeight:"100vh",background:C.bg,display:"flex",alignItems:"center",justifyContent:"center",padding:20}}>
      <div style={{width:"100%",maxWidth:380}}>
        <div style={{textAlign:"center",marginBottom:36}}>
          <div style={{fontSize:13,fontWeight:700,color:C.text,letterSpacing:"-0.02em",marginBottom:6}}>Agentic <span style={{color:C.blue}}>Intel</span></div>
          <div style={{fontSize:22,fontWeight:700,color:C.text,marginBottom:6}}>Supplier Intelligence</div>
          <div style={{fontSize:13,color:C.muted}}>Sign in to continue</div>
        </div>
        <Card style={{padding:"28px"}}>
          {error && <ErrMsg message={error}/>}
          <form onSubmit={handleLogin} style={{display:"flex",flexDirection:"column",gap:16}}>
            <div>
              <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Email</label>
              <input type="email" value={email} onChange={e=>setEmail(e.target.value)} placeholder="you@agentic-intel.de" autoComplete="email"
                style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:7,padding:"10px 12px",fontSize:13,fontFamily:"inherit",boxSizing:"border-box",outline:"none"}}/>
            </div>
            <div>
              <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Password</label>
              <input type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="••••••••" autoComplete="current-password"
                style={{width:"100%",background:C.surface,border:`1px solid ${C.border}`,color:C.text,borderRadius:7,padding:"10px 12px",fontSize:13,fontFamily:"inherit",boxSizing:"border-box",outline:"none"}}/>
            </div>
            <button type="submit" disabled={loading||!email.trim()||!password.trim()}
              style={{background:"rgba(96,165,250,0.2)",border:`1px solid ${C.blue}`,color:C.blue,borderRadius:7,padding:"11px",fontSize:13,fontWeight:600,cursor:loading?"not-allowed":"pointer",opacity:loading||!email.trim()||!password.trim()?0.5:1,marginTop:4}}>
              {loading ? "Signing in..." : "Sign in"}
            </button>
          </form>
        </Card>
        <div style={{textAlign:"center",marginTop:20,fontSize:11,color:C.muted}}>agentic-intel.de · Supplier Performance Intelligence</div>
      </div>
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [authUser,setAuthUser]     = useState(undefined);
  const [userRole,setUserRole]     = useState(null);
  const [supplierID,setSupplierID] = useState(null);
  const [view,setView]             = useState("queue");
  const [selected,setSelected]     = useState(null);
  const [queueCount,setQueueCount] = useState(null);
  const [decisions,setDecisions]   = useState({});
  const [dashTab,setDashTab]       = useState("business");
  const [controlTab,setControlTab] = useState("queue");
  const [theme,setTheme]           = useState("light");
  const [showLanding,setShowLanding]     = useState(true);
  const [demoAutoLogin,setDemoAutoLogin] = useState(false);
  const [,forceUpdate]             = useState(0);

  useEffect(() => {
    const unsub = onAuthStateChanged(auth, async (user) => {
      if (user) {
        const token = await user.getIdTokenResult();
        const claims = token.claims;
        setAuthUser(user);
        setUserRole(claims.role || null);
        setSupplierID(claims.supplierID || null);
        if (claims.role === "supplier") setView("supplier_dashboard");
        else if (claims.role === "business") setView("dashboards");
        else setView("dashboards");
      } else { setAuthUser(null); setUserRole(null); setSupplierID(null); }
    });
    return () => unsub();
  }, []);

  useEffect(() => {
    if (userRole === "admin" || userRole === "business") {
      apiFetch("/api/queue").then(d=>setQueueCount(d.total||0)).catch(()=>setQueueCount(null));
    }
  }, [decisions, view, userRole]);

  const handleSignOut = async () => { await signOut(auth); };
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    applyTheme(next);
    setTheme(next);
    forceUpdate(n => n + 1);
  };
  const isDemo = userRole === "demo";

  if (authUser === undefined) return (
    <div style={{minHeight:"100vh",background:"#f8fafc",display:"flex",alignItems:"center",justifyContent:"center"}}><Spinner/></div>
  );
  if (!authUser) {
    if (showLanding) return (
      <LandingPage
        onSignIn={() => setShowLanding(false)}
        onDemoLogin={() => { setShowLanding(false); setDemoAutoLogin(true); }}
      />
    );
    return (
      <LoginPage
        onBack={() => { setShowLanding(true); setDemoAutoLogin(false); }}
        autoEmail={demoAutoLogin ? "demo@agentic-intel.de" : ""}
        autoPassword={demoAutoLogin ? "DemoAccount!" : ""}
      />
    );
  }

  if (userRole === "supplier" && supplierID) {
    return (
      <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
        <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",height:52,background:C.surface}}>
          <div style={{fontSize:13,fontWeight:700,color:C.text,letterSpacing:"-0.02em"}}>Agentic <span style={{color:C.teal}}>Intel</span></div>
          <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:16}}>
            <span style={{fontSize:12,color:C.muted}}>{authUser.email}</span>
            <button onClick={toggleTheme} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>{theme==="dark"?"☀️ Light":"🌙 Dark"}</button>
            <button onClick={handleSignOut} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>Sign out</button>
          </div>
        </div>
        <div style={{padding:"28px",maxWidth:1100,margin:"0 auto"}}>
          <SupplierDashboard initialSupplier={supplierID} supplierFacing={true}/>
        </div>
      </div>
    );
  }

  const allNav = [
    {id:"dashboards",     label:"Dashboards",     adminOnly:false, demoOk:true,  group:"main"},
    {id:"ask",            label:"Ask",            adminOnly:false, demoOk:true,  group:"main"},
    {id:"new_report",     label:"New Report",     adminOnly:false, demoOk:false, group:"main"},
    {id:"control_plane",  label:"Control Plane",  badge:queueCount, adminOnly:true, demoOk:true, group:"control"},
  ];
  const nav = allNav.filter(item => {
    if (isDemo) return item.demoOk;
    if (userRole === "admin") return true;
    return !item.adminOnly;
  });

  return (
    <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
      <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",gap:0,height:52,background:C.surface,position:"sticky",top:0,zIndex:100}}>
        <div style={{fontSize:13,fontWeight:700,color:C.text,letterSpacing:"-0.02em",marginRight:32}}>
          Agentic <span style={{color:C.blue}}>Intel</span>
          {userRole==="business"&&<span style={{fontSize:10,color:C.muted,fontWeight:400,marginLeft:8,textTransform:"uppercase",letterSpacing:"0.08em"}}>read only</span>}
          {isDemo&&<span style={{fontSize:10,color:C.amber,fontWeight:400,marginLeft:8,textTransform:"uppercase",letterSpacing:"0.08em"}}>demo</span>}
        </div>
        {nav.map((item,i)=>(
          <Fragment key={item.id}>
            {i>0 && nav[i-1].group!==item.group && (
              <div style={{width:1,height:20,background:C.border,margin:"0 8px",alignSelf:"center"}}/>
            )}
            <button onClick={()=>{ setView(item.id); setSelected(null); }} style={{background:"none",border:"none",borderBottom:view===item.id?`2px solid ${C.blue}`:"2px solid transparent",color:view===item.id?C.blue:C.muted,padding:"0 16px",height:"100%",cursor:"pointer",fontSize:13,fontWeight:view===item.id?600:400,display:"flex",alignItems:"center",gap:8,transition:"all 0.15s"}}>
              {item.label}
              {item.badge!=null&&<span style={{background:C.blue,color:"#fff",borderRadius:"10px",padding:"1px 7px",fontSize:11,fontWeight:700}}>{item.badge}</span>}
            </button>
          </Fragment>
        ))}
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:16}}>
          <span style={{fontSize:12,color:C.muted}}>{new Date().toLocaleDateString("en-GB",{weekday:"short",day:"2-digit",month:"short",year:"numeric"})}</span>
          <span style={{fontSize:12,color:C.muted}}>{authUser.email}</span>
          <button onClick={toggleTheme} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>{theme==="dark"?"☀️ Light":"🌙 Dark"}</button>
          <button onClick={handleSignOut} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>Sign out</button>
        </div>
      </div>
      <div style={{padding:"28px",maxWidth:1300,margin:"0 auto"}}>
        {view==="control_plane"&&!selected&&(
          <div>
            <div style={{display:"flex",gap:2,borderBottom:`1px solid ${C.border}`,marginBottom:24}}>
              {[{id:"queue",label:"Queue"},{id:"observability",label:"Observability"}].map(t=>(
                <button key={t.id} onClick={()=>setControlTab(t.id)} style={{background:"none",border:"none",borderBottom:controlTab===t.id?`2px solid ${C.blue}`:"2px solid transparent",color:controlTab===t.id?C.blue:C.muted,padding:"10px 20px",cursor:"pointer",fontSize:13,fontWeight:controlTab===t.id?600:400}}>
                  {t.label}{t.id==="queue"&&queueCount!=null&&<span style={{marginLeft:6,background:C.blue,color:"#fff",borderRadius:10,padding:"1px 7px",fontSize:11,fontWeight:700}}>{queueCount}</span>}
                </button>
              ))}
            </div>
            {controlTab==="queue"&&<RunQueue onSelect={run=>{ setSelected(run); setView("audit"); }}/>}
            {controlTab==="observability"&&<Observability/>}
          </div>
        )}
        {view==="audit"&&selected&&<AuditView runSummary={selected} isDemo={isDemo} onDecision={dec=>{ setDecisions(p=>({...p,[dec.runID]:dec})); }} onBack={()=>{ setSelected(null); setView("control_plane"); setControlTab("queue"); }}/>}
        {view==="dashboards"&&(
          <div>
            <div style={{display:"flex",gap:2,borderBottom:`1px solid ${C.border}`,marginBottom:24}}>
              {[{id:"business",label:"Business Overview"},{id:"supplier",label:"Supplier Account"}].map(t=>(
                <button key={t.id} onClick={()=>setDashTab(t.id)} style={{background:"none",border:"none",borderBottom:dashTab===t.id?`2px solid ${C.blue}`:"2px solid transparent",color:dashTab===t.id?C.blue:C.muted,padding:"10px 20px",cursor:"pointer",fontSize:13,fontWeight:dashTab===t.id?600:400}}>{t.label}</button>
              ))}
            </div>
            {dashTab==="business"&&<BusinessDashboard/>}
            {dashTab==="supplier"&&<SupplierDashboard/>}
          </div>
        )}
        {view==="new_report"    &&<NewReport key={view} onCreated={()=>setView("queue")}/>}
        {view==="ask"           &&<AskQuestion/>}
        {view==="observability" &&<Observability/>}
      </div>
    </div>
  );
}
