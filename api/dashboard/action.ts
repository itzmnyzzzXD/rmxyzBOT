export default async function handler(req:any,res:any){
  if(req.method!=='POST') return res.status(405).json({ok:false,error:'Method not allowed'})
  // Mutating dashboard routes intentionally fail closed until Discord OAuth/session middleware is configured.
  res.status(401).json({ok:false,error:'Authentication is required for dashboard actions'})
}
