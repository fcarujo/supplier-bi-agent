import { useState, useEffect, useCallback, useRef } from "react";
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine
} from "recharts";

// ── API ───────────────────────────────────────────────────────────────────────
const API_BASE = window.location.hostname === "localhost" ? "http://localhost:8000" : "";
async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  if (!res.ok) { const e = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(e.detail || `API error ${res.status}`); }
  return res.json();
}

// ── Tokens ────────────────────────────────────────────────────────────────────
const C = { bg: "#0a0e1a", surface: "rgba(255,255,255,0.03)", border: "rgba(255,255,255,0.08)", text: "#e2e8f0", muted: "#64748b", blue: "#60a5fa", green: "#22c55e", amber: "#f59e0b", red: "#ef4444", purple: "#a855f7", teal: "#2dd4bf" };
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
  <button onClick={onClick} style={{background:active?"rgba(96,165,250,0.2)":"rgba(255,255,255,0.04)",border:`1px solid ${active?C.blue:C.border}`,color:active?C.blue:C.muted,borderRadius:20,padding:"3px 12px",fontSize:11,cursor:"pointer",transition:"all 0.15s"}}>
    {label}
  </button>
);

function Badge({children,variant="default"}) {
  const s={default:{bg:"rgba(255,255,255,0.08)",fg:"#94a3b8"},business:{bg:"rgba(59,130,246,0.15)",fg:C.blue},supplier:{bg:"rgba(20,184,166,0.15)",fg:C.teal},approved:{bg:"rgba(34,197,94,0.15)",fg:C.green},rejected:{bg:"rgba(239,68,68,0.15)",fg:C.red},pending:{bg:"rgba(245,158,11,0.15)",fg:C.amber},pass:{bg:"rgba(34,197,94,0.12)",fg:C.green},fail:{bg:"rgba(239,68,68,0.12)",fg:C.red}};
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
  return <div style={{display:"flex",alignItems:"center",gap:8}}><div style={{width:80,height:4,background:"rgba(255,255,255,0.1)",borderRadius:2,overflow:"hidden"}}><div style={{width:`${(value||0)*100}%`,height:"100%",background:c,borderRadius:2}}/></div><span style={{fontSize:12,color:c,fontWeight:700,fontFamily:"monospace"}}>{((value||0)*100).toFixed(0)}%</span></div>;
}

function Spinner() { return <div style={{display:"flex",justifyContent:"center",padding:40}}><div style={{width:24,height:24,border:"2px solid rgba(255,255,255,0.1)",borderTop:`2px solid ${C.blue}`,borderRadius:"50%",animation:"spin 0.8s linear infinite"}}/><style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style></div>; }
function ErrMsg({message,onRetry}) { return <div style={{padding:"12px 16px",background:"rgba(239,68,68,0.1)",border:"1px solid rgba(239,68,68,0.2)",borderRadius:8,display:"flex",gap:12,alignItems:"center",marginBottom:16}}><span style={{color:C.red}}>✗</span><span style={{fontSize:13,color:"#fca5a5",flex:1}}>{message}</span>{onRetry&&<button onClick={onRetry} style={{background:"none",border:"1px solid rgba(239,68,68,0.3)",color:C.red,borderRadius:6,padding:"4px 12px",cursor:"pointer",fontSize:12}}>Retry</button>}</div>; }

const TT = {contentStyle:{background:"#1e2436",border:`1px solid ${C.border}`,borderRadius:8,fontSize:12}};

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
      <input type="date" value={dateFrom||""} onChange={e=>onChange(e.target.value||null,dateTo)} style={{background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"3px 8px",fontSize:11}} />
      <span style={{color:C.muted,fontSize:11}}>to</span>
      <input type="date" value={dateTo||""} onChange={e=>onChange(dateFrom,e.target.value||null)} style={{background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"3px 8px",fontSize:11}} />
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
  // Cross-filter state
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

  // Apply cross-filters to datasets
  const filteredTrend = data?.trend || [];
  const filteredSuppliers = (data?.by_supplier||[]).filter(r =>
    (!filterCategory) // category filter doesn't narrow supplier list, just highlights
  );
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

      {/* Scorecards */}
      <div style={{display:"grid",gridTemplateColumns:"repeat(7,1fr)",gap:10,marginBottom:20}}>
        <Scorecard label="Total Orders"     value={fmt.num(s.total_orders)} />
        <Scorecard label="Gross Revenue"    value={fmt.cur(s.total_gross_revenue)} />
        <Scorecard label="Net Revenue"      value={fmt.cur(s.total_net_revenue)} />
        <Scorecard label="Incident Rate"    value={fmt.pct(s.incident_rate_pct)}  color={s.incident_rate_pct>15?C.red:s.incident_rate_pct>12?C.amber:C.green} />
        <Scorecard label="Return Rate"      value={fmt.pct(s.return_rate_pct)}    color={s.return_rate_pct>7?C.red:s.return_rate_pct>5?C.amber:C.green} />
        <Scorecard label="Resolution Cost"  value={fmt.cur(s.total_resolution_cost)} sub={`${((s.total_resolution_cost/(s.total_gross_revenue||1))*100).toFixed(1)}% of revenue`} />
        <Scorecard label="Returned Revenue" value={fmt.cur(s.returned_revenue)}   sub="Gross rev of returned orders" warn />
      </div>

      {/* Trend charts — full width */}
      <SLabel>Incident &amp; Return Rate Trend</SLabel>
      <Card style={{marginBottom:16}}>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={filteredTrend} margin={{top:5,right:20,bottom:5,left:0}}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="month" stroke={C.muted} tick={{fontSize:11}} tickFormatter={fmt.month} />
            <YAxis stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
            <Tooltip {...TT} formatter={(v,n)=>[`${(+v).toFixed(1)}%`,n]} labelFormatter={fmt.month} />
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
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
            <XAxis dataKey="month" stroke={C.muted} tick={{fontSize:11}} tickFormatter={fmt.month} />
            <YAxis stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
            <Tooltip {...TT} formatter={(v,n)=>[`${(+v).toFixed(2)}%`,n]} labelFormatter={fmt.month} />
            <Line type="monotone" dataKey="resolution_cost_pct" name="Res. Cost %" stroke={C.purple} strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      {/* Middle row — suppliers + categories */}
      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
        <div>
          <SLabel>Top 10 Suppliers by Incident Rate {filterSupplier && <span style={{color:C.blue,fontSize:10,marginLeft:6}}>● filtered</span>}</SLabel>
          <Card>
            <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter all charts</div>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={filteredSuppliers} layout="vertical" margin={{left:55,right:30}} onClick={e=>e?.activePayload&&setFilterSupplier(prev=>prev===e.activePayload[0]?.payload?.supplierID?null:e.activePayload[0]?.payload?.supplierID)}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                <YAxis type="category" dataKey="supplierID" stroke={C.muted} tick={{fontSize:11}} width={50} />
                <Tooltip {...TT} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
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
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                <Tooltip {...TT} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
                <Bar dataKey="incident_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                  {filteredCategories.map((r,i)=><Cell key={i} fill={r.productCategory===filterCategory?C.amber:C.purple} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </div>

      {/* Bottom row — resolution mix */}
      <SLabel>Resolution Method Mix</SLabel>
      <Card>
        <ResponsiveContainer width="100%" height={200}>
          <PieChart>
            <Pie data={filteredResMix} dataKey="total_incidents" nameKey="incidentResolution" cx="50%" cy="50%" outerRadius={75} label={({name,percent})=>`${(name||"").replace(/_/g," ")} ${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={11}>
              {filteredResMix.map((_,i)=><Cell key={i} fill={COLORS[i%COLORS.length]} />)}
            </Pie>
            <Tooltip {...TT} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
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
  const [reportExpanded,setReportExpanded] = useState(null); // index

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

  // Cross-filter applied datasets
  const skuInc = (data?.sku_incidents||[]).filter(r=> (!filterCategory||r.productCategory===filterCategory)&&(!filterIncType||r.incidentType===filterIncType));
  const skuRet = (data?.sku_returns||[]).filter(r=> (!filterCategory||r.productCategory===filterCategory));

  // Aggregate SKU incident table (group by SKU)
  const skuIncAgg = Object.values(skuInc.reduce((acc,r)=>{
    if(!acc[r.productSKU]) acc[r.productSKU]={productSKU:r.productSKU,productCategory:r.productCategory,total_incidents:0,total_resolution_cost:0,avg_product_rating:[]};
    acc[r.productSKU].total_incidents += (+r.total_incidents||0);
    acc[r.productSKU].total_resolution_cost += (+r.total_resolution_cost||0);
    if(r.avg_product_rating) acc[r.productSKU].avg_product_rating.push(+r.avg_product_rating);
    return acc;
  },{})).map(r=>({...r,avg_product_rating:r.avg_product_rating.length?r.avg_product_rating.reduce((a,b)=>a+b,0)/r.avg_product_rating.length:0})).sort((a,b)=>b.total_incidents-a.total_incidents);

  // Aggregate SKU return table
  const skuRetAgg = Object.values(skuRet.reduce((acc,r)=>{
    if(!acc[r.productSKU]) acc[r.productSKU]={productSKU:r.productSKU,productCategory:r.productCategory,total_returns:0,avg_product_rating:[]};
    acc[r.productSKU].total_returns += (+r.total_returns||0);
    if(r.avg_product_rating) acc[r.productSKU].avg_product_rating.push(+r.avg_product_rating);
    return acc;
  },{})).map(r=>({...r,avg_product_rating:r.avg_product_rating.length?r.avg_product_rating.reduce((a,b)=>a+b,0)/r.avg_product_rating.length:0})).sort((a,b)=>b.total_returns-a.total_returns);

  const hasFilter = filterCategory||filterIncType;

  return (
    <div>
      {/* Header */}
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
              <select value={selectedID} onChange={e=>setSelectedID(e.target.value)} style={{background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"6px 12px",fontSize:13,minWidth:200}}>
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
          {/* Scorecard row 1 */}
          <div style={{display:"grid",gridTemplateColumns:"repeat(6,1fr)",gap:10,marginBottom:10}}>
            <Scorecard label="Total Orders"     value={fmt.num(s.total_orders)} />
            <Scorecard label="Product Cost"     value={fmt.cur(s.total_product_cost)} sub="Supplier's revenue" />
            <Scorecard label="Incident Rate"    value={fmt.pct(s.incident_rate_pct)}  color={incVsBench>0?C.red:C.green} />
            <Scorecard label="Return Rate"      value={fmt.pct(s.return_rate_pct)}    color={retVsBench>0?C.red:C.green} />
            <Scorecard label="Resolution Cost"  value={fmt.cur(s.total_resolution_cost)} />
            <Scorecard label="Returned Revenue" value={fmt.cur(s.returned_revenue)} warn />
          </div>

          {/* Scorecard row 2 — benchmarks */}
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

          {/* Category charts — click to filter SKU tables */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>Incident Rate by Category {filterCategory&&<span style={{color:C.blue,fontSize:10,marginLeft:6}}>● {filterCategory}</span>}</SLabel>
              <Card>
                <div style={{fontSize:11,color:C.muted,marginBottom:8}}>Click to filter SKU tables &amp; all charts</div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={data.cat_incident_rate} layout="vertical" margin={{left:100,right:20}} onClick={e=>e?.activePayload&&setFilterCategory(prev=>prev===e.activePayload[0]?.payload?.productCategory?null:e.activePayload[0]?.payload?.productCategory)}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                    <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                    <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                    <Tooltip {...TT} formatter={v=>[`${(+v).toFixed(1)}%`,"Incident Rate"]} />
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
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" horizontal={false} />
                    <XAxis type="number" stroke={C.muted} tick={{fontSize:11}} tickFormatter={v=>`${v}%`} />
                    <YAxis type="category" dataKey="productCategory" stroke={C.muted} tick={{fontSize:10}} width={95} />
                    <Tooltip {...TT} formatter={v=>[`${(+v).toFixed(1)}%`,"Return Rate"]} />
                    <Bar dataKey="return_rate_pct" radius={[0,4,4,0]} cursor="pointer">
                      {data.cat_return_rate.map((r,i)=><Cell key={i} fill={r.productCategory===filterCategory?C.amber:C.teal} opacity={filterCategory&&r.productCategory!==filterCategory?0.35:1} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            </div>
          </div>

          {/* SKU tables */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>SKU Incidents {filterCategory&&`— ${filterCategory}`}</SLabel>
              <Card style={{padding:0}}>
                <div style={{overflowX:"auto",maxHeight:280,overflowY:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                    <thead style={{position:"sticky",top:0,background:"#0f1525"}}>
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
                    <thead style={{position:"sticky",top:0,background:"#0f1525"}}>
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

          {/* Bottom row — return reasons, incident types, resolution mix */}
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:16,marginBottom:16}}>
            <div>
              <SLabel>Return Reasons</SLabel>
              <Card>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={(data.return_reasons||[]).filter(r=>!filterCategory||(skuRet.some(s=>s.buyersRemorseReason===r.buyersRemorseReason)))} margin={{left:10,right:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="buyersRemorseReason" stroke={C.muted} tick={{fontSize:9}} tickFormatter={v=>(v||"").replace(/_/g," ")} />
                    <YAxis stroke={C.muted} tick={{fontSize:11}} />
                    <Tooltip {...TT} formatter={(v,n,p)=>[fmt.num(v),(p.payload.buyersRemorseReason||"").replace(/_/g," ")]} />
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
                    <Tooltip {...TT} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
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
                    <Tooltip {...TT} formatter={(v,n)=>[fmt.num(v),(n||"").replace(/_/g," ")]} />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </div>
          </div>

          {/* Reports */}
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
                    <div style={{fontSize:13,lineHeight:1.8,color:"#cbd5e1",whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:500,overflowY:"auto",paddingTop:12,marginTop:12,borderTop:`1px solid ${C.border}`}}>
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

  // Work out which step we're on based on elapsed time
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
      {/* Progress bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontSize: 12, color: C.muted }}>Pipeline progress</span>
        <span style={{ fontSize: 12, color: C.muted, fontFamily: "monospace" }}>{timeStr} elapsed</span>
      </div>
      <div style={{ width: "100%", height: 3, background: "rgba(255,255,255,0.08)", borderRadius: 2, marginBottom: 24, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${progress}%`, background: done ? C.green : C.blue, borderRadius: 2, transition: "width 0.8s ease" }} />
      </div>

      {/* Steps */}
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {PIPELINE_STEPS.map((step, i) => {
          const isActive  = !done && i === activeIdx;
          const isComplete = done ? true : i < activeIdx;
          const isPending = !isComplete && !isActive;

          return (
            <div key={step.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 10px", borderRadius: 6, background: isActive ? "rgba(96,165,250,0.08)" : "transparent", border: isActive ? `1px solid rgba(96,165,250,0.2)` : "1px solid transparent", transition: "all 0.3s" }}>
              {/* Icon */}
              <div style={{ width: 20, height: 20, borderRadius: "50%", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", background: isComplete ? "rgba(34,197,94,0.15)" : isActive ? "rgba(96,165,250,0.15)" : "rgba(255,255,255,0.05)", border: `1px solid ${isComplete ? C.green : isActive ? C.blue : "rgba(255,255,255,0.1)"}` }}>
                {isComplete
                  ? <span style={{ fontSize: 11, color: C.green }}>✓</span>
                  : isActive
                    ? <div style={{ width: 8, height: 8, borderRadius: "50%", border: `1.5px solid ${C.blue}`, borderTopColor: "transparent", animation: "spin 0.7s linear infinite" }} />
                    : <span style={{ fontSize: 9, color: "rgba(255,255,255,0.2)" }}>○</span>
                }
              </div>

              {/* Label */}
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 13, fontWeight: isActive ? 600 : 400, color: isComplete ? "#94a3b8" : isActive ? C.text : "rgba(255,255,255,0.3)" }}>
                  {step.label}
                </span>
                {isActive && (
                  <span style={{ fontSize: 11, color: C.muted, marginLeft: 8 }}>{step.desc}</span>
                )}
              </div>

              {/* Step number */}
              <span style={{ fontSize: 10, color: "rgba(255,255,255,0.15)", fontFamily: "monospace" }}>{i + 1}/{PIPELINE_STEPS.length}</span>
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
  const pollRef = useRef(null);

  useEffect(()=>{ apiFetch("/api/suppliers").then(d=>setSuppliers(d.suppliers||[])).catch(()=>{}); return ()=>{ if(pollRef.current) clearInterval(pollRef.current); }; },[]);

  const isSupplier = reportType.includes("supplier");
  const isReady    = status&&!["running","starting"].includes(status);
  const TERMINAL   = ["pending_review","pending_publish","approved","rejected","escalated","failed","completed"];

  const handleSubmit = async () => {
    if (!goal.trim()||(isSupplier&&!supplierID)) return;
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setRunning(true); setError(null); setStatus("starting"); setRunData(null); setRunID(null); setStartTime(Date.now());
    try {
      const res = await apiFetch("/api/runs",{method:"POST",body:JSON.stringify({reportType,supplierID:isSupplier?supplierID:null,goal})});
      const newRunID = res.runID;
      setRunID(newRunID); setStatus("running");
      pollRef.current = setInterval(async()=>{
        try {
          const s = await apiFetch(`/api/runs/${newRunID}/status`);
          if (s.status) setStatus(s.status);
          if (s.status && TERMINAL.includes(s.status)) {
            clearInterval(pollRef.current); pollRef.current = null; setRunning(false);
            const full = await apiFetch(`/api/runs/${newRunID}`);
            setRunData(full);
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
          <select value={reportType} onChange={e=>setReportType(e.target.value)} disabled={running} style={{width:"100%",background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,opacity:running?0.5:1}}>
            <option value="adhoc_business">Ad-hoc Business Overview</option>
            <option value="adhoc_supplier">Ad-hoc Supplier Account</option>
          </select>
        </div>
        {isSupplier&&(
          <div>
            <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Supplier</label>
            <select value={supplierID} onChange={e=>setSupplierID(e.target.value)} disabled={running} style={{width:"100%",background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,opacity:running?0.5:1}}>
              <option value="">Select a supplier...</option>
              {suppliers.map(s=><option key={s.supplierID} value={s.supplierID}>{s.supplierName} ({s.supplierID})</option>)}
            </select>
          </div>
        )}
        <div>
          <label style={{fontSize:12,color:C.muted,display:"block",marginBottom:6}}>Report goal</label>
          <textarea value={goal} onChange={e=>setGoal(e.target.value)} disabled={running} placeholder="Describe what you need, e.g. Analyse SUP002 incident trends last 6 months vs previous 6 months, broken down by category and SKU..." rows={4} style={{width:"100%",background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"10px 12px",fontSize:13,resize:"vertical",fontFamily:"inherit",boxSizing:"border-box",opacity:running?0.5:1}} />
        </div>
        <button onClick={handleSubmit} disabled={running||!goal.trim()||(isSupplier&&!supplierID)} style={{background:running?"rgba(255,255,255,0.06)":"rgba(96,165,250,0.2)",border:`1px solid ${running?C.border:C.blue}`,color:running?C.muted:C.blue,borderRadius:7,padding:"10px 18px",fontSize:13,fontWeight:600,cursor:running?"not-allowed":"pointer",opacity:(running||!goal.trim()||(isSupplier&&!supplierID))?0.5:1}}>
          {running?"Running pipeline...":"Run Report"}
        </button>

        {/* Pipeline progress — shown while running or just finished */}
        {(running || isReady) && startTime && (
          <div style={{borderTop:`1px solid ${C.border}`,paddingTop:16,marginTop:4}}>
            <PipelineProgress startTime={startTime} status={status} />
            {runID && <div style={{fontSize:11,color:"rgba(255,255,255,0.15)",fontFamily:"monospace",marginTop:8}}>Run ID: {runID}</div>}
          </div>
        )}
      </Card>

      {/* Results + sharing decision */}
      {isReady&&runData&&(
        <div>
          <div style={{marginBottom:16,padding:"14px 18px",background:"rgba(34,197,94,0.08)",border:"1px solid rgba(34,197,94,0.2)",borderRadius:10}}>
            <div style={{fontSize:14,fontWeight:600,color:C.green,marginBottom:4}}>Report ready — review below</div>
            <div style={{fontSize:12,color:"#86efac"}}>Confidence: {((runData.confidence||0)*100).toFixed(0)}% · {runData.policyDecision?.replace(/_/g," ")}</div>
          </div>

          <Card style={{marginBottom:16}}>
            <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:10}}>Report Narrative</div>
            <div style={{fontSize:13,lineHeight:1.8,color:"#cbd5e1",whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif",maxHeight:400,overflowY:"auto"}}>
              {runData.reportNarrative||"No narrative generated."}
            </div>
          </Card>

          <Card>
            <div style={{fontSize:13,fontWeight:600,color:C.text,marginBottom:12}}>What would you like to do with this report?</div>
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12}}>
              <button onClick={()=>handleShare(false)} disabled={sharing} style={{padding:"16px",border:`1px solid ${C.border}`,borderRadius:8,background:"rgba(255,255,255,0.03)",cursor:"pointer",textAlign:"left",transition:"all 0.15s"}}
                onMouseEnter={e=>e.currentTarget.style.borderColor=C.blue}
                onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}>
                <div style={{fontSize:14,fontWeight:600,color:C.text,marginBottom:4}}>🔒 Internal only</div>
                <div style={{fontSize:12,color:C.muted}}>Save to control plane. Not visible to supplier.</div>
              </button>
              {isSupplier&&(
                <button onClick={()=>handleShare(true)} disabled={sharing} style={{padding:"16px",border:`1px solid ${C.border}`,borderRadius:8,background:"rgba(255,255,255,0.03)",cursor:"pointer",textAlign:"left",transition:"all 0.15s"}}
                  onMouseEnter={e=>e.currentTarget.style.borderColor=C.teal}
                  onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}>
                  <div style={{fontSize:14,fontWeight:600,color:C.teal,marginBottom:4}}>🔗 Share with supplier</div>
                  <div style={{fontSize:12,color:C.muted}}>Appears in supplier's view alongside their standard dashboard.</div>
                </button>
              )}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

// ── Ask a Question ────────────────────────────────────────────────────────────
function AskQuestion() {
  const [suppliers,setSuppliers]   = useState([]);
  const [supplierID,setSupplierID] = useState("");
  const [question,setQuestion]     = useState("");
  const [loading,setLoading]       = useState(false);
  const [result,setResult]         = useState(null);
  const [error,setError]           = useState(null);
  const [history,setHistory]       = useState([]);

  useEffect(()=>{ apiFetch("/api/suppliers").then(d=>setSuppliers(d.suppliers||[])).catch(()=>{}); },[]);

  const handleAsk = async () => {
    if (!question.trim()) return;
    setLoading(true); setError(null); setResult(null);
    try {
      const res = await apiFetch("/api/ask",{method:"POST",body:JSON.stringify({question,supplierID:supplierID||null})});
      setResult(res);
      setHistory(prev=>[{question,result:res,ts:new Date().toISOString()},...prev.slice(0,9)]);
      setQuestion("");
    } catch(e) {
      setError(e.message.includes("wasn't able")||e.message.includes("rephrasing")?e.message:"I wasn't able to answer that question. Try rephrasing it or being more specific.");
    } finally { setLoading(false); }
  };

  const examples = ["Which supplier had the highest incident rate last month?","Top 10 SKUs by resolution cost in the last 30 days","Most common return reasons for Electronics","Compare incident rates across fulfilment channels"];

  return (
    <div>
      <div style={{marginBottom:24}}>
        <h2 style={{fontSize:20,fontWeight:700,color:C.text,margin:0}}>Ask a Question</h2>
        <p style={{fontSize:13,color:C.muted,margin:"4px 0 0"}}>Natural language queries on your supplier data</p>
      </div>

      <Card style={{marginBottom:20}}>
        <div style={{display:"flex",gap:10,marginBottom:12}}>
          <select value={supplierID} onChange={e=>setSupplierID(e.target.value)} style={{background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"8px 12px",fontSize:13,minWidth:180}}>
            <option value="">All suppliers</option>
            {suppliers.map(s=><option key={s.supplierID} value={s.supplierID}>{s.supplierName}</option>)}
          </select>
        </div>
        <div style={{display:"flex",gap:10}}>
          <input value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>e.key==="Enter"&&!e.shiftKey&&handleAsk()} placeholder="Ask anything about your supplier data..." style={{flex:1,background:"rgba(255,255,255,0.06)",border:`1px solid ${C.border}`,color:C.text,borderRadius:6,padding:"10px 14px",fontSize:13,fontFamily:"inherit"}} />
          <button onClick={handleAsk} disabled={loading||!question.trim()} style={{background:"rgba(96,165,250,0.2)",border:`1px solid ${C.blue}`,color:C.blue,borderRadius:6,padding:"10px 20px",fontSize:13,fontWeight:600,cursor:loading?"not-allowed":"pointer",opacity:(loading||!question.trim())?0.5:1,whiteSpace:"nowrap"}}>
            {loading?"Thinking...":"Ask →"}
          </button>
        </div>
        <div style={{marginTop:12,display:"flex",gap:8,flexWrap:"wrap"}}>
          {examples.map((ex,i)=>(<button key={i} onClick={()=>setQuestion(ex)} style={{background:"rgba(255,255,255,0.04)",border:`1px solid ${C.border}`,color:C.muted,borderRadius:20,padding:"4px 12px",fontSize:11,cursor:"pointer"}}>{ex}</button>))}
        </div>
      </Card>

      {error&&<ErrMsg message={error}/>}
      {loading&&<Spinner/>}

      {result&&(
        <div style={{display:"flex",flexDirection:"column",gap:16}}>
          <Card>
            <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:10}}>Result — {fmt.num(result.rows)} rows</div>
            {result.data?.length>0?(
              <div style={{overflowX:"auto"}}>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                  <thead>
                    <tr style={{borderBottom:`1px solid ${C.border}`}}>
                      {Object.keys(result.data[0]).map(col=>(
                        <th key={col} style={{padding:"8px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:11,textTransform:"uppercase",letterSpacing:"0.06em",whiteSpace:"nowrap"}}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.data.map((row,i)=>(
                      <tr key={i} style={{borderBottom:`1px solid rgba(255,255,255,0.04)`}}>
                        {Object.values(row).map((val,j)=>(
                          <td key={j} style={{padding:"8px 12px",color:C.text,fontFamily:typeof val==="number"?"monospace":"inherit"}}>
                            {typeof val==="number"?(+val).toLocaleString(undefined,{maximumFractionDigits:2}):String(val??"—")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ):<div style={{color:C.muted,fontSize:13,padding:"20px 0"}}>No results returned.</div>}
          </Card>
          <Card>
            <div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:8}}>SQL Generated</div>
            <pre style={{background:"rgba(0,0,0,0.3)",border:`1px solid ${C.border}`,borderRadius:6,padding:12,fontSize:11,color:"#94a3b8",overflow:"auto",whiteSpace:"pre-wrap",margin:0,fontFamily:"monospace"}}>{result.sql}</pre>
          </Card>
        </div>
      )}
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
          <div key={run.runID} onClick={()=>onSelect(run)} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"18px 22px",cursor:"pointer",transition:"all 0.15s",display:"grid",gridTemplateColumns:"1fr auto",gap:16,alignItems:"center"}} onMouseEnter={e=>e.currentTarget.style.background="rgba(255,255,255,0.06)"} onMouseLeave={e=>e.currentTarget.style.background=C.surface}>
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
function AuditView({runSummary,onDecision,onBack}) {
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
      <button onClick={onBack} style={{background:"rgba(255,255,255,0.08)",border:"none",color:"#94a3b8",borderRadius:7,padding:"10px 24px",cursor:"pointer",fontSize:13,fontWeight:600,marginTop:24}}>← Back to Queue</button>
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
          <div style={{background:"rgba(255,255,255,0.02)",border:`1px solid ${C.border}`,borderTop:"none",borderRadius:"0 0 10px 10px",padding:20,overflowY:"auto",maxHeight:560}}>
            {tab==="report"&&(decision==="edited_and_approved"?<textarea value={editedNarrative} onChange={e=>setEditedNarrative(e.target.value)} style={{width:"100%",minHeight:480,background:"rgba(255,255,255,0.04)",border:`1px solid rgba(96,165,250,0.3)`,borderRadius:8,padding:16,color:C.text,fontSize:13,lineHeight:1.7,fontFamily:"monospace",resize:"vertical",boxSizing:"border-box"}}/>:<div style={{fontSize:13,lineHeight:1.8,color:"#cbd5e1",whiteSpace:"pre-wrap",fontFamily:"'Georgia',serif"}}>{run?.reportNarrative||"No narrative."}</div>)}
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
                      {r.expectedValue!=null&&<div style={{fontSize:11,color:C.muted,marginTop:2}}>Expected: <span style={{color:"#94a3b8"}}>{(+r.expectedValue).toLocaleString()}</span> · Reported: <span style={{color:"#94a3b8"}}>{(+r.reportedValue).toLocaleString()}</span>{r.deviationPct!=null&&<> · Dev: <span style={{color:r.deviationPct>10?C.red:C.green}}>{(+r.deviationPct).toFixed(1)}%</span></>}</div>}
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
                  <div><div style={{fontSize:13,fontWeight:600,color:C.amber}}>Decision: {(run?.policyOutcome?.decision||"").toUpperCase().replace(/_/g," ")}</div><div style={{fontSize:12,color:"#94a3b8",marginTop:2}}>{run?.policyOutcome?.rules_passed}/{run?.policyOutcome?.rules_evaluated} rules passed</div></div>
                </div>
                {policyRules.map((r,i)=>(
                  <div key={i} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",background:C.surface,border:`1px solid ${C.border}`,borderRadius:6}}>
                    <span style={{color:r.passed?C.green:C.amber}}>{r.passed?"✓":"✗"}</span>
                    <div style={{flex:1}}><span style={{fontSize:12,fontWeight:600,color:C.text,fontFamily:"monospace"}}>{r.rule}</span>{!r.passed&&r.message&&<div style={{fontSize:11,color:"#94a3b8",marginTop:2}}>{r.message}</div>}</div>
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
                {Object.entries(run?.queries||{}).map(([t,sql])=>(<div key={t}><div style={{fontSize:11,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:6}}>{t}</div><pre style={{background:"rgba(0,0,0,0.3)",border:`1px solid ${C.border}`,borderRadius:6,padding:12,fontSize:11,color:"#94a3b8",overflow:"auto",whiteSpace:"pre-wrap",margin:0,fontFamily:"monospace"}}>{typeof sql==="string"?sql:JSON.stringify(sql,null,2)}</pre></div>))}
                {(run?.flags||[]).map((f,i)=><div key={i} style={{fontSize:12,color:C.amber,padding:"6px 0",borderBottom:`1px solid rgba(255,255,255,0.04)`}}>⚠ {f}</div>)}
              </div>
            )}
          </div>
        </div>

        <div style={{display:"flex",flexDirection:"column",gap:12}}>
          <div style={{fontSize:13,fontWeight:600,color:"#94a3b8",textTransform:"uppercase",letterSpacing:"0.06em"}}>Decision</div>
          {[{id:"approved",label:"Approve",desc:"Publish as generated",color:C.green},{id:"edited_and_approved",label:"Edit & Approve",desc:"Modify then publish",color:C.blue},{id:"rejected",label:"Reject",desc:"Send back — reason required",color:C.red}].map(opt=>(
            <div key={opt.id} onClick={()=>setDecision(opt.id)} style={{padding:"14px 16px",border:`1px solid ${decision===opt.id?opt.color:C.border}`,borderRadius:8,cursor:"pointer",background:decision===opt.id?`${opt.color}18`:"rgba(255,255,255,0.02)",transition:"all 0.15s"}}>
              <div style={{fontSize:13,fontWeight:600,color:decision===opt.id?opt.color:C.text}}>{opt.label}</div>
              <div style={{fontSize:11,color:C.muted,marginTop:2}}>{opt.desc}</div>
            </div>
          ))}

          {/* Share with supplier toggle — only for supplier reports */}
          {run?.supplierID&&decision&&decision!=="rejected"&&(
            <div style={{padding:"12px 14px",background:"rgba(255,255,255,0.03)",border:`1px solid ${C.border}`,borderRadius:8}}>
              <label style={{display:"flex",alignItems:"center",gap:10,cursor:"pointer"}}>
                <input type="checkbox" checked={shareWithSupplier} onChange={e=>setShareWithSupplier(e.target.checked)} style={{width:16,height:16,accentColor:C.teal}} />
                <div>
                  <div style={{fontSize:13,fontWeight:600,color:shareWithSupplier?C.teal:C.text}}>Share with supplier</div>
                  <div style={{fontSize:11,color:C.muted,marginTop:2}}>Appears in /supplier/{run.supplierID} view</div>
                </div>
              </label>
            </div>
          )}

          <div><label style={{fontSize:12,color:"#94a3b8",display:"block",marginBottom:6}}>Reviewer</label><input value={reviewer} onChange={e=>setReviewer(e.target.value)} style={{width:"100%",background:"rgba(255,255,255,0.04)",border:`1px solid ${C.border}`,borderRadius:6,padding:"8px 10px",color:C.text,fontSize:12,fontFamily:"inherit",boxSizing:"border-box"}}/></div>
          {(decision==="rejected"||decision==="edited_and_approved")&&(
            <div><label style={{fontSize:12,color:"#94a3b8",display:"block",marginBottom:6}}>{decision==="rejected"?"Reason *":"Notes"}</label><textarea value={reason} onChange={e=>setReason(e.target.value)} rows={3} style={{width:"100%",background:"rgba(255,255,255,0.04)",border:`1px solid ${C.border}`,borderRadius:6,padding:10,color:C.text,fontSize:12,resize:"vertical",fontFamily:"inherit",boxSizing:"border-box"}}/></div>
          )}
          <button onClick={handleSubmit} disabled={submitting||!decision||(decision==="rejected"&&!reason.trim())} style={{padding:"10px 18px",borderRadius:7,border:`1px solid ${decision==="approved"?C.green:decision==="edited_and_approved"?C.blue:decision==="rejected"?C.red:C.border}`,fontSize:13,fontWeight:600,cursor:"pointer",background:decision==="approved"?"rgba(34,197,94,0.2)":decision==="edited_and_approved"?"rgba(96,165,250,0.2)":decision==="rejected"?"rgba(239,68,68,0.2)":"rgba(255,255,255,0.06)",color:decision==="approved"?C.green:decision==="edited_and_approved"?C.blue:decision==="rejected"?C.red:C.muted,opacity:(submitting||!decision||(decision==="rejected"&&!reason.trim()))?0.4:1,marginTop:8}}>
            {submitting?"Recording...":"Confirm Decision"}
          </button>
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

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [view,setView]         = useState("queue");
  const [selected,setSelected] = useState(null);
  const [queueCount,setQueueCount] = useState(null);
  const [decisions,setDecisions]   = useState({});
  const [dashTab,setDashTab]   = useState("business");

  // Supplier-facing portal detection
  const supplierMatch = window.location.pathname.match(/^\/supplier\/([A-Z0-9]+)$/i);
  if (supplierMatch) {
    return (
      <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
        <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",height:52,background:"rgba(255,255,255,0.02)"}}>
          <div style={{fontSize:14,fontWeight:700,color:C.text,letterSpacing:"-0.02em"}}>Supplier <span style={{color:C.teal}}>Performance</span></div>
        </div>
        <div style={{padding:"28px",maxWidth:1100,margin:"0 auto"}}>
          <SupplierDashboard initialSupplier={supplierMatch[1]} supplierFacing={true}/>
        </div>
      </div>
    );
  }

  useEffect(()=>{ apiFetch("/api/queue").then(d=>setQueueCount(d.total||0)).catch(()=>setQueueCount(null)); },[decisions,view]);

  const nav=[{id:"queue",label:"Queue",badge:queueCount},{id:"dashboards",label:"Dashboards"},{id:"new_report",label:"New Report"},{id:"ask",label:"Ask"},{id:"observability",label:"Observability"}];

  return (
    <div style={{minHeight:"100vh",background:C.bg,color:C.text,fontFamily:"'DM Sans','Helvetica Neue',sans-serif"}}>
      <div style={{borderBottom:`1px solid ${C.border}`,padding:"0 28px",display:"flex",alignItems:"center",gap:0,height:52,background:"rgba(255,255,255,0.02)",position:"sticky",top:0,zIndex:100}}>
        <div style={{fontSize:14,fontWeight:700,color:C.text,letterSpacing:"-0.02em",marginRight:32}}>BI Agent <span style={{color:C.blue}}>Control</span></div>
        {nav.map(item=>(
          <button key={item.id} onClick={()=>{ setView(item.id); setSelected(null); }} style={{background:"none",border:"none",borderBottom:view===item.id?`2px solid ${C.blue}`:"2px solid transparent",color:view===item.id?C.blue:C.muted,padding:"0 16px",height:"100%",cursor:"pointer",fontSize:13,fontWeight:view===item.id?600:400,display:"flex",alignItems:"center",gap:8,transition:"all 0.15s"}}>
            {item.label}
            {item.badge!=null&&<span style={{background:C.blue,color:"#fff",borderRadius:"10px",padding:"1px 7px",fontSize:11,fontWeight:700}}>{item.badge}</span>}
          </button>
        ))}
        <div style={{marginLeft:"auto",fontSize:12,color:"#334155"}}>{new Date().toLocaleDateString("en-GB",{weekday:"short",day:"2-digit",month:"short",year:"numeric"})}</div>
      </div>

      <div style={{padding:"28px",maxWidth:1300,margin:"0 auto"}}>
        {view==="queue"&&!selected&&<RunQueue onSelect={run=>{ setSelected(run); setView("audit"); }}/>}
        {view==="audit"&&selected&&<AuditView runSummary={selected} onDecision={dec=>{ setDecisions(p=>({...p,[dec.runID]:dec})); }} onBack={()=>{ setSelected(null); setView("queue"); }}/>}

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
