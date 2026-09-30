export async function api<T>(path:string,body?:unknown):Promise<T> {
  const response=await fetch('/api/v1/demo'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const data=await response.json()
  if(!response.ok)throw new Error(`${data.detail?.code??response.status}：${data.detail?.message??'请求失败'}`)
  return data as T
}
export async function dynamicApi<T>(path:string,body?:unknown):Promise<T> {
  const response=await fetch('/api/v1/dynamic'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const data=await response.json()
  if(!response.ok)throw new Error(`${data.detail?.code??response.status}：${data.detail?.message??'请求失败'}`)
  return data as T
}
export const pause=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms))
