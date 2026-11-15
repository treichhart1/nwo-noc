import React,{useEffect,useState} from 'react'
import {readFieldContext} from './lib/field-context'
export default function UnifiedFieldContext({workId}:{workId:number}){
 const[items,setItems]=useState<any[]>([]),[error,setError]=useState(''),[retry,setRetry]=useState(0)
 useEffect(()=>{const controller=new AbortController();setItems([]);setError('');readFieldContext('/field/work-orders/'+workId+'/unified-context',controller.signal).then(d=>{if(!controller.signal.aborted)setItems(d.items)}).catch(()=>{if(!controller.signal.aborted)setError('Associated network components unavailable; your inspection draft is unchanged. Retry when connected.')});return()=>controller.abort()},[workId,retry])
 if(!items.length&&!error)return null
 return <section aria-label="Assigned network components"><h3>Documented network components</h3>{error&&<><p role="status">{error}</p><button onClick={()=>setRetry(retry+1)}>Retry associated network context</button></>}{items.map(i=><p key={i.association_id}>Component #{i.node_id??'Not specified'} · connection #{i.edge_id??'Not specified'}. Related asset/QR documents retain existing permissions. No equipment configuration changes.</p>)}</section>
}
