import { useState, useEffect, useCallback, useRef } from "react";
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
// C is a mutable proxy — App.setTheme() updates its properties in place
const C = { ...DARK };
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
const confColor = c => c>=0.85?C.green:c>=0.75?C.amber:C.red;

// ── Shared UI ─────────────────────────────────────────────────────────────────
const Card = ({children,style={}}) => <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"16px 20px",...style}}>{children}</div>;
const SLabel = ({children}) => <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:10,marginTop:20}}>{children}</div>;
const Chip = ({label,active,onClick}) => (
  <button onClick={onClick} style={{background:active?"rgba(96,165,250,0.2)":C.surface,border:`1px solid ${active?C.blue:C.border}`,color:active?C.blue:C.muted,borderRadius:20,padding:"3px 12px",fontSize:11,cursor:"pointer",transition:"all 0.15s"}}>
    {label}
  </button>
);

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

const getTT = () => ({contentStyle:{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,fontSize:12,color:C.text}});

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
                  {filteredCategories.map((r,i)=><Cell key={i} fill={r.productCategory===filterCategory?C.amber:C.purple} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
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
              {filteredResMix.map((_,i)=><Cell key={i} fill={COLORS[i%COLORS.length]} />)}
            </Pie>
            <Tooltip {...getTT()} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
          </PieChart>
        </ResponsiveContainer>
      </Card>
    </div>
  );
}

// ── Supplier Account Dashboard ────────────────────────────────────────────────
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
                      {data.cat_return_rate.map((r,i)=><Cell key={i} fill={r.productCategory===filterCategory?C.amber:C.teal} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
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
                      {(data.incident_types||[]).map((r,i)=><Cell key={i} fill={COLORS[i%COLORS.length]} opacity={filterIncType&&r.incidentType!==filterIncType?0.35:1} />)}
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
                      {(data.resolution_mix||[]).map((_,i)=><Cell key={i} fill={COLORS[i%COLORS.length]} />)}
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
  const [goal,setGoal]   = useState("");
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
      const res = await apiFetch("/api/runs",{method:"POST",body:JSON.stringify({reportType,supplierID:isSupplier?supplierID:null,goal})});
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
            // Force status to pending_review if timed out and still running
            if (elapsed > 180000 && (!s.status || !TERMINAL.includes(s.status))) {
              setStatus("pending_review");
            }
            try {
              const full = await apiFetch(`/api/runs/${newRunID}`);
              setRunData(full);
            } catch(e) { /* run data may not be available yet */ }
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
      alert(share?"Report approved and shared with supplier. It will appear in their view.":"Report approved for internal use only.");
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
        <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:10}}>
          <div style={{fontSize:14,fontWeight:600,color:C.amber,marginBottom:4}}>⚠ Report sent to review queue</div>
          <div style={{fontSize:12,color:C.muted}}>Confidence was below auto-approve threshold. An admin needs to review it in the Queue tab. You can find it in Recent Reports below once it loads.</div>
        </div>
      )}
      {isReady&&runData&&(
        <div>
          {IN_QUEUE.includes(status) ? (
            <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:10}}>
              <div style={{fontSize:14,fontWeight:600,color:C.amber,marginBottom:4}}>⚠ Low confidence — pending admin review</div>
              <div style={{fontSize:12,color:C.muted}}>Confidence: {((runData.confidence||0)*100).toFixed(0)}% · Below auto-approve threshold. You can read the report and save it internally, but sharing with a supplier requires admin approval in the Queue tab.</div>
            </div>
          ) : (
            <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.2)",borderRadius:10}}>
              <div style={{fontSize:14,fontWeight:600,color:C.green,marginBottom:4}}>Report ready — review below</div>
              <div style={{fontSize:12,color:C.green}}>Confidence: {((runData.confidence||0)*100).toFixed(0)}% · {runData.policyDecision?.replace(/_/g," ")}</div>
            </div>
          )}
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
                <div style={{fontSize:14,fontWeight:600,color:C.text,marginBottom:4}}>🔒 Internal only</div>
                <div style={{fontSize:12,color:C.muted}}>Save to control plane. Not visible to supplier.</div>
              </button>
              {isSupplier&&(
                <div style={{padding:"16px",border:`1px solid ${IN_QUEUE.includes(status)?C.border:C.border}`,borderRadius:8,background:IN_QUEUE.includes(status)?"rgba(0,0,0,0.02)":C.surface,opacity:IN_QUEUE.includes(status)?0.6:1,position:"relative",cursor:IN_QUEUE.includes(status)?"not-allowed":"pointer"}}
                  onClick={!IN_QUEUE.includes(status)?()=>handleShare(true):undefined}
                  onMouseEnter={e=>{ if(!IN_QUEUE.includes(status)) e.currentTarget.style.borderColor=C.teal; }}
                  onMouseLeave={e=>{ e.currentTarget.style.borderColor=C.border; }}>
                  <div style={{fontSize:14,fontWeight:600,color:IN_QUEUE.includes(status)?C.muted:C.teal,marginBottom:4}}>🔗 Share with supplier</div>
                  <div style={{fontSize:12,color:C.muted}}>
                    {IN_QUEUE.includes(status)
                      ? "Requires admin approval in Queue first."
                      : "Appears in supplier's view alongside their standard dashboard."}
                  </div>
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

  const statusColor = (s) => {
    if (["approved","edited_and_approved","auto_approved"].includes(s)) return C.green;
    if (s === "rejected") return C.red;
    if (["pending_review","pending","pending_publish","escalated"].includes(s)) return C.amber;
    return C.muted;
  };
  const statusLabel = (s) => {
    if (s === "approved" || s === "edited_and_approved") return "Approved";
    if (s === "auto_approved") return "Auto-approved";
    if (s === "pending_review" || s === "pending" || s === "pending_publish") return "Awaiting review";
    if (s === "escalated") return "Awaiting review";
    if (s === "rejected") return "Rejected";
    if (s === "running") return "Processing";
    return s || "Processing";
  };

  return (
    <div style={{marginTop:32}}>
      <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:14,display:"flex",alignItems:"center",gap:10}}>
        Recent Reports
        <button onClick={load} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:4,padding:"1px 8px",fontSize:10,cursor:"pointer"}}>↻ Refresh</button>
      </div>
      <div style={{display:"flex",flexDirection:"column",gap:8}}>
        {reports.map((run,i)=>{
          const ds = run.displayStatus || run.decision || run.status;
          return (
            <div key={run.runID} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,overflow:"hidden"}}>
              <div style={{padding:"14px 18px",display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:4,flexWrap:"wrap"}}>
                    <span style={{fontSize:13,fontWeight:600,color:C.text}}>{fmt.label(run.reportType)}</span>
                    {run.supplierID&&<Badge>{run.supplierID}</Badge>}
                    <span style={{fontSize:11,padding:"2px 8px",borderRadius:4,background:`${statusColor(ds)}18`,color:statusColor(ds),fontWeight:600,textTransform:"uppercase",letterSpacing:"0.04em"}}>
                      {statusLabel(ds)}
                    </span>
                  </div>
                  <div style={{fontSize:11,color:C.muted}}>
                    {run.startedAt ? new Date(run.startedAt).toLocaleString("en-GB") : ""}
                    {run.confidence ? ` · Confidence: ${((run.confidence||0)*100).toFixed(0)}%` : ""}
                    {run.approvedBy ? ` · ${run.approvedBy}` : run.reviewer ? ` · ${run.reviewer}` : ""}
                  </div>
                </div>
                <button onClick={()=>setExpanded(expanded===i?null:i)}
                  style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer",flexShrink:0}}>
                  {expanded===i?"Hide ↑":"View ↓"}
                </button>
              </div>
              {expanded===i&&(
                <div style={{borderTop:`1px solid ${C.border}`,padding:"16px 18px"}}>
                  {run.reason && (
                    <div style={{padding:"10px 14px",background:"rgba(239,68,68,0.06)",border:"1px solid rgba(239,68,68,0.15)",borderRadius:7,marginBottom:14,fontSize:12,color:C.red}}>
                      Rejection reason: {run.reason}
                    </div>
                  )}
                  {run.approvedBy && (
                    <div style={{fontSize:11,color:C.muted,marginBottom:12}}>
                      Approved by {run.approvedBy}{run.approvedAt ? ` · ${new Date(run.approvedAt).toLocaleDateString("en-GB")}` : ""}
                    </div>
                  )}
                  {run.reportNarrative ? (
                    <div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:500,overflowY:"auto"}}>
                      {run.reportNarrative}
                    </div>
                  ) : (
                    <div style={{padding:"12px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:7,fontSize:13,color:C.muted,fontStyle:"italic"}}>
                      {ds === "pending_review" || ds === "pending"
                        ? "Awaiting admin review in Queue. Narrative will appear here once approved."
                        : ds === "rejected"
                        ? "This report was rejected and not published."
                        : "Report is still processing."}
                    </div>
                  )}
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
              <div style={{fontSize:15,color:C.muted,marginBottom:16}}>Ask anything about your supplier data</div>
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
              <div style={{background:"rgba(96,165,250,0.15)",border:`1px solid rgba(96,165,250,0.25)`,borderRadius:"12px 12px 4px 12px",padding:"10px 16px",maxWidth:"75%",fontSize:14,color:C.text,lineHeight:1.5}}>
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
                <span style={{fontSize:15,fontWeight:600,color:C.text}}>{fmt.label(run.reportType)}</span>
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
              {run.softFailures?.length>0&&<div style={{fontSize:12,color:C.amber}}>⚠ {run.softFailures.join(" · ")}</div>}
            </div>
            <div style={{color:C.muted,fontSize:13}}>Review →</div>
          </div>
        ))}
        {!error&&runs.length===0&&<div style={{textAlign:"center",padding:60,color:C.muted,fontSize:14}}>No pending reports.</div>}
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
    <div style={{textAlign:"center",padding:"80px 40px"}}>
      <div style={{fontSize:48,marginBottom:16}}>{decision==="rejected"?"✗":"✓"}</div>
      <div style={{fontSize:20,fontWeight:700,color:C.text,marginBottom:8}}>{decision==="approved"?"Approved":decision==="edited_and_approved"?"Edited & Approved":"Rejected"}{shareWithSupplier?" · Shared with supplier":""}</div>
      <button onClick={onBack} style={{background:C.surface,border:"none",color:C.muted,borderRadius:7,padding:"10px 24px",cursor:"pointer",fontSize:13,fontWeight:600,marginTop:24}}>← Back to Queue</button>
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
            {tab==="report"&&(decision==="edited_and_approved"?<textarea value={editedNarrative} onChange={e=>setEditedNarrative(e.target.value)} style={{width:"100%",minHeight:480,background:C.surface,border:`1px solid rgba(96,165,250,0.3)`,borderRadius:8,padding:16,color:C.text,fontSize:13,lineHeight:1.7,fontFamily:"monospace",resize:"vertical",boxSizing:"border-box"}}/>:<div style={{fontSize:13,lineHeight:1.8,color:C.text,whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif"}}>{run?.reportNarrative||"No narrative."}</div>)}
            {tab==="validation"&&(
              <div style={{display:"flex",flexDirection:"column",gap:8}}>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:10,marginBottom:16}}>
                  <Scorecard label="Passed" value={run?.validationPassed||0}/><Scorecard label="Failed" value={run?.validationFailed||0}/><Scorecard label="Hallucinations" value={run?.hallucinationFlags||0}/><Scorecard label="Pass Rate" value={fmt.pct((run?.validationPassed||0)/Math.max((run?.validationPassed||0)+(run?.validationFailed||0),1)*100)}/>
                </div>
                {(run?.validationResults||[]).map((r,i)=>(
                  <div key={i} style={{display:"flex",alignItems:"flex-start",gap:10,padding:"10px 14px",background:r.passed?"rgba(34,197,94,0.05)":"rgba(239,68,68,0.05)",border:`1px solid ${r.passed?"rgba(34,197,94,0.15)":"rgba(239,68,68,0.15)"}`,borderRadius:6}}>
                    <span style={{color:r.passed?C.green:C.red,fontSize:14}}>{r.passed?"✓":"✗"}</span>
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
          {!isDemo && run?.supplierID&&decision&&decision!=="rejected"&&(
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
        {!error&&history.length===0&&<div style={{textAlign:"center",padding:60,color:C.muted,fontSize:14}}>No run history yet.</div>}
      </div>
    </div>
  );
}

// ── Login Page ────────────────────────────────────────────────────────────────
function LoginPage() {
  const [email,setEmail]       = useState("");
  const [password,setPassword] = useState("");
  const [loading,setLoading]   = useState(false);
  const [error,setError]       = useState(null);

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
  const [theme,setTheme]           = useState("dark");
  const [,forceUpdate]             = useState(0);

  // All hooks before any conditional returns
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
        else setView("queue");
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
    <div style={{minHeight:"100vh",background:C.bg,display:"flex",alignItems:"center",justifyContent:"center"}}><Spinner/></div>
  );

  if (!authUser) return <LoginPage/>;

  // Supplier portal
  if (userRole === "supplier" && supplierID) {
    return (
      <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
        <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",height:52,background:C.surface}}>
          <div style={{fontSize:14,fontWeight:700,color:C.text,letterSpacing:"-0.02em"}}>Agentic <span style={{color:C.teal}}>Intel</span></div>
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

  // Admin / Business / Demo control plane
  const allNav = [
    {id:"queue",         label:"Queue",         badge:queueCount, adminOnly:true,  demoOk:true},
    {id:"dashboards",    label:"Dashboards",    adminOnly:false,                   demoOk:true},
    {id:"new_report",    label:"New Report",    adminOnly:false,                   demoOk:false},
    {id:"ask",           label:"Ask",           adminOnly:false,                   demoOk:true},
    {id:"observability", label:"Observability", adminOnly:true,                    demoOk:true},
  ];
  const nav = allNav.filter(item => {
    if (isDemo) return item.demoOk;
    if (userRole === "admin") return true;
    return !item.adminOnly;
  });

  return (
    <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
      <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",gap:0,height:52,background:C.surface,position:"sticky",top:0,zIndex:100}}>
        <div style={{fontSize:14,fontWeight:700,color:C.text,letterSpacing:"-0.02em",marginRight:32}}>
          Agentic <span style={{color:C.blue}}>Intel</span>
          {userRole==="business"&&<span style={{fontSize:10,color:C.muted,fontWeight:400,marginLeft:8,textTransform:"uppercase",letterSpacing:"0.08em"}}>read only</span>}
          {isDemo&&<span style={{fontSize:10,color:C.amber,fontWeight:400,marginLeft:8,textTransform:"uppercase",letterSpacing:"0.08em"}}>demo</span>}
        </div>
        {nav.map(item=>(
          <button key={item.id} onClick={()=>{ setView(item.id); setSelected(null); }} style={{background:"none",border:"none",borderBottom:view===item.id?`2px solid ${C.blue}`:"2px solid transparent",color:view===item.id?C.blue:C.muted,padding:"0 16px",height:"100%",cursor:"pointer",fontSize:13,fontWeight:view===item.id?600:400,display:"flex",alignItems:"center",gap:8,transition:"all 0.15s"}}>
            {item.label}
            {item.badge!=null&&<span style={{background:C.blue,color:"#fff",borderRadius:"10px",padding:"1px 7px",fontSize:11,fontWeight:700}}>{item.badge}</span>}
          </button>
        ))}
        <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:16}}>
          <span style={{fontSize:12,color:C.muted}}>{new Date().toLocaleDateString("en-GB",{weekday:"short",day:"2-digit",month:"short",year:"numeric"})}</span>
          <span style={{fontSize:12,color:C.muted}}>{authUser.email}</span>
          <button onClick={toggleTheme} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>{theme==="dark"?"☀️ Light":"🌙 Dark"}</button>
          <button onClick={handleSignOut} style={{background:"none",border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:"4px 12px",fontSize:12,cursor:"pointer"}}>Sign out</button>
        </div>
      </div>

      <div style={{padding:"28px",maxWidth:1300,margin:"0 auto"}}>
        {view==="queue"&&!selected&&<RunQueue onSelect={run=>{ setSelected(run); setView("audit"); }}/>}
        {view==="audit"&&selected&&<AuditView runSummary={selected} isDemo={isDemo} onDecision={dec=>{ setDecisions(p=>({...p,[dec.runID]:dec})); }} onBack={()=>{ setSelected(null); setView("queue"); }}/>}
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
