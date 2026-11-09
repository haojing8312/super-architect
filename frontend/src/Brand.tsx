export const BRAND = {name:'超级建筑师', tagline:'AI 建模 · 空间设计 · 方案汇报', version:'0.6.2'};
export function BrandMark({size=32}:{size?:number}) {
 return <svg width={size} height={size} viewBox="0 0 48 48" fill="none" role="img" aria-label="超级建筑师标志"><rect width="48" height="48" rx="13" fill="currentColor"/><path d="M12 35V17L24 10L36 17V35M12 22L24 15L36 22M20 35V26L28 21V35" stroke="var(--brand-mark-line,#f5f4ed)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/><path d="M11 36H37" stroke="var(--brand-mark-line,#f5f4ed)" strokeWidth="2.5" strokeLinecap="round"/></svg>;
}
