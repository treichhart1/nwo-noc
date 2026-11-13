import {useEffect,useState} from 'react'
import {request} from './lib/api'
type Sample={profile:string;vendor:string;source_kind:string;observed_at:string;received_at:string;timestamp_basis:string;radio_status:string|null;gps_sync:string|null;modulation:string|null;measurements:Record<string,number|null>;counters:Record<string,number|null>;original_units:Record<string,string>;original_values:Record<string,number|string>;unavailable:Record<string,string>}
type Report={samples:Sample[];invalid_samples:number;last_attempt:{at:string;result:string}|null;diagnostics:{state:string;reasons:string[];freshness:{status:string};counter_status:{state:string;deltas:Record<string,number>}|null}}
const labels:Record<string,string>={rssi_dbm:'Local RSSI (dBm)',remote_rssi_dbm:'Remote RSSI (dBm)',snr_db:'SNR (dB)',noise_floor_dbm:'Noise floor (dBm)',frequency_mhz:'Frequency (MHz)',channel_width_mhz:'Channel width (MHz)',tx_rate_mbps:'TX PHY rate (Mbps)',rx_rate_mbps:'RX PHY rate (Mbps)',link_capacity_mbps:'Estimated capacity (Mbps)',tx_throughput_mbps:'TX throughput (Mbps)',rx_throughput_mbps:'RX throughput (Mbps)',ccq_percent:'Link quality (%)',mcs_index:'MCS index',subscriber_count:'Subscriber count',uptime_seconds:'Uptime (seconds)',temperature_c:'Temperature (°C)'}
function Trend({samples,field}:{samples:Sample[];field:string}){
 const values=samples.map(s=>s.measurements[field]).filter((v):v is number=>v!=null)
 if(values.length<2)return <p>{labels[field]}: Insufficient history</p>
 const low=Math.min(...values),high=Math.max(...values),first=Date.parse(samples[0].observed_at),last=Date.parse(samples[samples.length-1].observed_at)
 const lines:string[]=[];let segment:string[]=[]
 samples.forEach(s=>{const v=s.measurements[field];if(v==null){if(segment.length)lines.push(segment.join(' '));segment=[];return}segment.push(`${10+380*(Date.parse(s.observed_at)-first)/Math.max(last-first,1)},${85-70*(v-low)/Math.max(high-low,1)}`)})
 if(segment.length)lines.push(segment.join(' '))
 return <figure><figcaption>{labels[field]} · {low} to {high}</figcaption><svg role="img" aria-label={`${labels[field]} history`} viewBox="0 0 400 100" style={{width:'100%',maxWidth:600}}>{lines.map((points,i)=><polyline key={i} points={points} stroke="currentColor" fill="none" strokeWidth="2"/>)}</svg><small>{new Date(first).toLocaleString()} — {new Date(last).toLocaleString()} · gaps retain unavailable readings</small></figure>
}
export default function RadioTelemetry({deviceId}:{deviceId:string}){
 const [report,setReport]=useState<Report|null>(null),[error,setError]=useState('')
 useEffect(()=>{let active=true;setReport(null);setError('');if(deviceId)request(`/devices/${encodeURIComponent(deviceId)}/rf-radio-history?limit=288`).then(data=>{if(active)setReport(data)}).catch(()=>{if(active)setError('Radio history unavailable; inspect application and collection health.')});return()=>{active=false}},[deviceId])
 const latest=report?.samples[report.samples.length-1]
 return <section className="panel" aria-label="Ubiquiti and Cambium radio telemetry"><h3>Ubiquiti and Cambium radio telemetry</h3><p className="muted">SNMPv3 GET-only, explicit firmware/MIB mappings. Collectors are disabled by default and are not scheduled automatically. Synthetic evidence does not establish firmware compatibility.</p>{error&&<p role="alert">{error}</p>}{deviceId&&report&&!latest&&<p>No vendor radio history. Unsupported measurements are never zero-filled.</p>}{report?.last_attempt&&<p>Last poll attempt: {new Date(report.last_attempt.at).toLocaleString()} · {report.last_attempt.result}</p>}{latest&&report&&<>
 <p>Radio health: <strong>{report.diagnostics.state}</strong> · Freshness: {report.diagnostics.freshness.status} · Reported connection: {latest.radio_status??'Not available'}</p>
 {report.diagnostics.reasons.length>0&&<p role="alert">Degradation warnings: {report.diagnostics.reasons.join(', ')}</p>}
 {report.invalid_samples>0&&<p role="alert">Invalid stored samples: {report.invalid_samples}. Current health is unknown.</p>}
 <p>Source: {latest.vendor} · {latest.profile} · {latest.source_kind}</p><p>Last successful telemetry collection: {new Date(latest.received_at).toLocaleString()} · Measurement time: {new Date(latest.observed_at).toLocaleString()} ({latest.timestamp_basis})</p>
 <div className="cards">{Object.entries(labels).map(([key,label])=><article key={key}><b>{label}</b><div>{latest.measurements[key]??'Not available'}</div>{latest.measurements[key]==null&&<small>{latest.unavailable[key]||'unknown'}</small>}</article>)}</div>
 <p>GPS synchronization: {latest.gps_sync??`Not available (${latest.unavailable.gps_sync})`} · Modulation: {latest.modulation??`Not available (${latest.unavailable.modulation})`}</p>
 <p>Counter continuity: {report.diagnostics.counter_status?.state??'No baseline'}. Resets/restarts never produce negative deltas or fabricated rates.</p>
 <div className="cards">{Object.entries(latest.counters).map(([key,value])=><article key={key}><b>{key}</b><div>{value??`Not available (${latest.unavailable[key]})`}</div><small>Delta: {report.diagnostics.counter_status?.deltas[key]??'Not available'}</small></article>)}</div>
 {['rssi_dbm','snr_db','noise_floor_dbm','link_capacity_mbps'].map(field=><Trend key={field} samples={report.samples} field={field}/>)}
 <details><summary>Accepted original vendor values</summary><p>Only allowlisted numeric values and enumerations are retained; units are defined by the approved mapping. No raw response, credentials or subscriber identities.</p><pre>{JSON.stringify({values:latest.original_values,units:latest.original_units},null,2)}</pre></details>
 </>}</section>
}
