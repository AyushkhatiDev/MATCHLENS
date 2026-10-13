import type {Metadata} from 'next';
import './globals.css';
import './motion.css';
import './clarity.css';
export const metadata:Metadata={title:'MatchLens · Inside the match',description:'Evidence-driven football intelligence. Synthetic local replay.'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
