import Experience from "@/components/experience";
export default async function Page({params}:{params:Promise<{playerId:string}>}){const {playerId}=await params;return <Experience playerId={playerId} initialMode="player"/>;}
