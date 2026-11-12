import {useEffect,useState} from "react"
import {request} from "./lib/api"

type RFMetric={baseline:number,recent:number,change:number,declining:boolean,observations:number}
type RFReport={
  device_id:number;device_name:string;rf_sample_count:number;lookback_hours:number;
  freshness:{status:"fresh"|"stale"|"missing"|"future";last_sampled_at:string|null;age_seconds:number|null;stale_after_seconds:number};
  degradation:{degraded:boolean;reasons:string[];consecutive_counts:Record<string,number>};
  trends:{status:string;declining_metrics:string[];metrics:Record<string,RFMetric>}
}
type DeviceOption={id:number;name:string;site_name?:string}

const labels:Record<string,string>={rssi_dbm:"RSSI (dBm)",snr_db:"SNR (dB)",ccq_percent:"CCQ (%)"}
const warnings:Record<string,string>={weak_rssi:"Weak RSSI",low_snr:"Low SNR",low_ccq:"Low CCQ"}

export default function RFDiagnostics({devices}:{devices:DeviceOption[]}){
  const [deviceId,setDeviceId]=useState("")
  const [hours,setHours]=useState(24)
  const [report,setReport]=useState<RFReport|null>(null)
  const [loading,setLoading]=useState(false)
  const [error,setError]=useState("")
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
  return <section className="panel">
    <div className="panelHead"><div><h2>RF diagnostics</h2><p className="muted">Read-only RF health and historical trends. No automatic radio changes or alerts.</p></div></div>
    <div className="form2">
      <label>Device<select value={deviceId} onChange={e=>setDeviceId(e.target.value)}>
        <option value="">Select a device…</option>
        {devices.map(d=><option key={d.id} value={d.id}>{d.name}{d.site_name?` — ${d.site_name}`:""}</option>)}
      </select></label>
      <label>History<select value={hours} onChange={e=>setHours(Number(e.target.value))}>
        <option value={6}>6 hours</option><option value={24}>24 hours</option><option value={72}>3 days</option><option value={168}>7 days</option>
      </select></label>
    </div>
    {loading&&<p role="status">Loading RF diagnostics…</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {!deviceId&&<p className="muted">Choose a radio to inspect its RF telemetry.</p>}
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
  </section>
}
