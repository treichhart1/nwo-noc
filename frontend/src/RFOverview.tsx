import {useEffect,useState} from "react"
import {request} from "./lib/api"

type RFHealth={
  enabled:boolean;uisp_status:string;rf_devices_fresh:number;
  rf_devices_stale:number;rf_devices_missing:number;rf_devices_future:number;
}

export default function RFOverview({openDiagnostics}:{openDiagnostics:()=>void}){
  const [health,setHealth]=useState<RFHealth|null>(null)
  const [error,setError]=useState("")
  useEffect(()=>{
    let active=true
    const refresh=()=>request("/rf/collection-health")
      .then((value:RFHealth)=>{if(active){setHealth(value);setError("")}})
      .catch(()=>{if(active){setHealth(null);setError("RF health unavailable")}})
    void refresh()
    const interval=setInterval(()=>{void refresh()},60000)
    return()=>{active=false;clearInterval(interval)}
  },[])
  return <section className="panel">
    <div className="panelHead">
      <div><h2>RF monitoring</h2><p className="muted">Read-only UISP RF telemetry; separate from reachability alerts.</p></div>
      <button className="linkBtn" onClick={openDiagnostics}>RF diagnostics</button>
    </div>
    {error?<p role="status">{error}. Network reachability remains independent.</p>:
      !health?<p>Loading RF collection health…</p>:
      <div>
        <p>Collection: <strong>{health.enabled?"Enabled":"Disabled"}</strong> · UISP: {health.uisp_status}</p>
        <p>Fresh: {health.rf_devices_fresh} · Stale: {health.rf_devices_stale} · Missing: {health.rf_devices_missing} · Future timestamps: {health.rf_devices_future}</p>
        {!health.enabled&&<p className="muted">RF collection is disabled; missing samples do not indicate an outage.</p>}
      </div>}
  </section>
}
