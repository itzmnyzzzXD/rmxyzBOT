export default async function handler(req:any,res:any){
  if(req.method!=='GET')return res.status(405).json({error:'Method not allowed'})
  // Discord OAuth should replace this demo adapter once DISCORD_CLIENT_ID/SECRET are configured.
  res.status(200).json([
    {id:'100001',name:'RM Community',icon:'RM',members:12480,online:true,manageable:true},
    {id:'100002',name:'Night Shift',icon:'NS',members:5810,online:true,manageable:true},
    {id:'100003',name:'Gaming Hub',icon:'GH',members:2220,online:false,manageable:false}
  ])
}
