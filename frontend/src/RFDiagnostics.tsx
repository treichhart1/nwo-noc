import {useEffect,useState} from "react"
import {request} from "./lib/api"
import RFFoundation from "./RFFoundation"
import RadioTelemetry from "./RadioTelemetry"
import RFSiteHealth from "./RFSiteHealth"

type RFMetric={baseline:number,recent:number,change:number,declining:boolean,observations:number}
type RFReport={
  device_id:number;device_name:string;rf_sample_count:number;lookback_hours:number;
  freshness:{status:"fresh"|"stale"|"missing"|"future";last_sampled_at:string|null;age_seconds:number|null;stale_after_seconds:number};
  degradation:{degraded:boolean;reasons:string[];consecutive_counts:Record<string,number>};
  trends:{status:string;declining_metrics:string[];metrics:Record<string,RFMetric>}
}
type CollectionHealth={enabled:boolean;uisp_status:string;last_uisp_sync_at:string|null;enabled_uisp_devices:number;rf_devices_fresh:number;rf_devices_stale:number;rf_devices_missing:number;rf_devices_future:number;last_batch_note:string}
type Reliability={window_days:number;total_runs:number;successful_runs:number;failed_runs:number;success_rate_percent:number|null}
type CollectionRun={id:number;started_at:string;status:string;received:number;staged:number;skipped:number}
type DeviceOption={id:number;name:string;site_name?:string}

const labels:Record<string,string>={rssi_dbm:"RSSI (dBm)",snr_db:"SNR (dB)",ccq_percent:"CCQ (%)"}
const warnings:Record<string,string>={weak_rssi:"Weak RSSI",low_snr:"Low SNR",low_ccq:"Low CCQ"}

export default function RFDiagnostics({devices}:{devices:DeviceOption[]}){
  const [deviceId,setDeviceId]=useState("")
  const [hours,setHours]=useState(24)
  const [report,setReport]=useState<RFReport|null>(null)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState("")
  const [health,setHealth]=useState<CollectionHealth|null>(null)
  const [healthError,setHealthError]=useState("")
  const [history,setHistory]=useState<CollectionRun[]>([])
  const [historyError,setHistoryError]=useState("")
  const [reliability,setReliability]=useState<Reliability|null>(null)
  useEffect(()=>{let active=true;request("/rf/collection-reliability?days=7").then((data:Reliability)=>{if(active)setReliability(data)}).catch(()=>{});return()=>{active=false}},[])
  useEffect(()=>{let active=true;request("/rf/collection-history?limit=20").then((runs:CollectionRun[])=>{if(active)setHistory(runs)}).catch((e:Error)=>{if(active)setHistoryError(e.message)});return()=>{active=false}},[])
  useEffect(()=>{let active=true;request("/rf/collection-health").then((data:CollectionHealth)=>{if(active)setHealth(data)}).catch((e:Error)=>{if(active)setHealthError(e.message)});return()=>{active=false}},[])
  useEffect(()=>{
    if(!deviceId){setReport(null);setError("");return}
    let cancelled=false
    setLoading(true);setError("");setReport(null)
    request(`/devices/${encodeURIComponent(deviceId)}/rf-diagnostics?lookback_hours=${hours}&max_samples=288`)
      .then((data:RFReport)=>{if(!cancelled)setReport(data)})
      .catch((err:Error)=>{if(!cancelled)setError(err.message)})
      .finally(()=>{if(!cancelled)setLoading(false)})
    return()=>{cancelled=true}
  },[deviceId,hours])
  return <><RFSiteHealth/><section className="panel">
    <div className="panelHead"><div><h2>RF diagnostics</h2><p className="muted">Read-only RF health and historical trends. No automatic radio changes or alerts.</p></div></div>
    {health&&<div className="panel"><h3>Collection health</h3><p>RF collection: <strong>{health.enabled?"Enabled":"Disabled"}</strong> · UISP: {health.uisp_status}</p><p>Fresh: {health.rf_devices_fresh} · Stale: {health.rf_devices_stale} · Missing: {health.rf_devices_missing} · Future timestamps: {health.rf_devices_future}</p><p className="muted">Last UISP sync: {health.last_uisp_sync_at?new Date(health.last_uisp_sync_at).toLocaleString():"Not available"}</p><p className="muted">{health.last_batch_note}</p></div>}
    {healthError&&<p className="error" role="alert">Collection health unavailable: {healthError}</p>}
    {reliability&&<div className="panel"><h3>Collection reliability · 7 days</h3><p>{reliability.total_runs} runs · {reliability.successful_runs} succeeded · {reliability.failed_runs} failed · Success rate: {reliability.success_rate_percent===null?"No data":`${reliability.success_rate_percent}%`}</p></div>}
    <div className="panel"><h3>Recent RF collection runs</h3>
      {historyError&&<p className="error" role="alert">Collection history unavailable: {historyError}</p>}
      {!historyError&&history.length===0&&<p className="muted">No RF collection runs recorded yet.</p>}
      {history.map(run=><p key={run.id}><strong>{run.status}</strong> · {new Date(run.started_at).toLocaleString()} · Received {run.received} · Staged {run.staged} · Skipped {run.skipped}</p>)}
    </div>
    <div className="form2">
      <label>Device<select aria-label="Device" value={deviceId} onChange={e=>setDeviceId(e.target.value)}>
        <option value="">Select a device…</option>
        {devices.map(d=><option key={d.id} value={d.id}>{d.name}{d.site_name?` — ${d.site_name}`:""}</option>)}
      </select></label>
      <label>History<select aria-label="History" value={hours} onChange={e=>setHours(Number(e.target.value))}>
        <option value={6}>6 hours</option><option value={24}>24 hours</option><option value={72}>3 days</option><option value={168}>7 days</option>
      </select></label>
    </div>
    {loading&&<p role="status">Loading RF diagnostics…</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {!deviceId&&<p className="muted">Choose a radio to inspect its RF telemetry.</p>}
    <RFFoundation deviceId={deviceId}/>
    <RadioTelemetry deviceId={deviceId}/>
    {report&&<div>
      <h3>{report.device_name}</h3>
      <p className="muted">{report.rf_sample_count} RF samples in the selected window</p>
      <p role="status"><strong>Telemetry freshness:</strong> {report.freshness.status.toUpperCase()}{report.freshness.last_sampled_at?` · Last sample: ${new Date(report.freshness.last_sampled_at).toLocaleString()}`:" · No RF samples available"}</p>
      {report.freshness.status!=="fresh"&&<p className="error" role="alert">RF readings are {report.freshness.status}; do not interpret the signal condition as current health.</p>}
      {report.rf_sample_count===0?<p>No RF telemetry is available for this device yet.</p>:<>
        <p><strong>Last recorded signal condition (not live):</strong> {report.degradation.degraded?"Sustained threshold degradation detected":"No sustained threshold degradation detected"}</p>
        {report.degradation.reasons.length>0&&<p role="status">Warnings: {report.degradation.reasons.map(x=>warnings[x]||x).join(", ")}</p>}
        <p><strong>Historical trend:</strong> {report.trends.status.replaceAll("_"," ")}</p>
        {report.trends.status==="insufficient_data"&&<p className="muted">More valid measurements and time coverage are needed for trend analysis.</p>}
        <div className="cards">{Object.entries(report.trends.metrics).map(([key,m])=><article key={key}>
          <div><b>{labels[key]||key}</b><span>Earlier: {m.baseline} · Recent: {m.recent}</span>
          <small>Change: {m.change>0?"+":""}{m.change} · {m.declining?"Declining":"Stable"}</small></div>
        </article>)}</div>
      </>}
    </div>}
  </section></>
}
