import {useEffect,useState} from "react"
import {request} from "./lib/api"

type SiteRF={site_id:number;site_name:string;devices:number;fresh:number;stale:number;missing:number;future:number;degraded:number}
type Response={sites:SiteRF[];processed_devices:number;truncated:boolean;max_devices:number}

export default function RFSiteHealth(){
  const [data,setData]=useState<Response|null>(null)
  const [error,setError]=useState("")
  useEffect(()=>{
    let active=true
    request("/rf/site-health").then((value:Response)=>{if(active)setData(value)})
      .catch(()=>{if(active)setError("Site RF health unavailable")})
    return()=>{active=false}
  },[])
  return <section className="panel">
    <div className="panelHead"><div><h2>RF by tower / POP</h2>
      <p className="muted">Informational signal quality; not an outage indicator.</p></div></div>
    {error?<p role="status">{error}</p>:!data?<p>Loading site RF health…</p>:
      <div>
        {data.truncated&&<p role="status">Partial results: first {data.max_devices} assigned UISP devices only.</p>}
        {data.sites.length===0?<p>No assigned UISP radio data.</p>:
          <div className="tableWrap"><table><thead><tr><th>Site</th><th>Devices</th><th>Fresh</th><th>Stale</th><th>Missing</th><th>Future</th><th>Degraded</th></tr></thead>
          <tbody>{data.sites.map(s=><tr key={s.site_id}><td>{s.site_name}</td><td>{s.devices}</td><td>{s.fresh}</td><td>{s.stale}</td><td>{s.missing}</td><td>{s.future}</td><td>{s.degraded}</td></tr>)}</tbody></table></div>}
      </div>}
  </section>
}
