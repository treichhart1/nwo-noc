import {useEffect,useState} from "react"
import {request} from "./lib/api"

type RFReport={
  rf_sample_count:number;
  freshness:{status:string;last_sampled_at:string|null};
  degradation:{degraded:boolean;reasons:string[]};
  trends:{status:string;declining_metrics:string[]};
}

export default function DeviceRFHealth({deviceId,reachability}:{deviceId:number;reachability:string}){
  const [report,setReport]=useState<RFReport|null>(null)
  const [error,setError]=useState("")
  const [loading,setLoading]=useState(true)
  useEffect(()=>{
    let active=true
    setLoading(true);setReport(null);setError("")
    request(`/devices/${encodeURIComponent(deviceId)}/rf-diagnostics?lookback_hours=24&max_samples=288`)
      .then((data:RFReport)=>{if(active)setReport(data)})
      .catch(()=>{if(active)setError("RF diagnostics unavailable")})
      .finally(()=>{if(active)setLoading(false)})
    return()=>{active=false}
  },[deviceId])
  return <div className="monitorBox">
    <h3>RF and reachability</h3>
    <p>Device reachability: <strong>{reachability||"unknown"}</strong></p>
    {loading?<p>Loading RF diagnostics…</p>:
      error?<p role="status">{error}. Reachability is reported independently.</p>:
      report?<div>
        <p>RF freshness: <strong>{report.freshness.status}</strong> · Samples: {report.rf_sample_count}</p>
        <p>RF degradation: <strong>{report.degradation.degraded?"Detected":"Not detected"}</strong></p>
        {report.degradation.reasons.length>0&&<p>Indicators: {report.degradation.reasons.join(", ")}</p>}
        <p>RF trend: {report.trends.status}</p>
        {report.trends.declining_metrics.length>0&&<p>Declining metrics: {report.trends.declining_metrics.join(", ")}</p>}
        <p className="muted">RF conditions do not change device outage status or create alerts.</p>
      </div>:<p>No RF diagnostic data.</p>}
  </div>
}
