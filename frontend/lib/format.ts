export const clock=(t:number)=>`${Math.floor(t/60).toString().padStart(2,'0')}:${Math.floor(t%60).toString().padStart(2,'0')}`;
export const label=(s:string)=>s.replaceAll('_',' ').toLowerCase().replace(/^./,c=>c.toUpperCase());
export const number=(n:number|null|undefined)=>typeof n==='number'&&Number.isFinite(n)?new Intl.NumberFormat('en',{maximumFractionDigits:2}).format(n):'Unavailable';
