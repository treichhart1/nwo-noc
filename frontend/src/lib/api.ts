const BASE = "/api"
export const getToken=()=>localStorage.getItem("nwo_token")
export const setToken=(v:string|null)=>v?localStorage.setItem("nwo_token",v):localStorage.removeItem("nwo_token")
export async function request(path:string, init:RequestInit={}){
  const headers=new Headers(init.headers); if(getToken()) headers.set("Authorization",`Bearer ${getToken()}`)
  const r=await fetch(BASE+path,{...init,headers}); const data=await r.json().catch(()=>({}))
  if(r.status===401){setToken(null);location.href="/"}
  if(!r.ok) throw new Error(data.detail||`Request failed (${r.status})`)
  return data
}
export async function login(username:string,password:string){
  const body=new URLSearchParams({username,password})
  const r=await fetch(BASE+"/auth/login",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body})
  const data=await r.json().catch(()=>({})); if(!r.ok) throw new Error(data.detail||"Login failed"); return data
}
export async function logout(){
  const token=getToken()
  if(token){const r=await fetch(BASE+'/auth/logout',{method:'POST',headers:{Authorization:`Bearer ${token}`}});if(!r.ok&&r.status!==401)throw new Error('Server logout could not revoke this session. Retry when service recovers.')}
  setToken(null)
}
