import Experience from "@/components/experience";
export default async function Page({params}:{params:Promise<{matchId:string}>}){const {matchId}=await params;return <Experience matchId={matchId} />;}
