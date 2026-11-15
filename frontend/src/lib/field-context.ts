import {getToken} from './api'
/** Optional read-only panels must not discard a primary field session/draft.
 * Backend authorization is still mandatory; primary field actions own reauthentication.
 */
export async function readFieldContext(path:string,signal?:AbortSignal){
 const token=getToken();if(!token)throw new Error('Context authorization unavailable')
 if(!navigator.onLine)throw new Error('Context unavailable offline')
 const controller=new AbortController(),abort=()=>controller.abort()
 signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)controller.abort()
 const timer=setTimeout(abort,5000)
 try{const r=await fetch('/api'+path,{signal:controller.signal,headers:{Authorization:'Bearer '+token,Accept:'application/json'}});if(!r.ok)throw Object.assign(new Error('Context unavailable; check connection or primary session'),{status:r.status});return await r.json()}
 finally{clearTimeout(timer);signal?.removeEventListener('abort',abort)}
}
